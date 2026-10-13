#!/usr/bin/env bash
# BUILD: AUTODARTS-V2-MIGRATION-20261008-03
# Roadmap STEP 1: Migration v1 → v2, ttyd, autodarts-tui.service
# Idempotent. Installer: https://autodarts.sh --headless (immer)
set -euo pipefail

PORT_V2=3180
PORT_TTYD=7681
LOG="${LOG_FILE:-/var/log/autodarts_webpanel_update.log}"
STATE_DIR="/var/lib/autodarts"
STATE_FILE="${STATE_DIR}/autodarts-v2-migration-state.json"
TUI_SERVICE="/etc/systemd/system/autodarts-tui.service"

mkdir -p "${STATE_DIR}"

ts()  { date +"[%F %T]"; }
log() { echo "$(ts) [V2-MIGRATION] $*" | tee -a "${LOG}"; }

# ─── §2: Autodarts-User ermitteln ────────────────────────────────────────────

detect_ad_user() {
  # 1. AUTODARTS_USER env
  if [[ -n "${AUTODARTS_USER:-}" ]] && id "${AUTODARTS_USER}" >/dev/null 2>&1; then
    echo "${AUTODARTS_USER}"; return
  fi
  # 2. User= aus bestehendem Legacy-Service
  local su
  su="$(systemctl show autodarts.service -p User 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)"
  [[ -n "$su" ]] && id "$su" >/dev/null 2>&1 && { echo "$su"; return; }
  # 3. Besitzer von config.toml
  local cf co
  for cf in /home/*/.config/autodarts/config.toml; do
    [[ -f "$cf" ]] || continue
    co="$(stat -c '%U' "$cf" 2>/dev/null || true)"
    [[ -n "$co" ]] && id "$co" >/dev/null 2>&1 && { echo "$co"; return; }
  done
  # 4. peter
  id peter >/dev/null 2>&1 && { echo "peter"; return; }
  # 5. Erster regulärer User UID >= 1000
  getent passwd | awk -F: '$3>=1000 && $3<65534 {print $1; exit}'
}

AD_USER="$(detect_ad_user)"
if [[ -z "$AD_USER" ]]; then
  echo "$(ts) [V2-MIGRATION] FEHLER: Kein Autodarts-User gefunden"
  exit 1
fi
AD_UID="$(id -u "$AD_USER")"
AD_HOME="$(getent passwd "$AD_USER" | cut -d: -f6)"

log "Autodarts-User: $AD_USER (UID=$AD_UID, HOME=$AD_HOME)"

# Pfade die vom User abhängen
USER_SERVICE_DIR="${AD_HOME}/.config/systemd/user"
USER_SERVICE="${USER_SERVICE_DIR}/autodarts.service"
CONFIG_DIR="${AD_HOME}/.config/autodarts"
V2_BINARY=""  # wird via find_v2_binary() gesetzt

# ─── Basisfunktionen ─────────────────────────────────────────────────────────

run_as_user() {
  sudo -u "$AD_USER" \
    XDG_RUNTIME_DIR="/run/user/${AD_UID}" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${AD_UID}/bus" \
    HOME="${AD_HOME}" \
    "$@"
}

service_enabled() { run_as_user systemctl --user is-enabled  autodarts.service >/dev/null 2>&1; }
service_active()  { run_as_user systemctl --user is-active   autodarts.service >/dev/null 2>&1; }

# §11: running:false ist kein Fehler — gültiges JSON reicht
api_reachable() {
  local resp
  resp="$(curl -sSf --max-time 4 "http://127.0.0.1:${PORT_V2}/api/state" 2>/dev/null || true)"
  echo "$resp" | grep -q '"' && return 0
  curl -sSf --max-time 3 "http://127.0.0.1:${PORT_V2}/api/version" >/dev/null 2>&1
}

# ─── §3: Binary finden (3 Pfade + realpath) ──────────────────────────────────

find_v2_binary() {
  local candidates=(
    "${AD_HOME}/.local/share/autodarts/autodarts"
    "${AD_HOME}/.local/bin/autodarts"
    "${AD_HOME}/.local/opt/autodarts/autodarts"
  )
  local b real
  for b in "${candidates[@]}"; do
    [[ -x "$b" ]] || continue
    real="$(realpath "$b" 2>/dev/null || echo "$b")"
    [[ -x "$real" ]] && echo "$real" && return 0
  done
  return 1
}

classify_state() {
  # Liest globales V2_BINARY (muss vorher via find_v2_binary gesetzt sein)
  if [[ -z "${V2_BINARY}" ]]; then
    if systemctl is-active --quiet autodarts.service 2>/dev/null \
       || systemctl is-enabled --quiet autodarts.service 2>/dev/null; then
      echo "V1_ONLY"
    else
      echo "FRESH"
    fi
    return
  fi

  local ver major
  ver="$(run_as_user "${V2_BINARY}" --version 2>/dev/null || true)"
  major="$(echo "$ver" | grep -oE '[0-9]+' | head -n1 || true)"

  if [[ -z "$major" ]];           then echo "BROKEN_OR_UNKNOWN"; return; fi
  if [[ "$major" -gt 2 ]];        then echo "NEWER_THAN_V2";     return; fi
  if [[ "$major" -lt 2 ]];        then echo "V1_ONLY";           return; fi

  # major == 2
  if service_active && api_reachable; then
    echo "V2_HEALTHY"
  else
    echo "V2_SERVICE_BROKEN"
  fi
}

# ─── §4: Vollständiges Backup mit Metadaten ───────────────────────────────────

backup_before_migration() {
  local bd="${STATE_DIR}/config/backups/autodarts-v2-core-migration-$(date +%Y%m%d_%H%M%S)"
  mkdir -p "$bd"
  log "Backup: $bd"

  [[ -d "${CONFIG_DIR}" ]]                      && cp -a "${CONFIG_DIR}" "${bd}/config_autodarts"    || true
  cp /etc/systemd/system/autodarts.service         "${bd}/" 2>/dev/null || true
  cp /etc/systemd/system/autodartsupdater.service  "${bd}/" 2>/dev/null || true
  cp -r /etc/systemd/system/autodartsupdater.service.d "${bd}/" 2>/dev/null || true
  cp /usr/local/bin/autodarts-safe-updater.sh      "${bd}/" 2>/dev/null || true
  [[ -d "${AD_HOME}/.local/opt/autodarts" ]]    && cp -a "${AD_HOME}/.local/opt/autodarts" "${bd}/local_opt_autodarts" || true
  [[ -f "${AD_HOME}/.local/bin/autodarts" ]]    && cp "${AD_HOME}/.local/bin/autodarts" "${bd}/autodarts_v1_bin" || true

  {
    echo "=== uname ===";       uname -a
    echo "=== os-release ===";  cat /etc/os-release 2>/dev/null || true
    echo "=== user ===";        id "$AD_USER"
    echo "=== autodarts version ==="
    run_as_user "${AD_HOME}/.local/bin/autodarts" --version 2>/dev/null \
      || run_as_user "${AD_HOME}/.local/opt/autodarts/autodarts" --version 2>/dev/null \
      || echo "unbekannt"
    echo "=== autodarts.service ==="
    cat /etc/systemd/system/autodarts.service 2>/dev/null || echo "nicht vorhanden"
    echo "=== symlinks ==="
    ls -la "${AD_HOME}/.local/bin/autodarts" 2>/dev/null || echo "kein symlink"
  } > "${bd}/metadata.txt" 2>/dev/null || true

  log "OK: Backup abgeschlossen ($bd)"
}

# ─── §5: v1 Services stoppen (Dateien bleiben bis v2 verifiziert) ────────────

stop_v1_services() {
  if systemctl is-active --quiet autodarts.service 2>/dev/null; then
    log "Stoppe v1 autodarts.service"
    systemctl stop autodarts.service 2>/dev/null || true
    sleep 2
  fi
  if systemctl is-enabled --quiet autodartsupdater.service 2>/dev/null; then
    log "Deaktiviere autodartsupdater.service"
    systemctl disable --now autodartsupdater.service 2>/dev/null || true
  fi
}

# §16: Legacy erst NACH bestätigtem v2 entfernen
cleanup_v1_files() {
  log "Entferne v1 Legacy-Dateien (v2 bestätigt)"
  rm -f  /etc/systemd/system/autodarts.service           2>/dev/null || true
  rm -f  /etc/systemd/system/autodartsupdater.service    2>/dev/null || true
  rm -rf /etc/systemd/system/autodartsupdater.service.d  2>/dev/null || true
  rm -f  /usr/local/bin/autodarts-safe-updater.sh        2>/dev/null || true
  rm -rf "${AD_HOME}/.local/opt/autodarts"               2>/dev/null || true
  systemctl daemon-reload 2>/dev/null || true
  log "OK: v1 Legacy-Dateien entfernt"
}

# ─── §9: Linger ───────────────────────────────────────────────────────────────

enable_linger() {
  loginctl show-user "$AD_USER" 2>/dev/null | grep -q "^Linger=yes" && return 0
  log "Aktiviere loginctl linger für $AD_USER"
  loginctl enable-linger "$AD_USER" 2>/dev/null || log "WARN: linger fehlgeschlagen"
}

# ─── §6: Installer — immer --headless ────────────────────────────────────────

install_autodarts_v2() {
  log "Installiere Autodarts v2 (https://autodarts.sh --headless) ..."
  if run_as_user bash -c "curl -fsSL https://autodarts.sh | bash -s -- --headless" >>"${LOG}" 2>&1; then
    # Post-install: Binary prüfen (§6 Nachverifikation)
    local new_bin
    new_bin="$(find_v2_binary 2>/dev/null || true)"
    if [[ -z "$new_bin" ]]; then
      log "FEHLER: Binary nach Installation nicht gefunden"; return 1
    fi
    local ver
    ver="$(run_as_user "$new_bin" --version 2>/dev/null || true)"
    log "OK: Autodarts v2 installiert — Version: $ver"
    V2_BINARY="$new_bin"
    return 0
  else
    log "FEHLER: Installation fehlgeschlagen"; return 1
  fi
}

# ─── §8: User-Service (vollständige Unit) ─────────────────────────────────────

ensure_user_service() {
  run_as_user mkdir -p "$USER_SERVICE_DIR" 2>/dev/null || true

  if [[ ! -f "$USER_SERVICE" ]]; then
    log "Erstelle User-Service: $USER_SERVICE"
    local bin="${V2_BINARY:-${AD_HOME}/.local/share/autodarts/autodarts}"
    sudo -u "$AD_USER" bash -c "cat > '${USER_SERVICE}'" <<EOF
[Unit]
Description=Autodarts board
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=notify
NotifyAccess=main
ExecStart=${bin} run
Restart=always
RestartSec=5
TimeoutStopSec=15
WatchdogSec=30
StateDirectory=autodarts
StateDirectoryMode=0700
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF
    chown "$AD_USER:$AD_USER" "$USER_SERVICE" 2>/dev/null || true
  fi

  run_as_user systemctl --user daemon-reload 2>/dev/null || true
  service_enabled || run_as_user systemctl --user enable autodarts.service 2>/dev/null || true
  service_active  || run_as_user systemctl --user start  autodarts.service 2>/dev/null \
    || log "WARN: Service-Start fehlgeschlagen"
}

# ─── §11: API-Verifikation ────────────────────────────────────────────────────

verify_v2() {
  log "Warte auf API (Port $PORT_V2, max 30s) ..."
  local i
  for i in $(seq 1 15); do
    if service_active && api_reachable; then
      log "OK: Service active + API erreichbar"
      return 0
    fi
    sleep 2
  done
  log "WARN: API nach 30s nicht erreichbar"
  return 1
}

# ─── §13: ttyd installieren ───────────────────────────────────────────────────

install_ttyd() {
  if command -v ttyd >/dev/null 2>&1 && ttyd --version >/dev/null 2>&1; then
    log "INFO: ttyd bereits vorhanden"
    return 0
  fi
  log "Installiere ttyd ..."
  if apt-get install -y ttyd >>"${LOG}" 2>&1; then
    log "OK: ttyd via apt installiert"; return 0
  fi
  log "WARN: apt fehlgeschlagen, versuche Binary-Download ..."
  local arch
  case "$(uname -m)" in
    aarch64|arm64) arch="aarch64" ;;
    armv7l)        arch="armhf"   ;;
    x86_64)        arch="x86_64"  ;;
    *)             arch="$(uname -m)" ;;
  esac
  if curl -fsSL --max-time 60 \
       "https://github.com/tsl0922/ttyd/releases/latest/download/ttyd.${arch}" \
       -o /usr/local/bin/ttyd 2>/dev/null; then
    chmod 755 /usr/local/bin/ttyd
    log "OK: ttyd Binary installiert"
  else
    log "WARN: ttyd konnte nicht installiert werden"; return 1
  fi
}

# ─── §14: TUI-Service (korrekte ExecStart) ────────────────────────────────────

ensure_tui_service() {
  local ttyd_bin
  ttyd_bin="$(command -v ttyd 2>/dev/null || echo /usr/local/bin/ttyd)"
  local autodarts_cli="${AD_HOME}/.local/bin/autodarts"
  local tui_wrapper="/usr/local/bin/autodarts-tui-wrapper"

  # Wrapper installieren — sorgt dafür dass autodarts beim Tab-Schließen endet
  cat > "$tui_wrapper" <<'TUI_WRAPPER_EOF'
#!/usr/bin/env bash
AD_BIN="${HOME}/.local/bin/autodarts"
pkill -u "$(id -un)" -f "${AD_BIN} -H 127.0.0.1" 2>/dev/null || true
sleep 0.3
_reset_terminal() {
    printf '\033[?1000l\033[?1002l\033[?1003l\033[?1006l\033[?1015l' 2>/dev/null || true
    printf '\033[?25h' 2>/dev/null || true
}
_cleanup() {
    kill -TERM "$AD_PID" 2>/dev/null || true
    sleep 0.5
    kill -KILL "$AD_PID" 2>/dev/null || true
    _reset_terminal
}
trap '_cleanup' HUP TERM INT EXIT
_reset_terminal
"$AD_BIN" -H 127.0.0.1 <&0 >&1 2>&2 &
AD_PID=$!
wait "$AD_PID"
TUI_WRAPPER_EOF
  chmod +x "$tui_wrapper"
  log "OK: autodarts-tui-wrapper installiert → ${tui_wrapper}"

  if [[ -f "$TUI_SERVICE" ]] && systemctl is-enabled --quiet autodarts-tui.service 2>/dev/null; then
    log "INFO: autodarts-tui.service bereits vorhanden"
    local _patched=0
    # -m 1 entfernen falls vorhanden (verursacht Reconnect-Probleme bei schnellem Tab-Wechsel)
    if grep -q -- '-m 1' "$TUI_SERVICE" 2>/dev/null; then
      log "INFO: Entferne -m 1 aus autodarts-tui.service"
      sed -i 's| -m 1||g' "$TUI_SERVICE" 2>/dev/null || true
      _patched=1
      log "OK: -m 1 entfernt"
    fi
    # Wrapper eintragen falls noch direkter autodarts-Aufruf in ExecStart
    if ! grep -q 'autodarts-tui-wrapper' "$TUI_SERVICE" 2>/dev/null; then
      log "INFO: Ersetze direkten autodarts-Aufruf durch autodarts-tui-wrapper"
      sed -i "s|${autodarts_cli} -H 127\.0\.0\.1|${tui_wrapper}|" "$TUI_SERVICE" 2>/dev/null || true
      _patched=1
      log "OK: ExecStart auf autodarts-tui-wrapper umgestellt"
    fi
    if [[ "$_patched" -eq 1 ]]; then
      systemctl daemon-reload 2>/dev/null || true
      systemctl restart autodarts-tui.service 2>/dev/null || true
      log "OK: autodarts-tui.service nach Patch neu gestartet"
    fi
    systemctl is-active --quiet autodarts-tui.service 2>/dev/null \
      || systemctl start autodarts-tui.service 2>/dev/null || true
    return 0
  fi

  log "Erstelle autodarts-tui.service"
  cat > "$TUI_SERVICE" <<EOF
[Unit]
Description=Autodarts v2 Browser TUI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${AD_USER}
Environment=HOME=${AD_HOME}
WorkingDirectory=${AD_HOME}
ExecStart=${ttyd_bin} -W -p ${PORT_TTYD} -i 0.0.0.0 ${tui_wrapper}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload 2>/dev/null || true
  systemctl enable autodarts-tui.service 2>/dev/null || true
  systemctl start  autodarts-tui.service 2>/dev/null \
    || log "WARN: autodarts-tui.service Start fehlgeschlagen"
  log "OK: autodarts-tui.service installiert"
}

# ─── §3 Caller: boot-stabilize.conf Drop-in bereinigen ───────────────────────
# Das Drop-in enthält eine [Unit]-Abhängigkeit auf den alten System-Service
# autodarts.service. Da v2 als User-Service läuft, muss diese weg.
# ExecStartPre und RestartSec bleiben erhalten.

fix_caller_drop_in() {
  local drop_in="/etc/systemd/system/darts-caller.service.d/boot-stabilize.conf"
  [[ -f "$drop_in" ]] || { log "INFO: $drop_in nicht vorhanden → skip"; return 0; }

  # Prüfen ob die Abhängigkeit überhaupt drin ist
  if ! grep -q "autodarts.service" "$drop_in" 2>/dev/null; then
    log "INFO: $drop_in enthält keine autodarts.service-Abhängigkeit → skip"
    return 0
  fi

  log "Bereinige $drop_in (entferne autodarts.service-Abhängigkeit)"
  cat > "$drop_in" <<'EOF'
[Service]
ExecStartPre=/bin/sleep 12
RestartSec=8
EOF

  systemctl daemon-reload 2>/dev/null || true

  if systemctl is-active --quiet darts-caller.service 2>/dev/null; then
    log "Starte darts-caller.service neu (Drop-in geändert)"
    systemctl restart darts-caller.service 2>/dev/null \
      || log "WARN: darts-caller.service Neustart fehlgeschlagen"
  fi
  log "OK: boot-stabilize.conf bereinigt"
}

# ─── §15: Firewall ────────────────────────────────────────────────────────────

ensure_firewall_port() {
  command -v ufw >/dev/null 2>&1 || return 0
  ufw status 2>/dev/null | grep -q "^Status: active" || return 0
  ufw status 2>/dev/null | grep -q "${PORT_TTYD}/tcp" && {
    log "INFO: UFW Port ${PORT_TTYD} bereits freigegeben"; return 0
  }
  log "Öffne UFW Port ${PORT_TTYD}/tcp"
  ufw allow "${PORT_TTYD}/tcp" >/dev/null 2>&1 || true
}

# ─── State-File ───────────────────────────────────────────────────────────────

write_state() {
  local result="$1"
  local ver
  ver="$(run_as_user "${V2_BINARY:-/dev/null}" --version 2>/dev/null \
    | grep -oE '[0-9]+\.[0-9]+[^ ]*' | head -n1 || echo 'unknown')"
  cat > "$STATE_FILE" <<EOF
{
  "migration_version": 3,
  "status": "success",
  "result": "${result}",
  "ad_user": "${AD_USER}",
  "ad_uid": "${AD_UID}",
  "ad_home": "${AD_HOME}",
  "v2_binary": "${V2_BINARY:-unknown}",
  "autodarts_version": "${ver}",
  "service_enabled": $(service_enabled && echo true || echo false),
  "service_active": $(service_active && echo true || echo false),
  "api_reachable": $(api_reachable && echo true || echo false),
  "ttyd_installed": $(command -v ttyd >/dev/null 2>&1 && echo true || echo false),
  "ttyd_service_active": $(systemctl is-active --quiet autodarts-tui.service 2>/dev/null && echo true || echo false),
  "updated_at": "$(date -Iseconds)"
}
EOF
  log "State-Datei: $STATE_FILE ($result)"
}

# ─── HAUPTABLAUF ─────────────────────────────────────────────────────────────

log "===== Autodarts V2 Migration START (user=$AD_USER) ====="

V2_BINARY="$(find_v2_binary 2>/dev/null || true)"
AD_STATE="$(classify_state)"
log "Zustand: $AD_STATE | Binary: ${V2_BINARY:-keines}"

case "$AD_STATE" in

  V2_HEALTHY)
    # §18: Idempotenz — kein Installer, kein Backup, kein Service-Neustart
    log "v2 läuft gesund → nur Integration prüfen"
    enable_linger
    fix_caller_drop_in || true
    install_ttyd       || true
    ensure_tui_service || true
    ensure_firewall_port
    write_state "already_v2"
    log "===== V2 Migration SKIP (already healthy) ====="
    exit 0
    ;;

  V2_SERVICE_BROKEN)
    log "v2-Binary OK, Service/API defekt → reparieren"
    enable_linger
    fix_caller_drop_in || true
    ensure_user_service
    verify_v2          || true
    install_ttyd       || true
    ensure_tui_service || true
    ensure_firewall_port
    write_state "repaired_v2_service"
    log "===== V2 Migration OK (repaired) ====="
    exit 0
    ;;

  NEWER_THAN_V2)
    log "Autodarts-Version > 2 → kein Eingriff"
    ensure_tui_service || true
    ensure_firewall_port
    write_state "newer_than_v2"
    log "===== V2 Migration SKIP (newer_than_v2) ====="
    exit 0
    ;;

  V1_ONLY|FRESH|BROKEN_OR_UNKNOWN)
    case "$AD_STATE" in
      V1_ONLY)           MIGRATION_TYPE="migrated_from_v1"; log "v1 erkannt → Migration" ;;
      BROKEN_OR_UNKNOWN) MIGRATION_TYPE="fresh_install_from_broken"; log "Defekter Zustand → Neuinstallation" ;;
      *)                 MIGRATION_TYPE="fresh_install"; log "Kein Autodarts → Neuinstallation" ;;
    esac

    backup_before_migration
    enable_linger
    fix_caller_drop_in || true
    stop_v1_services

    if ! install_autodarts_v2; then
      log "FEHLER: Installation fehlgeschlagen → Abbruch"
      exit 1
    fi

    ensure_user_service

    # §11: API prüfen
    if verify_v2; then
      # §16: Legacy erst nach bestätigtem v2 entfernen
      cleanup_v1_files
    else
      log "WARN: v2 Service nicht aktiv — Legacy-Dateien bleiben als Fallback"
    fi

    install_ttyd       || true
    ensure_tui_service || true
    ensure_firewall_port
    write_state "$MIGRATION_TYPE"
    log "===== V2 Migration OK (${MIGRATION_TYPE}) ====="
    exit 0
    ;;

  *)
    log "FEHLER: Unbekannter Zustand '$AD_STATE'"
    exit 1
    ;;
esac
