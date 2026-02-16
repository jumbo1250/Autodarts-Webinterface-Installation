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
FORCE="${FORCE:-0}"  # FORCE=1 -> alles neu laden, egal ob schon aktuell

ts() { date +"[%Y-%m-%d %H:%M:%S]"; }
log(){ echo "$(ts) $*" | tee -a "${LOG_FILE}" >/dev/null; }

# curl installation
# Ergebnis: jeder Job bekommt seinen eigenen Marker
run_once() {
  local name="$1"
  local cmd="$2"

  local marker="${STATE_DIR}/once-${name}.done"

  if [[ -f "$marker" ]]; then
    log "ONCE[$name]: skip (marker exists: $marker)"
    return 0
  fi

  log "ONCE[$name]: run (will write output into ${LOG_FILE})"
  if bash -lc "$cmd" >>"${LOG_FILE}" 2>&1; then
    touch "$marker"
    log "ONCE[$name]: OK (marker created)"
  else
    local rc=$?
    log "ONCE[$name]: FAILED (exit=$rc) -> Update läuft weiter"
  fi

  return 0
}


# Lock gegen Doppelklick / parallele Updates
LOCK="/run/autodarts-webpanel-update.lock"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"
  flock -n 9 || { log "Update läuft bereits (lock: $LOCK)."; echo "BUSY"; exit 0; }
fi

log "===== Webpanel Update START ====="

REMOTE_VER="$(curl -sSL "${BASE_URL}/version.txt" 2>/dev/null | tr -d '\r\n' || true)"
LOCAL_VER="$(cat "${LOCAL_VER_FILE}" 2>/dev/null | tr -d '\r\n' || true)"
log "Local:  ${LOCAL_VER:-unknown}"
log "Remote: ${REMOTE_VER:-unknown}"

TMP_DIR="$(mktemp -d)"
cleanup(){ rm -rf "${TMP_DIR}"; }
trap cleanup EXIT

# Dateien die aktualisiert werden sollen: "RemoteName|LocalPath"
# WICHTIG: Fehlt eine Datei im Repo -> wird geskippt (kein Abbruch).
FILES=(
  "autodarts-web.py|${BIN_DIR}/autodarts-web.py"
  "autodarts-button-led.py|${BIN_DIR}/autodarts-button-led.py"
  "autodarts-extensions-update.sh|${BIN_DIR}/autodarts-extensions-update.sh"
  "Autodarts_install_manual.pdf|${DATA_DIR}/Autodarts_install_manual.pdf"
  "GPIO_Setup.jpeg|${DATA_DIR}/GPIO_Setup.jpeg"
  "Autodarts_Installationshandbuch_v2.docx|${DATA_DIR}/Autodarts_Installationshandbuch_v2.docx"
  "start-custom.sh|/var/lib/autodarts/config/darts-wled/start-custom.sh"
  "version.txt|${LOCAL_VER_FILE}"
  # OPTIONAL: Updater selbst (wenn nicht vorhanden -> skip)
  "autodarts-webpanel-update.sh|${BIN_DIR}/autodarts-webpanel-update.sh"
)

# Merken, was erfolgreich geladen wurde (damit wir nur das ersetzen)
declare -A DOWNLOADED=()

normalize_text_file() {
  local f="$1"
  # Windows-CRLF -> LF
  sed -i 's/\r$//' "$f" 2>/dev/null || true
  # optional: UTF-8 BOM entfernen
  sed -i '1s/^\xEF\xBB\xBF//' "$f" 2>/dev/null || true
}

is_text_ext() {
  case "$1" in
    *.sh|*.py|*.service|*.txt|*.json|*.yml|*.yaml|*.md|*.conf|*.cfg) return 0 ;;
    *) return 1 ;;
  esac
}

# 1) Download-Phase (aber: fehlende Dateien -> SKIP, unveränderte -> SKIP)
for entry in "${FILES[@]}"; do
  IFS="|" read -r src dst <<< "${entry}"
  url="${BASE_URL}/${src}"
  out="${TMP_DIR}/${src}"

  rm -f "${out}" 2>/dev/null || true

  # Conditional Download: wenn lokal vorhanden und FORCE!=1 -> nur holen, wenn remote neuer ist
  if [[ "${FORCE}" != "1" && -f "${dst}" ]]; then
    log "Check/Download (nur wenn neuer): ${src}"
    http_code="$(curl -sSL --retry 2 --connect-timeout 5 --max-time 30 \
      -z "${dst}" -o "${out}" -w "%{http_code}" "${url}" || true)"
  else
    log "Download: ${src}"
    http_code="$(curl -sSL --retry 2 --connect-timeout 5 --max-time 30 \
      -o "${out}" -w "%{http_code}" "${url}" || true)"
  fi

  case "${http_code}" in
    200)
      # nicht leer (falls GitHub mal Mist liefert)
      if [[ ! -s "${out}" ]]; then
        log "WARN: ${src} wurde leer geladen -> skip"
        rm -f "${out}" || true
        continue
      fi

      # Textdateien normalisieren (CRLF/BOM)
      if is_text_ext "${src}"; then
        normalize_text_file "${out}"
      fi

      DOWNLOADED["${src}"]=1
      log "OK: ${src} geladen"
      ;;
    304)
      log "UNCHANGED: ${src} (skip)"
      rm -f "${out}" || true
      ;;
    404)
      log "MISSING: ${src} nicht im Repo -> skip"
      rm -f "${out}" || true
      ;;
    000|"")
      log "WARN: ${src} Download-Problem (network/timeout?) -> skip"
      rm -f "${out}" || true
      ;;
    *)
      log "WARN: ${src} HTTP=${http_code} -> skip"
      rm -f "${out}" || true
      ;;
  esac
done

# 2) Ersetzen (mit Backup) + ALLES 777 (wie bisher)
UPDATED_START_CUSTOM=0

for entry in "${FILES[@]}"; do
  IFS="|" read -r src dst <<< "${entry}"

  # nur ersetzen, wenn geladen
  if [[ -z "${DOWNLOADED[${src}]+x}" ]]; then
    continue
  fi

  mkdir -p "$(dirname "${dst}")"

  if [[ -f "${dst}" ]]; then
    cp -a "${dst}" "${dst}.bak.$(date +%Y%m%d_%H%M%S)" || true
  fi

  install -m 644 "${TMP_DIR}/${src}" "${dst}"
  chmod 777 "${dst}" || true

  if [[ "${src}" == "start-custom.sh" ]]; then
    UPDATED_START_CUSTOM=1
  fi
done

# Optional: auch die Ordner offen lassen (wenn du willst, sonst rauslöschen)
chmod 777 "${BIN_DIR}" "${DATA_DIR}" "${STATE_DIR}" 2>/dev/null || true

log "Restart ${WEB_SERVICE}"
systemctl restart "${WEB_SERVICE}" || {
  log "ERROR: systemctl restart failed"
  exit 1
}

# darts-wled nur neu starten, wenn start-custom.sh wirklich aktualisiert wurde
if [[ "${UPDATED_START_CUSTOM}" == "1" ]]; then
  if systemctl list-unit-files | grep -q "^darts-wled.service"; then
    log "Restart darts-wled.service (start-custom.sh updated)"
    systemctl restart darts-wled.service || true
  fi
fi

#beispiel
#run_once "NAME" "DEIN_COMMAND"

#uvc hack
run_once "uvc-hack-$(uname -r)" '
  set +e

  AD_SERVICE="autodarts.service"
  WAS_ACTIVE=0

  # Autodarts stoppen (nur wenn Service existiert & aktiv ist)
  if systemctl status "$AD_SERVICE" >/dev/null 2>&1; then
    if systemctl is-active --quiet "$AD_SERVICE"; then
      WAS_ACTIVE=1
      echo "Stopping $AD_SERVICE ..."
      systemctl stop "$AD_SERVICE" || true
      sleep 2
    fi
  else
    echo "Service $AD_SERVICE not found -> skip stop"
  fi

  # (optional) Falls irgendwas anderes die Cam blockiert:
  # fuser -k /dev/video* 2>/dev/null || true

  # UVC Hack installieren (build + copy)
  bash <(curl -sL get.autodarts.io/uvc)

  # Dein .ko.xz Problem fixen (System lädt .ko.xz)
  KVER="$(uname -r)"
  MODDIR="/lib/modules/${KVER}/kernel/drivers/media/usb/uvc"

  if [[ -f "${MODDIR}/uvcvideo.ko" && -f "${MODDIR}/uvcvideo.ko.xz" ]]; then
    echo "Rebuilding uvcvideo.ko.xz from uvcvideo.ko ..."
    xz -T0 -f -k "${MODDIR}/uvcvideo.ko"
    depmod -a "${KVER}"
  fi

  # Treiber reloaden (klappt jetzt eher, weil Autodarts gestoppt ist)
  modprobe -r uvcvideo 2>/dev/null || true
  modprobe uvcvideo 2>/dev/null || true

  # Autodarts wieder starten, falls vorher aktiv
  if [[ "$WAS_ACTIVE" -eq 1 ]]; then
    echo "Starting $AD_SERVICE ..."
    systemctl start "$AD_SERVICE" || true
  fi

  exit 0
'
#
# AUS!
# Kernel update stop update, damit der geflashte kamera kernerl treiber uvc hack
# nicht ueberschreiben wird. bei jedem mal wenn es gemacht werden soll aktiv sein soll
# muss man das aktuelle datum reinschreiben
run_once "Kernel_update_stop_16.02" '
  apt-mark unhold raspi-firmware 2>/dev/null || true
  dpkg -l | awk "/^ii  linux-(image|headers)-rpi/ {print \$2}" | xargs -r apt-mark unhold 2>/dev/null || true
  exit 0
'
#
# EIN!
# uppdate einschalten, muss aber wieder ausgeschaltet werden, entweder EIn oder Aus auskommtieren
#run_once "Kernel_hold_2026-07-06_close" '
#  apt-mark hold raspi-firmware 2>/dev/null || true
#  dpkg -l | awk "/^ii  linux-(image|headers)-rpi/ {print \$2}" | xargs -r apt-mark hold 2>/dev/null || true
#  exit 0
'

log "===== Webpanel Update OK ====="
echo "OK"
