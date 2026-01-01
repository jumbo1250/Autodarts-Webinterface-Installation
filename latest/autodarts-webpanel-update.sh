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

# Dateien die aktualisiert werden sollen: "RemoteName|LocalPath|chmod"
FILES=(
  "autodarts-web.py|${BIN_DIR}/autodarts-web.py|755"
  "autodarts-button-led.py|${BIN_DIR}/autodarts-button-led.py|755"
  "Autodarts_install_manual.pdf|${DATA_DIR}/Autodarts_install_manual.pdf|644"
  "GPIO_Setup.jpeg|${DATA_DIR}/GPIO_Setup.jpeg|644"
  "Autodarts_Installationshandbuch_v2.docx|${DATA_DIR}/Autodarts_Installationshandbuch_v2.docx|644"
  "version.txt|${LOCAL_VER_FILE}|644"
)

ts() { date +"%Y-%m-%d %H:%M:%S"; }

echo "===== $(ts) Webpanel Update START =====" >> "${LOG_FILE}"

# Remote version holen (optional, aber nice)
REMOTE_VER="$(curl -fsSL "${BASE_URL}/version.txt" | tr -d '\r\n' || true)"
LOCAL_VER="$(cat "${LOCAL_VER_FILE}" 2>/dev/null | tr -d '\r\n' || true)"

echo "Local:  ${LOCAL_VER:-unknown}"  >> "${LOG_FILE}"
echo "Remote: ${REMOTE_VER:-unknown}" >> "${LOG_FILE}"

TMP_DIR="$(mktemp -d)"
cleanup(){ rm -rf "${TMP_DIR}"; }
trap cleanup EXIT

# 1) Alles erst downloaden (wenn ein Download failt -> nix wird ersetzt)
for entry in "${FILES[@]}"; do
  IFS="|" read -r src dst mode <<< "${entry}"
  url="${BASE_URL}/${src}"
  out="${TMP_DIR}/${src}"
  echo "डाउनलोड ${url}" >> "${LOG_FILE}"
  curl -fsSL --retry 2 --connect-timeout 5 --max-time 30 "${url}" -o "${out}"
  test -s "${out}"  # nicht leer
done

# 2) Erst jetzt ersetzen (mit Backup)
for entry in "${FILES[@]}"; do
  IFS="|" read -r src dst mode <<< "${entry}"
  mkdir -p "$(dirname "${dst}")"

  if [[ -f "${dst}" ]]; then
    cp -a "${dst}" "${dst}.bak.$(date +%Y%m%d_%H%M%S)" || true
  fi

  install -m "${mode}" "${TMP_DIR}/${src}" "${dst}"
done

echo "Restart ${WEB_SERVICE}" >> "${LOG_FILE}"
# Service restart
systemctl restart "${WEB_SERVICE}" || {
  echo "ERROR: systemctl restart failed" >> "${LOG_FILE}"
  exit 1
}

echo "===== $(ts) Webpanel Update OK =====" >> "${LOG_FILE}"
