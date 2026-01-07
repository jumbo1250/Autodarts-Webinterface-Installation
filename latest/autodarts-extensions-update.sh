#!/usr/bin/env bash
set -euo pipefail

# ---- Einstellungen ----
CALLER_DIR="/var/lib/autodarts/extensions/darts-caller"
WLED_DIR="/var/lib/autodarts/extensions/darts-wled"

CALLER_START="${CALLER_DIR}/start-custom.sh"
WLED_WRAPPER="${WLED_DIR}/start-custom.sh"
WLED_CONFIG="/var/lib/autodarts/config/darts-wled/start-custom.sh"

BACKUP_ROOT="/var/lib/autodarts/config/backups"
TS="$(date +'%Y%m%d-%H%M%S')"
BK="${BACKUP_ROOT}/extensions-update-${TS}"

LOG="/var/log/autodarts_extensions_update.log"

# optional: mit FORCE=1 alles erzwingen (pip + restart auch wenn unchanged)
FORCE="${FORCE:-0}"

# ---- Helpers ----
log() { echo "[$(date +'%F %T')] $*"; }

mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1

exists_unitfile() {
  systemctl list-unit-files --no-legend 2>/dev/null | awk '{print $1}' | grep -qx "$1"
}
is_active() { systemctl is-active --quiet "$1"; }

stop_if_exists() {
  local svc="$1"
  if exists_unitfile "$svc"; then
    if is_active "$svc"; then
      log "Stoppe $svc ..."
      systemctl stop "$svc" || true
      echo "1"
    else
      echo "0"
    fi
  else
    echo "0"
  fi
}

restart_if_exists() {
  local svc="$1"
  if exists_unitfile "$svc"; then
    log "Starte/Restarte $svc ..."
    systemctl restart "$svc" || systemctl start "$svc" || true
  else
    log "Service nicht gefunden: $svc (ok, übersprungen)"
  fi
}

ensure_wrapper() {
  if [[ -d "$WLED_DIR" ]]; then
    log "Stelle sicher: WLED Wrapper zeigt auf Config-Startscript ..."
    cat > "$WLED_WRAPPER" <<EOF
#!/usr/bin/env bash
exec "$WLED_CONFIG"
EOF
    chmod +x "$WLED_WRAPPER" || true
  fi
  if [[ -f "$WLED_CONFIG" ]]; then
    chmod +x "$WLED_CONFIG" || true
  fi
}

backup_file_if_exists() {
  local src="$1"
  local dst="$2"
  if [[ -f "$src" ]]; then
    mkdir -p "$(dirname "$dst")"
    cp -a "$src" "$dst"
    log "Backup: $src -> $dst"
  else
    log "Backup übersprungen (nicht gefunden): $src"
  fi
}

restore_file_if_exists() {
  local src="$1"
  local dst="$2"
  if [[ -f "$src" ]]; then
    mkdir -p "$(dirname "$dst")"
    cp -a "$src" "$dst"
    log "Restore: $src -> $dst"
  else
    log "Restore übersprungen (Backup fehlt): $src"
  fi
}

repo_update_status() {
  # prints one of: CHANGED / UNCHANGED / SKIPPED
  local dir="$1"
  local label="$2"

  if [[ ! -d "$dir/.git" ]]; then
    log "$label: Kein Git-Repo: $dir (SKIPPED)"
    echo "SKIPPED"
    return 0
  fi

  # git safe.directory (gegen "dubious ownership" bei root)
  git config --global --add safe.directory "$dir" >/dev/null 2>&1 || true

  log "$label: Update Repo: $dir"
  pushd "$dir" >/dev/null || { echo "SKIPPED"; return 0; }

  local before after upstream
  before="$(git rev-parse HEAD 2>/dev/null || echo "")"
  upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || echo "")"

  git fetch --all --prune || true

  if [[ -n "$upstream" ]]; then
    git reset --hard "@{u}" || true
  else
    git pull --rebase --autostash || true
  fi

  after="$(git rev-parse HEAD 2>/dev/null || echo "")"
  popd >/dev/null || true

  if [[ -n "$before" && -n "$after" && "$before" != "$after" ]]; then
    log "$label: CHANGED ($before -> $after)"
    echo "CHANGED"
  else
    log "$label: UNCHANGED ($after)"
    echo "UNCHANGED"
  fi
}

venv_refresh_install_if_needed() {
  local dir="$1"
  local label="$2"
  local changed="$3"
  local requirements="$dir/requirements.txt"

  [[ -d "$dir" ]] || return 0

  if [[ "$FORCE" == "1" ]]; then
    log "$label: FORCE=1 -> pip/venv wird ausgeführt."
  elif [[ "$changed" != "CHANGED" && -d "$dir/.venv" ]]; then
    log "$label: Keine Repo-Änderung und .venv vorhanden -> pip übersprungen."
    return 0
  fi

  log "$label: Python venv/requirements: $dir"
  pushd "$dir" >/dev/null || return 1

  export PIP_DISABLE_PIP_VERSION_CHECK=1
  export PIP_NO_INPUT=1

  if [[ ! -d ".venv" ]]; then
    python3 -m venv .venv
  fi

  # shellcheck disable=SC1091
  source .venv/bin/activate

  python3 -m pip install -U pip setuptools wheel || true

  if [[ -f "$requirements" ]]; then
    if python3 -m pip install -r "$requirements" --upgrade; then
      log "$label: requirements OK"
    else
      log "$label: requirements FAIL -> Retry ohne pyinstaller/pyinstaller-hooks-contrib"
      grep -v -E '^pyinstaller(==|$)|^pyinstaller-hooks-contrib(==|$)' "$requirements" > /tmp/req-no-pyinstaller.txt
      python3 -m pip install -r /tmp/req-no-pyinstaller.txt --upgrade
    fi
  else
    log "$label: Keine requirements.txt gefunden (übersprungen)"
  fi

  deactivate || true
  popd >/dev/null || true
}

# ---- MAIN ----
if [[ "$(id -u)" -ne 0 ]]; then
  echo "Bitte mit sudo ausführen."
  exit 1
fi

mkdir -p "$BK"
log "===== Extensions Update START ====="
log "Backup-Ordner: $BK"
log "FORCE=$FORCE"

# 1) Services (falls vorhanden) stoppen, Status merken
CALLER_WAS_ACTIVE="$(stop_if_exists darts-caller.service)"
WLED_WAS_ACTIVE="$(stop_if_exists darts-wled.service)"

# 2) Backups wichtiger Dateien
backup_file_if_exists "$CALLER_START" "${BK}/darts-caller/start-custom.sh"
backup_file_if_exists "$WLED_WRAPPER" "${BK}/darts-wled/start-custom.wrapper.sh"
backup_file_if_exists "$WLED_CONFIG"  "${BK}/darts-wled/start-custom.config.sh"

# 3) Repos updaten (und merken ob geändert)
CALLER_STATUS="$(repo_update_status "$CALLER_DIR" "CALLER")"
WLED_STATUS="$(repo_update_status "$WLED_DIR" "WLED")"

# 4) Start-Custom Dateien wiederherstellen
restore_file_if_exists "${BK}/darts-caller/start-custom.sh"         "$CALLER_START"
restore_file_if_exists "${BK}/darts-wled/start-custom.wrapper.sh"  "$WLED_WRAPPER"
restore_file_if_exists "${BK}/darts-wled/start-custom.config.sh"   "$WLED_CONFIG"

# 5) Wrapper sicherstellen
ensure_wrapper

# 6) venv + requirements nur wenn nötig
venv_refresh_install_if_needed "$CALLER_DIR" "CALLER" "$CALLER_STATUS"
venv_refresh_install_if_needed "$WLED_DIR"   "WLED"   "$WLED_STATUS"

# 7) Services wieder starten nur wenn vorher aktiv UND relevant geändert (oder FORCE)
if [[ "$CALLER_WAS_ACTIVE" == "1" ]]; then
  if [[ "$FORCE" == "1" || "$CALLER_STATUS" == "CHANGED" ]]; then
    restart_if_exists darts-caller.service
  else
    log "CALLER: war aktiv, aber UNCHANGED -> kein Restart."
  fi
else
  log "CALLER: war vorher aus -> bleibt aus (ok)."
fi

if [[ "$WLED_WAS_ACTIVE" == "1" ]]; then
  if [[ "$FORCE" == "1" || "$WLED_STATUS" == "CHANGED" ]]; then
    restart_if_exists darts-wled.service
  else
    log "WLED: war aktiv, aber UNCHANGED -> kein Restart."
  fi
else
  log "WLED: war vorher aus -> bleibt aus (ok)."
fi

systemctl daemon-reload || true

log "===== SUMMARY ====="
log "CALLER: $CALLER_STATUS"
log "WLED:   $WLED_STATUS"
log "Backup: $BK"
log "===== Extensions Update DONE ====="
echo "CALLER: $CALLER_STATUS"
echo "WLED:   $WLED_STATUS"
echo "Backup: $BK"
exit 0
