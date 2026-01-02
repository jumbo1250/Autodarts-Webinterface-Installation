#!/usr/bin/env bash
set -euo pipefail

REPO="jumbo1250/Autodarts-Webinterface-Installation"
BASE_URL="https://raw.githubusercontent.com/${REPO}/main/latest"

# Zielpfade bei dir:
BIN_DIR="/usr/local/bin"
DATA_DIR="/home/peter/autodarts-data"
STATE_DIR="/var/lib/autodarts"
LOG_FILE="/var/log/autodarts_webpanel_update.log"
WEB_SERVICE="autodarts-web.service"

mkdir -p "${DATA_DIR}" "${STATE_DIR}"

LOCAL_VER_FILE="${STATE_DIR}/webpanel-version.txt"

# Dateien die aktualisiert werden sollen: "RemoteName|LocalPath"
FILES=(
  "autodarts-web.py|${BIN_DIR}/autodarts-web.py"
  "autodarts-button-led.py|${BIN_DIR}/autodarts-button-led.py"
  "Autodarts_install_manual.pdf|${DATA_DIR}/Autodarts_install_manual.pdf"
  "GPIO_Setup.jpeg|${DATA_DIR}/GPIO_Setup.jpeg"
  "Autodarts_Installationshandbuch_v2.docx|${DATA_DIR}/Autodarts_Installationshandbuch_v2.docx"
  "start-custom.sh|/var/lib/autodarts/config/darts-wled/start-custom.sh"
  "version.txt|${LOCAL_VER_FILE}"
)

ts() { date +"[%Y-%m-%d %H:%M:%S]"; }
log(){ echo "$(ts) $*" | tee -a "${LOG_FILE}" >/dev/null; }

log "===== Webpanel Update START ====="

REMOTE_VER="$(curl -fsSL "${BASE_URL}/version.txt" | tr -d '\r\n' || true)"
LOCAL_VER="$(cat "${LOCAL_VER_FILE}" 2>/dev/null | tr -d '\r\n' || true)"
log "Local:  ${LOCAL_VER:-unknown}"
log "Remote: ${REMOTE_VER:-unknown}"

TMP_DIR="$(mktemp -d)"
cleanup(){ rm -rf "${TMP_DIR}"; }
trap cleanup EXIT

# 1) Alles erst downloaden (wenn ein Download failt -> nix wird ersetzt)
for entry in "${FILES[@]}"; do
  IFS="|" read -r src dst <<< "${entry}"
  url="${BASE_URL}/${src}"
  out="${TMP_DIR}/${src}"

  log "Download: ${url}"
  curl -fsSL --retry 2 --connect-timeout 5 --max-time 30 "${url}" -o "${out}"
  test -s "${out}"  # nicht leer
done

# 2) Erst jetzt ersetzen (mit Backup) + ALLES 777
for entry in "${FILES[@]}"; do
  IFS="|" read -r src dst <<< "${entry}"
  mkdir -p "$(dirname "${dst}")"

  if [[ -f "${dst}" ]]; then
    cp -a "${dst}" "${dst}.bak.$(date +%Y%m%d_%H%M%S)" || true
  fi

  # Copy (mode setzen wir danach auf 777)
  install -m 644 "${TMP_DIR}/${src}" "${dst}"

  # ALLES offen (dein Wunsch)
  chmod 777 "${dst}" || true
done

# Optional: auch die Ordner (falls du willst, sonst rauslöschen)
chmod 777 "${BIN_DIR}" "${DATA_DIR}" "${STATE_DIR}" 2>/dev/null || true

log "Restart ${WEB_SERVICE}"
systemctl restart "${WEB_SERVICE}" || {
  log "ERROR: systemctl restart failed"
  exit 1
}

# Wenn wir start-custom.sh updated haben, darts-wled neu starten (nur wenn service existiert)
if systemctl list-unit-files | grep -q "^darts-wled.service"; then
  log "Restart darts-wled.service (weil start-custom.sh updated)"
  systemctl restart darts-wled.service || true
fi

log "===== Webpanel Update OK ====="
echo "OK"
