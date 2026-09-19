#!/usr/bin/env bash
# BUILD: WEBPANEL-UPDATER-DESKTOP-V2-CALLERTOGGLE-UPDATEFALLBACK-20260902-01
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
MODE="${1:-update}"

UVC_KERNEL="$(uname -r)"
UVC_MODDIR="/lib/modules/${UVC_KERNEL}/kernel/drivers/media/usb/uvc"
UVC_BACKUP_ROOT="${STATE_DIR}/uvc-backup"
UVC_BACKUP_DIR="${UVC_BACKUP_ROOT}/${UVC_KERNEL}"
UVC_MARKER="${STATE_DIR}/once-uvc-hack-${UVC_KERNEL}.done"

WEBPANEL_ZIP_NAME="webpanel.zip"
WEBPANEL_ZIP_URL="${BASE_URL}/${WEBPANEL_ZIP_NAME}"

# Dateien die einzeln aktualisiert werden sollen: "RemoteName|LocalPath"
# WICHTIG: Fehlt eine Datei im Repo -> wird geskippt (kein Abbruch).
# autodarts-web.py ist absichtlich NICHT mehr dabei, weil diese Datei nur noch aus webpanel.zip kommt.
FILES=(
  "autodarts-button-led.py|${BIN_DIR}/autodarts-button-led.py"
  "autodarts-extensions-update.sh|${BIN_DIR}/autodarts-extensions-update.sh"
  "autodarts-extensions-v2-install.sh|${BIN_DIR}/autodarts-extensions-v2-install.sh"
  "autodarts-caller-auth-reset.sh|${BIN_DIR}/autodarts-caller-auth-reset.sh"
  "Autodarts_install_manual.pdf|${DATA_DIR}/Autodarts_install_manual.pdf"
  "GPIO_Setup.jpeg|${DATA_DIR}/GPIO_Setup.jpeg"
  "Autodarts_Installationshandbuch_v2.docx|${DATA_DIR}/Autodarts_Installationshandbuch_v2.docx"
  "version.txt|${LOCAL_VER_FILE}"
  "fix_ap_internet_sharing_v3.sh|${BIN_DIR}/autodarts-ap-internet-fix.sh"
  # OPTIONAL: Desktop-Migration + Wallpaper. Fehlen sie im Repo -> sauberer Skip.
  "autodarts-desktop-migration.sh|${BIN_DIR}/autodarts-desktop-migration.sh"
  "Wallpaper.png|${DATA_DIR}/Wallpaper.png"
  "autodarts.png|${DATA_DIR}/autodarts.png"
  # OPTIONAL: Updater selbst (wenn nicht vorhanden -> skip)
  "autodarts-webpanel-update.sh|${BIN_DIR}/autodarts-webpanel-update.sh"
)

ts() { date +"[%Y-%m-%d %H:%M:%S]"; }
log(){ echo "$(ts) $*" | tee -a "${LOG_FILE}" >/dev/null; }


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

normalize_text_file() {
  local f="$1"
  sed -i 's/\r$//' "$f" 2>/dev/null || true
  sed -i '1s/^\xEF\xBB\xBF//' "$f" 2>/dev/null || true
}

is_text_ext() {
  case "$1" in
    *.sh|*.py|*.service|*.txt|*.json|*.yml|*.yaml|*.md|*.conf|*.cfg|*.html|*.css|*.js) return 0 ;;
    *) return 1 ;;
  esac
}

version_gt() {
  local a="${1:-}"
  local b="${2:-}"

  [[ -n "$a" ]] || return 1
  [[ -n "$b" ]] || return 0
  [[ "$a" == "$b" ]] && return 1

  local highest
  highest="$(printf '%s\n%s\n' "$a" "$b" | sort -V | tail -n1)"
  [[ "$highest" == "$a" ]]
}

backup_path() {
  local path="$1"
  if [[ -e "$path" ]]; then
    cp -a "$path" "${path}.bak.$(date +%Y%m%d_%H%M%S)" || true
  fi
}

extract_zip_with_python() {
  local zip_file="$1"
  local dest_dir="$2"

  python3 - "$zip_file" "$dest_dir" >>"${LOG_FILE}" 2>&1 <<'PY'
import os
import sys
import zipfile

zip_file = sys.argv[1]
dest_dir = sys.argv[2]

os.makedirs(dest_dir, exist_ok=True)
with zipfile.ZipFile(zip_file, 'r') as zf:
    zf.extractall(dest_dir)
PY
}

extract_zip() {
  local zip_file="$1"
  local dest_dir="$2"

  if command -v unzip >/dev/null 2>&1; then
    unzip -oq "$zip_file" -d "$dest_dir" >>"${LOG_FILE}" 2>&1
    return $?
  fi

  if command -v python3 >/dev/null 2>&1; then
    extract_zip_with_python "$zip_file" "$dest_dir"
    return $?
  fi

  log "ERROR: Weder unzip noch python3 verfügbar - ZIP kann nicht entpackt werden"
  return 1
}



run_optional_desktop_migration_if_downloaded() {
  local remote_name="autodarts-desktop-migration.sh"
  local migration_script="${BIN_DIR}/${remote_name}"

  # WICHTIG: Nur ausführen, wenn der Hook in DIESEM Update wirklich von GitHub
  # geladen wurde. Eine alte lokal liegengebliebene Version wird nicht ausgeführt,
  # wenn die Datei später aus dem Repo entfernt wurde oder der Download fehlschlug.
  if [[ -z "${DOWNLOADED[${remote_name}]+x}" ]]; then
    log "INFO: Optionaler Desktop-Migrations-Hook nicht geladen -> skip"
    return 0
  fi

  if [[ ! -f "$migration_script" ]]; then
    log "WARN: Desktop-Migrations-Hook wurde geladen, fehlt aber lokal -> skip"
    return 0
  fi

  chmod 755 "$migration_script" 2>/dev/null || true
  chmod 644 "${DATA_DIR}/Wallpaper.png" 2>/dev/null || true
  chmod 644 "${DATA_DIR}/autodarts.png" 2>/dev/null || true

  log "Starte optionalen Desktop-Migrations-Hook: ${migration_script}"
  if bash "$migration_script" >>"${LOG_FILE}" 2>&1; then
    log "OK: Optionaler Desktop-Migrations-Hook abgeschlossen"
  else
    local rc=$?
    # Der Hook besitzt seinen eigenen Rollback. Ein Fehler dort soll das eigentliche
    # Webpanel-Update nicht zerstören; er bleibt aber deutlich im Update-Log sichtbar.
    log "WARN: Desktop-Migrations-Hook meldete Fehler (exit=${rc}) -> Webpanel-Update läuft weiter"
  fi

  return 0
}

install_caller_boot_stabilize() {
  local drop_in_dir="/etc/systemd/system/darts-caller.service.d"
  local conf_file="${drop_in_dir}/boot-stabilize.conf"

  if ! systemctl cat darts-caller.service >/dev/null 2>&1; then
    log "INFO: darts-caller.service nicht vorhanden -> boot-stabilize Drop-in skip"
    return 0
  fi

  mkdir -p "$drop_in_dir"
  cat > "$conf_file" <<'DROPIN'
[Unit]
Wants=network-online.target autodarts.service
After=network-online.target autodarts.service

[Service]
ExecStartPre=/bin/sleep 12
RestartSec=8
DROPIN
  chmod 644 "$conf_file" 2>/dev/null || true
  systemctl daemon-reload 2>/dev/null || true
  log "OK: darts-caller boot-stabilize Drop-in installiert: ${conf_file}"
}

run_ap_internet_fix_if_present() {
  local fix_script="${BIN_DIR}/autodarts-ap-internet-fix.sh"
  if [[ ! -f "$fix_script" ]]; then
    log "INFO: Kein AP-Internet-Fix-Script vorhanden -> skip"
    return 0
  fi
  chmod +x "$fix_script" 2>/dev/null || true
  log "Starte AP-Internet-Fix-Script: $fix_script"
  if AP_IF="wlan_ap" UPLINK_IFS="wlan0 eth0" bash "$fix_script" >>"${LOG_FILE}" 2>&1; then
    log "OK: AP-Internet-Fix-Script erfolgreich ausgeführt"
  else
    local rc=$?
    log "WARN: AP-Internet-Fix-Script meldete Fehler (exit=$rc) -> Update läuft weiter"
  fi
  return 0
}


install_autodarts_update_fallback() {
  # Schlanker Fallback fuer Boardmanager-Updates:
  # - get.autodarts.io bleibt bevorzugt, weil aktuell offiziell.
  # - get.autodarts.com wird nur genutzt, wenn der gleiche Detection-Pfad wirklich gueltiges JSON liefert.
  # - Wenn beide Quellen nicht gehen, wird nichts zerstoert und der alte Boardmanager laeuft weiter.
  local wrapper="${BIN_DIR}/autodarts-safe-updater.sh"
  local override_dir="/etc/systemd/system/autodartsupdater.service.d"
  local override_file="${override_dir}/override.conf"

  cat >"$wrapper" <<'EOF'
#!/usr/bin/env bash
# BUILD: AUTODARTS-SAFE-UPDATER-20260902-01
set -Eeuo pipefail

LOG="/var/log/autodarts_safe_updater.log"
CHANNEL="${1:-latest}"
PLATFORM="linux"
SERVICE="autodarts.service"

mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1

log() { echo "[$(date +'%F %T')] $*"; }

arch_norm() {
  case "$(uname -m)" in
    x86_64|amd64) echo "amd64" ;;
    aarch64|arm64) echo "arm64" ;;
    armv7l) echo "armv7l" ;;
    *) uname -m ;;
  esac
}

service_user() {
  local u
  u="$(systemctl show "$SERVICE" -p User --value 2>/dev/null || true)"
  [[ -n "$u" ]] && echo "$u" || echo "$(id -un)"
}

install_dir() {
  local exec_start path
  exec_start="$(systemctl show "$SERVICE" -p ExecStart --value 2>/dev/null || true)"
  path="$(printf '%s\n' "$exec_start" | sed -nE 's/.*path=([^ ;]+).*/\1/p' | head -n1)"
  if [[ -z "$path" ]]; then
    path="$(systemctl cat "$SERVICE" 2>/dev/null | sed -nE 's/^ExecStart=([^[:space:]]+).*/\1/p' | head -n1)"
  fi
  if [[ -n "$path" ]]; then
    dirname "$path"
  else
    echo "$HOME/.local/opt/autodarts"
  fi
}

local_version() {
  local dir bin out
  dir="$(install_dir)"
  bin="${dir}/autodarts"
  [[ -x "$bin" ]] || return 0
  out="$($bin --version 2>&1 || $bin -V 2>&1 || true)"
  printf '%s\n' "$out" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+(-beta\.[0-9]+)?' | head -n1 || true
}

version_gt() {
  local a="${1#v}" b="${2#v}"
  [[ -n "$a" ]] || return 1
  [[ -n "$b" ]] || return 0
  [[ "$a" == "$b" ]] && return 1
  [[ "$(printf '%s\n%s\n' "$a" "$b" | sort -V | tail -n1)" == "$a" ]]
}

fetch_meta() {
  local base="$1" arch="$2" out="$3" url
  url="${base}/detection/${CHANNEL}/${PLATFORM}/${arch}/RELEASES.json"
  curl -fsSL --connect-timeout 5 --max-time 15 "$url" -o "$out" || return 1
  python3 - "$out" >/dev/null <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    d=json.load(f)
assert isinstance(d.get("currentVersion"), str) and d["currentVersion"].startswith("v")
assert isinstance(d.get("releases"), list) and d["releases"]
PY
}

pick_source() {
  local arch tmp base
  arch="$(arch_norm)"
  for base in "https://get.autodarts.io" "https://get.autodarts.com"; do
    tmp="$(mktemp)"
    if fetch_meta "$base" "$arch" "$tmp"; then
      echo "${base}|${tmp}|${arch}"
      return 0
    fi
    rm -f "$tmp"
  done
  return 1
}

run_installer() {
  local base="$1" latest="$2" tmp_inst args=()
  tmp_inst="$(mktemp)"
  if ! curl -fsSL --connect-timeout 5 --max-time 20 "$base" -o "$tmp_inst"; then
    log "FEHLER: Installer nicht erreichbar: $base"
    rm -f "$tmp_inst"
    return 1
  fi
  if ! head -n 1 "$tmp_inst" | grep -q '^#!' || ! grep -q 'INSTALL_DIR' "$tmp_inst"; then
    log "FEHLER: Installer bei $base sieht ungueltig aus."
    rm -f "$tmp_inst"
    return 1
  fi
  chmod +x "$tmp_inst"
  log "Nutze Installer: $base"
  # Ohne Versionsangabe installiert der offizielle Installer die aktuelle Version und erneuert updater.sh.
  bash "$tmp_inst"
  rm -f "$tmp_inst"
}

log "===== Autodarts Safe Updater START ====="
SRC="$(pick_source)" || { log "Keine gueltige Updatequelle erreichbar (.io/.com)."; exit 0; }
BASE="${SRC%%|*}"
REST="${SRC#*|}"
JSON_FILE="${REST%%|*}"
ARCH="${REST##*|}"
LATEST="$(python3 - "$JSON_FILE" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    d=json.load(f)
print(d["currentVersion"].lstrip("v"))
PY
)"
LOCAL="$(local_version || true)"
rm -f "$JSON_FILE"
log "Quelle: ${BASE}"
log "Arch:   ${ARCH}"
log "Lokal:  ${LOCAL:-unknown}"
log "Online: ${LATEST:-unknown}"

if [[ -n "$LOCAL" ]] && ! version_gt "$LATEST" "$LOCAL"; then
  log "Kein Boardmanager-Update noetig."
  exit 0
fi

run_installer "$BASE" "$LATEST" || { log "Updateversuch fehlgeschlagen; bestehende Installation bleibt unveraendert."; exit 0; }
log "Autodarts Safe Updater fertig."
EOF
  chmod 755 "$wrapper" 2>/dev/null || true
  log "OK: Autodarts Safe Updater installiert: $wrapper"

  # Nur wenn der offizielle Auto-Updater-Service existiert, per Drop-in auf den sicheren Wrapper umleiten.
  # Das aktiviert den Service NICHT, falls er bewusst deaktiviert ist.
  if systemctl status autodartsupdater.service >/dev/null 2>&1 || [[ -f /etc/systemd/system/autodartsupdater.service ]]; then
    mkdir -p "$override_dir"
    cat >"$override_file" <<EOF
[Service]
ExecStart=
ExecStart=${wrapper}
EOF
    chmod 644 "$override_file" 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true
    log "OK: autodartsupdater.service nutzt nun den Safe-Updater per Drop-in."
  else
    log "INFO: autodartsupdater.service nicht vorhanden -> Safe-Updater nur abgelegt, kein Drop-in."
  fi
}

run_extensions_service_repair_if_present() {
  # Installiert/aktualisiert den gemeinsamen Caller/WLED-Watchdog aus dem frisch geladenen Extensions-Updater.
  local ext_script="${BIN_DIR}/autodarts-extensions-update.sh"
  local flag="/var/lib/autodarts/config/extensions-v2-installed.json"

  if [[ ! -f "$ext_script" ]]; then
    log "INFO: Extensions-Updater nicht vorhanden -> Caller/WLED-Service-Reparatur skip"
    return 0
  fi
  if [[ ! -f "$flag" ]] || ! grep -q '"installed"[[:space:]]*:[[:space:]]*true' "$flag"; then
    log "INFO: Caller/WLED V2 ist nicht als installiert markiert -> Service-Reparatur skip"
    return 0
  fi

  chmod +x "$ext_script" 2>/dev/null || true
  log "Starte Caller/WLED-Service-Reparatur (installiert schlanken Health-Watchdog)"
  if bash "$ext_script" service-repair >>"${LOG_FILE}" 2>&1; then
    log "OK: Caller/WLED-Service-Reparatur abgeschlossen"
  else
    local rc=$?
    log "WARN: Caller/WLED-Service-Reparatur meldete Fehler (exit=${rc}) -> Webpanel-Update läuft weiter"
  fi
  return 0
}

install_webpanel_from_zip() {
  local zip_file="$1"
  local extract_dir="$2/webpanel_zip"
  local ts_now
  ts_now="$(date +%Y%m%d_%H%M%S)"

  log "Installiere Webpanel aus ${WEBPANEL_ZIP_NAME}"
  rm -rf "$extract_dir"
  mkdir -p "$extract_dir"

  if ! extract_zip "$zip_file" "$extract_dir"; then
    log "ERROR: ${WEBPANEL_ZIP_NAME} konnte nicht entpackt werden"
    return 1
  fi

  if [[ ! -f "${extract_dir}/autodarts-web.py" ]]; then
    log "ERROR: ${WEBPANEL_ZIP_NAME} enthält keine autodarts-web.py"
    return 1
  fi

  mkdir -p "${BIN_DIR}"

  if [[ -f "${BIN_DIR}/autodarts-web.py" ]]; then
    cp -a "${BIN_DIR}/autodarts-web.py" "${BIN_DIR}/autodarts-web.py.bak.${ts_now}" || true
  fi
  install -m 644 "${extract_dir}/autodarts-web.py" "${BIN_DIR}/autodarts-web.py"
  chmod 777 "${BIN_DIR}/autodarts-web.py" || true

  for dir_name in templates static theme; do
    if [[ -d "${extract_dir}/${dir_name}" ]]; then
      if [[ -e "${BIN_DIR}/${dir_name}" ]]; then
        cp -a "${BIN_DIR}/${dir_name}" "${BIN_DIR}/${dir_name}.bak.${ts_now}" || true
        rm -rf "${BIN_DIR:?}/${dir_name}"
      fi
      mkdir -p "${BIN_DIR}/${dir_name}"
      cp -a "${extract_dir}/${dir_name}/." "${BIN_DIR}/${dir_name}/"
      chmod -R 777 "${BIN_DIR}/${dir_name}" || true
      log "OK: ${dir_name}/ aus ZIP ersetzt"
    else
      log "INFO: ${dir_name}/ nicht in ${WEBPANEL_ZIP_NAME} enthalten -> skip"
    fi
  done

  return 0
}

uvc_backup_exists() {
  [[ -f "${UVC_BACKUP_DIR}/uvcvideo.ko" || -f "${UVC_BACKUP_DIR}/uvcvideo.ko.xz" ]]
}

write_uvc_backup_manifest() {
  mkdir -p "${UVC_BACKUP_DIR}"
  local files=""
  files="$(find "${UVC_BACKUP_DIR}" -maxdepth 1 -type f -printf '%f ' 2>/dev/null || true)"
  cat > "${UVC_BACKUP_DIR}/manifest.txt" <<EOF2
kernel=${UVC_KERNEL}
created_at=$(date +"%Y-%m-%d %H:%M:%S")
source_dir=${UVC_MODDIR}
files=${files}
EOF2
}

create_uvc_backup_if_safe() {
  mkdir -p "${UVC_BACKUP_ROOT}"

  if uvc_backup_exists; then
    log "UVC backup already exists: ${UVC_BACKUP_DIR}"
    write_uvc_backup_manifest
    return 0
  fi

  if [[ -f "${UVC_MARKER}" ]]; then
    log "SAFE-ABORT: Kein Original-Backup vorhanden, aber alter UVC-Marker gefunden: ${UVC_MARKER}"
    log "SAFE-ABORT: Bitte Original-Dateien nach ${UVC_BACKUP_DIR} kopieren, bevor der UVC-Hack erneut installiert oder deinstalliert wird."
    return 1
  fi

  if [[ ! -f "${UVC_MODDIR}/uvcvideo.ko" && ! -f "${UVC_MODDIR}/uvcvideo.ko.xz" ]]; then
    log "SAFE-ABORT: Keine uvcvideo.ko / uvcvideo.ko.xz gefunden unter ${UVC_MODDIR}"
    return 1
  fi

  mkdir -p "${UVC_BACKUP_DIR}"
  if [[ -f "${UVC_MODDIR}/uvcvideo.ko" ]]; then
    cp -a "${UVC_MODDIR}/uvcvideo.ko" "${UVC_BACKUP_DIR}/uvcvideo.ko"
  fi
  if [[ -f "${UVC_MODDIR}/uvcvideo.ko.xz" ]]; then
    cp -a "${UVC_MODDIR}/uvcvideo.ko.xz" "${UVC_BACKUP_DIR}/uvcvideo.ko.xz"
  fi

  write_uvc_backup_manifest
  log "UVC original backup created: ${UVC_BACKUP_DIR}"
  return 0
}

restore_uvc_from_backup() {
  if ! uvc_backup_exists; then
    log "SAFE-ABORT: Kein lokales Original-Backup gefunden: ${UVC_BACKUP_DIR}"
    return 1
  fi

  mkdir -p "${UVC_MODDIR}"

  if [[ -f "${UVC_BACKUP_DIR}/uvcvideo.ko" ]]; then
    install -m 644 "${UVC_BACKUP_DIR}/uvcvideo.ko" "${UVC_MODDIR}/uvcvideo.ko"
  else
    rm -f "${UVC_MODDIR}/uvcvideo.ko" 2>/dev/null || true
  fi

  if [[ -f "${UVC_BACKUP_DIR}/uvcvideo.ko.xz" ]]; then
    install -m 644 "${UVC_BACKUP_DIR}/uvcvideo.ko.xz" "${UVC_MODDIR}/uvcvideo.ko.xz"
  elif [[ -f "${UVC_BACKUP_DIR}/uvcvideo.ko" ]]; then
    rm -f "${UVC_MODDIR}/uvcvideo.ko.xz" 2>/dev/null || true
    xz -T0 -f -k "${UVC_MODDIR}/uvcvideo.ko" >>"${LOG_FILE}" 2>&1 || true
  else
    rm -f "${UVC_MODDIR}/uvcvideo.ko.xz" 2>/dev/null || true
  fi

  depmod -a "${UVC_KERNEL}" >>"${LOG_FILE}" 2>&1 || true
  write_uvc_backup_manifest
  return 0
}

install_uvc_hack() {
  log "===== UVC Hack START ====="

  if ! create_uvc_backup_if_safe; then
    log "===== UVC Hack ABORT ====="
    echo "NO_BACKUP"
    return 1
  fi

  run_once "uvc-hack-$(uname -r)" '
    set +e

    AD_SERVICE="autodarts.service"
    WAS_ACTIVE=0

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

    bash <(curl -sL get.autodarts.io/uvc)

    KVER="$(uname -r)"
    MODDIR="/lib/modules/${KVER}/kernel/drivers/media/usb/uvc"

    if [[ -f "${MODDIR}/uvcvideo.ko" && -f "${MODDIR}/uvcvideo.ko.xz" ]]; then
      echo "Rebuilding uvcvideo.ko.xz from uvcvideo.ko ..."
      xz -T0 -f -k "${MODDIR}/uvcvideo.ko"
      depmod -a "${KVER}"
    fi

    modprobe -r uvcvideo 2>/dev/null || true
    modprobe uvcvideo 2>/dev/null || true

    if [[ "$WAS_ACTIVE" -eq 1 ]]; then
      echo "Starting $AD_SERVICE ..."
      systemctl start "$AD_SERVICE" || true
    fi

    exit 0
  '

  run_once "Kernel_hold_2026-07-06_off" '
    apt-mark hold raspi-firmware 2>/dev/null || true
    dpkg -l | awk "/^ii  linux-(image|headers)-rpi/ {print \$2}" | xargs -r apt-mark hold 2>/dev/null || true
    exit 0
  '

  log "===== UVC Hack OK ====="
  echo "OK"
}

uninstall_uvc_hack() {
  log "===== UVC Hack UNINSTALL START ====="

  if ! uvc_backup_exists; then
    log "SAFE-ABORT: Kein lokales Original-Backup vorhanden: ${UVC_BACKUP_DIR}"
    echo "NO_BACKUP"
    return 1
  fi

  set +e

  systemctl stop autodarts.service || true
  pkill -f mjpg_streamer 2>/dev/null || true
  fuser -k /dev/video* 2>/dev/null || true
  modprobe -r uvcvideo 2>/dev/null || true

  if ! restore_uvc_from_backup; then
    log "SAFE-ABORT: Restore aus Backup fehlgeschlagen."
    echo "NO_BACKUP"
    return 1
  fi

  modprobe uvcvideo 2>/dev/null || true
  rm -f "${UVC_MARKER}" 2>/dev/null || true

  log "===== UVC Hack UNINSTALL OK ====="
  log "Schedule reboot system"

  nohup bash -lc 'sleep 2; reboot' >/dev/null 2>&1 &

  echo "OK"
}

LOCK="/run/autodarts-webpanel-update.lock"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"
  flock -n 9 || { log "Update läuft bereits (lock: $LOCK)."; echo "BUSY"; exit 0; }
fi

log "===== Webpanel Update START ====="

if [[ "${MODE}" == "--uvc-hack" || "${MODE}" == "uvc-hack" ]]; then
  install_uvc_hack
  exit 0
fi

if [[ "${MODE}" == "--uvc-uninstall" || "${MODE}" == "uvc-uninstall" ]]; then
  uninstall_uvc_hack
  exit 0
fi

REMOTE_VER="$(curl -sSL "${BASE_URL}/version.txt" 2>/dev/null | tr -d '\r\n' || true)"
LOCAL_VER="$(cat "${LOCAL_VER_FILE}" 2>/dev/null | tr -d '\r\n' || true)"
log "Local:  ${LOCAL_VER:-unknown}"
log "Remote: ${REMOTE_VER:-unknown}"

if [[ -z "${REMOTE_VER}" ]]; then
  log "ERROR: Remote version.txt konnte nicht gelesen werden"
  exit 1
fi

if [[ "${FORCE}" != "1" ]] && [[ -n "${LOCAL_VER}" ]] && ! version_gt "${REMOTE_VER}" "${LOCAL_VER}"; then
  log "Kein Update nötig (lokal aktuell oder neuer)."
  echo "UP_TO_DATE"
  exit 0
fi

TMP_DIR="$(mktemp -d)"
cleanup(){ rm -rf "${TMP_DIR}"; }
trap cleanup EXIT

declare -A DOWNLOADED=()
UPDATED_ANY=0

# 1) Einzeldateien laden
for entry in "${FILES[@]}"; do
  IFS="|" read -r src dst <<< "${entry}"
  url="${BASE_URL}/${src}"
  out="${TMP_DIR}/${src}"

  rm -f "${out}" 2>/dev/null || true
  log "Download: ${src}"
  http_code="$(curl -sSL --retry 2 --connect-timeout 5 --max-time 60 -o "${out}" -w "%{http_code}" "${url}" || true)"

  case "${http_code}" in
    200)
      if [[ ! -s "${out}" ]]; then
        log "WARN: ${src} wurde leer geladen -> skip"
        rm -f "${out}" || true
        continue
      fi

      if is_text_ext "${src}"; then
        normalize_text_file "${out}"
      fi

      DOWNLOADED["${src}"]=1
      log "OK: ${src} geladen"
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

# 2) webpanel.zip optional laden und installieren
WEBPANEL_ZIP_FILE="${TMP_DIR}/${WEBPANEL_ZIP_NAME}"
rm -f "${WEBPANEL_ZIP_FILE}" 2>/dev/null || true
log "Prüfe optionales Paket: ${WEBPANEL_ZIP_NAME}"
ZIP_HTTP_CODE="$(curl -sSL --retry 2 --connect-timeout 5 --max-time 120 -o "${WEBPANEL_ZIP_FILE}" -w "%{http_code}" "${WEBPANEL_ZIP_URL}" || true)"

case "${ZIP_HTTP_CODE}" in
  200)
    if [[ -s "${WEBPANEL_ZIP_FILE}" ]]; then
      if install_webpanel_from_zip "${WEBPANEL_ZIP_FILE}" "${TMP_DIR}"; then
        UPDATED_ANY=1
        log "OK: ${WEBPANEL_ZIP_NAME} installiert"
      else
        log "ERROR: Installation aus ${WEBPANEL_ZIP_NAME} fehlgeschlagen"
        exit 1
      fi
    else
      log "WARN: ${WEBPANEL_ZIP_NAME} wurde leer geladen -> skip"
    fi
    ;;
  404)
    log "INFO: ${WEBPANEL_ZIP_NAME} nicht im Repo -> skip"
    ;;
  000|"")
    log "WARN: ${WEBPANEL_ZIP_NAME} Download-Problem (network/timeout?) -> skip"
    ;;
  *)
    log "WARN: ${WEBPANEL_ZIP_NAME} HTTP=${ZIP_HTTP_CODE} -> skip"
    ;;
esac

# 3) Einzeldateien ersetzen
for entry in "${FILES[@]}"; do
  IFS="|" read -r src dst <<< "${entry}"

  if [[ -z "${DOWNLOADED[${src}]+x}" ]]; then
    continue
  fi

  mkdir -p "$(dirname "${dst}")"
  backup_path "${dst}"
  install -m 644 "${TMP_DIR}/${src}" "${dst}"
  chmod 777 "${dst}" || true
  UPDATED_ANY=1

done

chmod 777 "${BIN_DIR}" "${DATA_DIR}" "${STATE_DIR}" 2>/dev/null || true

# 4) Optionalen Migrations-Hook nur dann ausführen, wenn er in diesem Update
#    tatsächlich von GitHub geladen wurde. Fehlt die Datei -> keinerlei Änderung.
run_optional_desktop_migration_if_downloaded
install_autodarts_update_fallback
run_extensions_service_repair_if_present
install_caller_boot_stabilize

if [[ "${UPDATED_ANY}" != "1" ]]; then
  log "Kein Updatepaket installiert."
  echo "NOTHING_UPDATED"
  exit 0
fi

log "Restart ${WEB_SERVICE}"
systemctl restart "${WEB_SERVICE}" || {
  log "ERROR: systemctl restart failed"
  exit 1
}


run_ap_internet_fix_if_present

run_once "Kernel_hold_2026-07-06_off" '
  apt-mark hold raspi-firmware 2>/dev/null || true
  dpkg -l | awk "/^ii  linux-(image|headers)-rpi/ {print \$2}" | xargs -r apt-mark hold 2>/dev/null || true
  exit 0
'

log "===== Webpanel Update OK ====="
echo "OK"
