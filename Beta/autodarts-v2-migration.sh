#!/usr/bin/env bash
# BUILD: AUTODARTS-V2-MIGRATION-20261008-01
#
# Einmalige Migration von Autodarts v1 (System-Service) → v2 (User-Service, Port 3180).
# Idempotent: Läuft v2 bereits gesund (Port 3180 oder User-Service aktiv) → exit 0.
# Wird nur ausgeführt, wenn er in DIESEM Update heruntergeladen wurde (DOWNLOADED[]-Pattern).

set -euo pipefail

USER_NAME="${AUTODARTS_DESKTOP_USER:-peter}"
PORT_V2=3180
SERVICE="autodarts.service"
LOG="${LOG_FILE:-/var/log/autodarts_webpanel_update.log}"

log_m() { echo "[$(date +'%F %T')] [V2-MIGRATION] $*" | tee -a "${LOG}" >/dev/null; }

# ────────────────────────────────────────────────────────────────────────────────
# Hilfsfunktionen
# ────────────────────────────────────────────────────────────────────────────────

user_uid() {
  id -u "$USER_NAME" 2>/dev/null || true
}

run_as_user() {
  local uid
  uid="$(user_uid)"
  [[ -z "$uid" ]] && { log_m "FEHLER: User '$USER_NAME' nicht gefunden."; return 1; }
  sudo -n -u "$USER_NAME" \
    XDG_RUNTIME_DIR="/run/user/${uid}" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${uid}/bus" \
    "$@"
}

# Prüft ob v2 bereits läuft und gesund ist
is_v2_healthy() {
  local uid
  uid="$(user_uid)"
  [[ -z "$uid" ]] && return 1

  # Priorität 1: User-Service aktiv?
  if run_as_user systemctl --user is-active "$SERVICE" >/dev/null 2>&1; then
    log_m "INFO: Autodarts v2 läuft als User-Service."
    return 0
  fi

  # Priorität 2: Port 3180 antwortet?
  if curl -sSf --max-time 3 "http://localhost:${PORT_V2}/api/version" >/dev/null 2>&1; then
    log_m "INFO: Autodarts v2 antwortet auf Port ${PORT_V2}."
    return 0
  fi

  return 1
}

# Prüft ob noch der alte System-Service (v1) läuft
stop_v1_if_running() {
  if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
    log_m "Stoppe v1 System-Service: $SERVICE"
    systemctl stop "$SERVICE" 2>/dev/null || true
    sleep 2
  fi

  # Alten autodartsupdater-Service deaktivieren (wird durch v2 ersetzt)
  if systemctl is-enabled --quiet autodartsupdater.service 2>/dev/null; then
    log_m "Deaktiviere alten autodartsupdater.service"
    systemctl disable autodartsupdater.service 2>/dev/null || true
  fi
}

arch_norm() {
  case "$(uname -m)" in
    x86_64|amd64)   echo "amd64"  ;;
    aarch64|arm64)  echo "arm64"  ;;
    armv7l)         echo "armv7l" ;;
    *)              uname -m      ;;
  esac
}

# Lädt den offiziellen Autodarts-Installer herunter und validiert ihn
fetch_installer() {
  local base="$1"
  local tmp
  tmp="$(mktemp)"

  if ! curl -fsSL --connect-timeout 8 --max-time 30 "${base}/install.sh" -o "$tmp" 2>/dev/null; then
    rm -f "$tmp"
    return 1
  fi

  # Sanity-Check: muss ein Shell-Script sein und INSTALL_DIR enthalten
  if ! head -n 1 "$tmp" | grep -q '^#!' || ! grep -q 'INSTALL_DIR\|install_dir\|autodarts' "$tmp"; then
    log_m "WARN: Installer von ${base} sieht ungültig aus."
    rm -f "$tmp"
    return 1
  fi

  echo "$tmp"
}

# Versucht get.autodarts.io, dann get.autodarts.com
install_autodarts_v2_binary() {
  local installer_path base

  for base in "https://get.autodarts.io" "https://get.autodarts.com"; do
    log_m "Versuche Installer von: $base"
    installer_path="$(fetch_installer "$base" || true)"
    if [[ -n "$installer_path" && -f "$installer_path" ]]; then
      log_m "Installer gefunden: $base"
      # Installer als Ziel-User ausführen (v2 installiert im Home des Users)
      if run_as_user bash "$installer_path" >>"${LOG}" 2>&1; then
        rm -f "$installer_path"
        log_m "OK: Autodarts v2 durch Installer installiert."
        return 0
      else
        log_m "WARN: Installer von ${base} fehlgeschlagen."
        rm -f "$installer_path"
      fi
    fi
  done

  log_m "FEHLER: Kein gültiger Installer erreichbar (get.autodarts.io / get.autodarts.com)."
  return 1
}

# Aktiviert Linger damit der User-Service auch ohne aktive Login-Session läuft
enable_linger() {
  if loginctl show-user "$USER_NAME" 2>/dev/null | grep -q "^Linger=yes"; then
    log_m "INFO: Linger für $USER_NAME bereits aktiv."
    return 0
  fi
  log_m "Aktiviere loginctl linger für $USER_NAME"
  loginctl enable-linger "$USER_NAME" 2>/dev/null || {
    log_m "WARN: loginctl enable-linger fehlgeschlagen (kein harter Fehler)."
  }
}

# Startet den User-Service (falls noch nicht aktiv)
start_user_service() {
  if run_as_user systemctl --user is-active "$SERVICE" >/dev/null 2>&1; then
    log_m "INFO: User-Service bereits aktiv."
    return 0
  fi

  log_m "Aktiviere und starte User-Service: $SERVICE"
  run_as_user systemctl --user daemon-reload 2>/dev/null || true
  run_as_user systemctl --user enable "$SERVICE" 2>/dev/null || true
  run_as_user systemctl --user start "$SERVICE" 2>/dev/null || {
    log_m "WARN: systemctl --user start $SERVICE fehlgeschlagen."
    return 1
  }
}

# Verifiziert Port 3180 (bis zu 20s warten)
verify_v2() {
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -sSf --max-time 3 "http://localhost:${PORT_V2}/api/version" >/dev/null 2>&1; then
      log_m "OK: Autodarts v2 antwortet auf Port ${PORT_V2}."
      return 0
    fi
    sleep 2
  done
  log_m "WARN: Port ${PORT_V2} antwortet nach 20s nicht. Service könnte noch starten."
  return 1
}

# ────────────────────────────────────────────────────────────────────────────────
# Hauptablauf
# ────────────────────────────────────────────────────────────────────────────────

log_m "===== Autodarts V2 Migration START ====="

# Schritt 1: Bereits gesund? → fertig
if is_v2_healthy; then
  log_m "Autodarts v2 bereits aktiv → Migration übersprungen."
  log_m "===== Autodarts V2 Migration SKIP (already running) ====="
  exit 0
fi

log_m "Autodarts v2 nicht aktiv. Starte Migration..."

# Schritt 2: User existiert?
UID_CHECK="$(user_uid)"
if [[ -z "$UID_CHECK" ]]; then
  log_m "FEHLER: User '$USER_NAME' nicht gefunden → Migration abgebrochen."
  exit 1
fi

# Schritt 3: Linger aktivieren (vor Installation, damit Service nach Install autostartet)
enable_linger

# Schritt 4: v1 stoppen
stop_v1_if_running

# Schritt 5: v2 Binary installieren (offizieller Installer)
if ! install_autodarts_v2_binary; then
  log_m "FEHLER: Autodarts v2 konnte nicht installiert werden → Migration abgebrochen."
  exit 1
fi

# Schritt 6: User-Service starten
start_user_service || {
  log_m "WARN: User-Service konnte nicht gestartet werden."
}

# Schritt 7: Verifizierung
if verify_v2; then
  log_m "===== Autodarts V2 Migration OK ====="
  exit 0
else
  log_m "WARN: Verifizierung fehlgeschlagen. Service könnte verzögert starten."
  log_m "===== Autodarts V2 Migration PARTIAL (binary installed, port unverified) ====="
  exit 0
fi
