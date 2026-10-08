#!/usr/bin/env bash
# BUILD: WEBPANEL-UPDATER-SLIM-20261008-03
set -euo pipefail

REPO="jumbo1250/Autodarts-Webinterface-Installation"
GITHUB_RAW="https://raw.githubusercontent.com/${REPO}/main"

# Kanal: "stable" (Standard) oder "beta" (kein Versionscheck, immer aktuell)
CHANNEL="${AUTODARTS_WEBPANEL_CHANNEL:-stable}"
if [[ "$CHANNEL" == "beta" ]]; then
  BASE_URL="${GITHUB_RAW}/Beta"
else
  BASE_URL="${GITHUB_RAW}/latest"
fi

BIN_DIR="/usr/local/bin"
DATA_DIR="/home/peter/autodarts-data"
STATE_DIR="/var/lib/autodarts"
LOG_FILE="/var/log/autodarts_webpanel_update.log"
WEB_SERVICE="autodarts-web.service"

mkdir -p "${DATA_DIR}" "${STATE_DIR}"

LOCAL_VER_FILE="${STATE_DIR}/webpanel-version.txt"
FORCE="${FORCE:-0}"
[[ "$CHANNEL" == "beta" ]] && FORCE=1
MODE="${1:-update}"

WEBPANEL_ZIP_NAME="webpanel.zip"
WEBPANEL_ZIP_URL="${BASE_URL}/${WEBPANEL_ZIP_NAME}"

# Dateien die einzeln aktualisiert werden: "RemoteName|LocalPath"
# Fehlt eine Datei im Repo -> wird geskippt (kein Abbruch).
FILES=(
  "autodarts-button-led.py|${BIN_DIR}/autodarts-button-led.py"
  "autodarts-extensions-update.sh|${BIN_DIR}/autodarts-extensions-update.sh"
  "autodarts-caller-auth-reset.sh|${BIN_DIR}/autodarts-caller-auth-reset.sh"
  "version.txt|${LOCAL_VER_FILE}"
  "autodarts-webpanel-update.sh|${BIN_DIR}/autodarts-webpanel-update.sh"
)

ts()  { date +"[%Y-%m-%d %H:%M:%S]"; }
log() { echo "$(ts) $*" | tee -a "${LOG_FILE}" >/dev/null; }

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
  local a="${1:-}" b="${2:-}"
  [[ -n "$a" ]] || return 1
  [[ -n "$b" ]] || return 0
  [[ "$a" == "$b" ]] && return 1
  [[ "$(printf '%s\n%s\n' "$a" "$b" | sort -V | tail -n1)" == "$a" ]]
}

backup_path() {
  local path="$1"
  [[ -e "$path" ]] && cp -a "$path" "${path}.bak.$(date +%Y%m%d_%H%M%S)" || true
}

extract_zip_with_python() {
  python3 - "$1" "$2" >>"${LOG_FILE}" 2>&1 <<'PY'
import os, sys, zipfile
os.makedirs(sys.argv[2], exist_ok=True)
with zipfile.ZipFile(sys.argv[1], 'r') as zf:
    zf.extractall(sys.argv[2])
PY
}

extract_zip() {
  if command -v unzip >/dev/null 2>&1; then
    unzip -oq "$1" -d "$2" >>"${LOG_FILE}" 2>&1 && return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    extract_zip_with_python "$1" "$2" && return 0
  fi
  log "ERROR: Weder unzip noch python3 verfügbar"
  return 1
}

install_webpanel_from_zip() {
  local zip_file="$1" extract_dir="$2/webpanel_zip" ts_now
  ts_now="$(date +%Y%m%d_%H%M%S)"

  log "Installiere Webpanel aus ${WEBPANEL_ZIP_NAME}"
  rm -rf "$extract_dir"; mkdir -p "$extract_dir"

  if ! extract_zip "$zip_file" "$extract_dir"; then
    log "ERROR: ${WEBPANEL_ZIP_NAME} konnte nicht entpackt werden"; return 1
  fi
  if [[ ! -f "${extract_dir}/autodarts-web.py" ]]; then
    log "ERROR: ${WEBPANEL_ZIP_NAME} enthält keine autodarts-web.py"; return 1
  fi

  mkdir -p "${BIN_DIR}"
  [[ -f "${BIN_DIR}/autodarts-web.py" ]] && cp -a "${BIN_DIR}/autodarts-web.py" "${BIN_DIR}/autodarts-web.py.bak.${ts_now}" || true
  install -m 644 "${extract_dir}/autodarts-web.py" "${BIN_DIR}/autodarts-web.py"
  chmod 777 "${BIN_DIR}/autodarts-web.py" || true

  for dir_name in templates static theme configs; do
    if [[ -d "${extract_dir}/${dir_name}" ]]; then
      [[ -e "${BIN_DIR}/${dir_name}" ]] && { cp -a "${BIN_DIR}/${dir_name}" "${BIN_DIR}/${dir_name}.bak.${ts_now}" || true; rm -rf "${BIN_DIR:?}/${dir_name}"; }
      mkdir -p "${BIN_DIR}/${dir_name}"
      cp -a "${extract_dir}/${dir_name}/." "${BIN_DIR}/${dir_name}/"
      chmod -R 777 "${BIN_DIR}/${dir_name}" || true
      log "OK: ${dir_name}/ aus ZIP installiert"
    fi
  done
}

run_revision_chain() {
  # Beta-Kanal: Revisionskette überspringen
  [[ "$CHANNEL" == "beta" ]] && return 0

  local conf_url="${GITHUB_RAW}/latest/update-revisions.conf"
  local conf_tmp
  conf_tmp="$(mktemp)"

  local http_code
  http_code="$(curl -sSL --connect-timeout 5 --max-time 30 -o "$conf_tmp" -w "%{http_code}" "$conf_url" || true)"
  if [[ "$http_code" != "200" ]] || [[ ! -s "$conf_tmp" ]]; then
    log "INFO: update-revisions.conf nicht erreichbar (HTTP=$http_code) -> Revisionskette skip"
    rm -f "$conf_tmp"; return 0
  fi

  # shellcheck source=/dev/null
  source "$conf_tmp"; rm -f "$conf_tmp"

  if [[ -z "${UPDATE_REVISIONS:-}" ]]; then
    log "INFO: UPDATE_REVISIONS nicht definiert -> Revisionskette skip"; return 0
  fi

  IFS=';' read -ra REVS <<< "$UPDATE_REVISIONS"
  local local_ver
  local_ver="$(cat "${LOCAL_VER_FILE}" 2>/dev/null | tr -d '\r\n' || true)"

  log "===== Installierte Version: ${local_ver:-unknown} | Update-Kette: ${UPDATE_REVISIONS} ====="

  for rev in "${REVS[@]}"; do
    [[ "$rev" == "latest" ]] && continue

    if [[ -n "$local_ver" ]] && ! version_gt "$rev" "$local_ver"; then
      log "CHAIN: Checkpoint ${rev} -> skip (lokal ${local_ver} >= ${rev})"
      continue
    fi

    log "CHAIN: Checkpoint ${rev} -> starten"
    local cp_tmp
    cp_tmp="$(mktemp)"
    local cp_http
    cp_http="$(curl -sSL --connect-timeout 5 --max-time 60 -o "$cp_tmp" -w "%{http_code}" "${GITHUB_RAW}/${rev}/autodarts-webpanel-update.sh" || true)"

    if [[ "$cp_http" != "200" ]] || [[ ! -s "$cp_tmp" ]] || ! head -n 1 "$cp_tmp" | grep -q '^#!'; then
      log "WARN: Checkpoint ${rev} Script nicht verfügbar (HTTP=$cp_http) -> skip"
      rm -f "$cp_tmp"; continue
    fi

    sed -i "s|BASE_URL=.*|BASE_URL=\"${GITHUB_RAW}/${rev}\"|" "$cp_tmp"
    chmod +x "$cp_tmp"
    log "CHAIN: Führe Checkpoint ${rev} aus..."
    if LOG_FILE="${LOG_FILE}" bash "$cp_tmp" >>"${LOG_FILE}" 2>&1; then
      log "CHAIN: Checkpoint ${rev} -> OK"
    else
      log "WARN: Checkpoint ${rev} meldete Fehler (exit=$?) -> Kette läuft weiter"
    fi
    rm -f "$cp_tmp"

    local_ver="$(cat "${LOCAL_VER_FILE}" 2>/dev/null | tr -d '\r\n' || true)"
    log "CHAIN: Lokale Version nach Checkpoint ${rev}: ${local_ver:-unknown}"
  done

  log "===== Revisionskette abgeschlossen -> weiter mit latest ====="
}

# --- MAIN ---

LOCK="/run/autodarts-webpanel-update.lock"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"
  flock -n 9 || { log "Update läuft bereits (lock: $LOCK)."; echo "BUSY"; exit 0; }
fi

log "===== Webpanel Update START ====="

# Revisionskette: alte Systeme müssen alle Checkpoints sequenziell durchlaufen
run_revision_chain

REMOTE_VER="$(curl -sSL "${BASE_URL}/version.txt" 2>/dev/null | tr -d '\r\n' || true)"
LOCAL_VER="$(cat "${LOCAL_VER_FILE}" 2>/dev/null | tr -d '\r\n' || true)"
log "Local:  ${LOCAL_VER:-unknown}"
log "Remote: ${REMOTE_VER:-unknown}"

if [[ -z "${REMOTE_VER}" ]]; then
  log "ERROR: Remote version.txt konnte nicht gelesen werden"; exit 1
fi

if [[ "${FORCE}" != "1" ]] && [[ -n "${LOCAL_VER}" ]] && ! version_gt "${REMOTE_VER}" "${LOCAL_VER}"; then
  log "Kein Update nötig (lokal aktuell oder neuer)."
  echo "UP_TO_DATE"; exit 0
fi

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "${TMP_DIR}"; }
trap cleanup EXIT

declare -A DOWNLOADED=()
UPDATED_ANY=0

# 1) Einzeldateien laden
for entry in "${FILES[@]}"; do
  IFS="|" read -r src dst <<< "${entry}"
  local out="${TMP_DIR}/${src}"
  rm -f "${out}" 2>/dev/null || true
  log "Download: ${src}"
  http_code="$(curl -sSL --retry 2 --connect-timeout 5 --max-time 60 -o "${out}" -w "%{http_code}" "${BASE_URL}/${src}" || true)"

  case "${http_code}" in
    200)
      if [[ ! -s "${out}" ]]; then log "WARN: ${src} leer -> skip"; rm -f "${out}"; continue; fi
      is_text_ext "${src}" && normalize_text_file "${out}"
      DOWNLOADED["${src}"]=1
      log "OK: ${src} geladen"
      ;;
    404) log "MISSING: ${src} nicht im Repo -> skip"; rm -f "${out}" ;;
    *)   log "WARN: ${src} HTTP=${http_code} -> skip"; rm -f "${out}" ;;
  esac
done

# 2) webpanel.zip laden und installieren
WEBPANEL_ZIP_FILE="${TMP_DIR}/${WEBPANEL_ZIP_NAME}"
log "Prüfe ${WEBPANEL_ZIP_NAME}..."
ZIP_HTTP="$(curl -sSL --retry 2 --connect-timeout 5 --max-time 120 -o "${WEBPANEL_ZIP_FILE}" -w "%{http_code}" "${WEBPANEL_ZIP_URL}" || true)"

case "${ZIP_HTTP}" in
  200)
    if [[ -s "${WEBPANEL_ZIP_FILE}" ]]; then
      if install_webpanel_from_zip "${WEBPANEL_ZIP_FILE}" "${TMP_DIR}"; then
        UPDATED_ANY=1; log "OK: ${WEBPANEL_ZIP_NAME} installiert"
      else
        log "ERROR: Installation aus ${WEBPANEL_ZIP_NAME} fehlgeschlagen"; exit 1
      fi
    else
      log "WARN: ${WEBPANEL_ZIP_NAME} leer -> skip"
    fi
    ;;
  404) log "INFO: ${WEBPANEL_ZIP_NAME} nicht im Repo -> skip" ;;
  *)   log "WARN: ${WEBPANEL_ZIP_NAME} HTTP=${ZIP_HTTP} -> skip" ;;
esac

# 3) Einzeldateien ersetzen
for entry in "${FILES[@]}"; do
  IFS="|" read -r src dst <<< "${entry}"
  [[ -z "${DOWNLOADED[${src}]+x}" ]] && continue
  mkdir -p "$(dirname "${dst}")"
  backup_path "${dst}"
  install -m 644 "${TMP_DIR}/${src}" "${dst}"
  chmod 777 "${dst}" || true
  UPDATED_ANY=1
done

chmod 777 "${BIN_DIR}" "${DATA_DIR}" "${STATE_DIR}" 2>/dev/null || true

if [[ "${UPDATED_ANY}" != "1" ]]; then
  log "Kein Updatepaket installiert."
  echo "NOTHING_UPDATED"; exit 0
fi

log "Restart ${WEB_SERVICE}"
systemctl restart "${WEB_SERVICE}" || { log "ERROR: systemctl restart failed"; exit 1; }

log "===== Webpanel Update OK ====="
echo "OK"
