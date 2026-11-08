#!/usr/bin/env bash
# BUILD: WEBPANEL-RUNNER-20261010-01
#
# Dirigent der Webpanel-Update-Kette.
# Wird IMMER frisch aus latest/ geladen — wird nie lokal installiert.
# Orchestriert: Beta-Direkt-Update  ODER  Stable-Revisionskette.

set -euo pipefail

REPO="jumbo1250/Autodarts-Webinterface-Installation"
GITHUB_RAW="https://raw.githubusercontent.com/${REPO}/main"
CHANNEL="${AUTODARTS_WEBPANEL_CHANNEL:-stable}"
STATE_DIR="/var/lib/autodarts"
LOG_FILE="/var/log/autodarts_webpanel_update.log"
LOCAL_VER_FILE="${STATE_DIR}/webpanel-version.txt"

mkdir -p "${STATE_DIR}"
# Kein eigener flock: Flask hält bereits /var/lib/autodarts/webpanel-update.lock.
# Checkpoint-Scripts haben ihren eigenen flock — sie würden sonst BUSY melden.

ts()  { date +"[%Y-%m-%d %H:%M:%S]"; }
log() { echo "$(ts) $*" | tee -a "${LOG_FILE}" >/dev/null; }

version_gt() {
  local a="${1:-}" b="${2:-}"
  [[ -n "$a" ]] || return 1
  [[ -n "$b" ]] || return 0
  [[ "$a" == "$b" ]] && return 1
  [[ "$(printf '%s\n%s\n' "$a" "$b" | sort -V | tail -n1)" == "$a" ]]
}

# Lädt ein Checkpoint-Script, patcht BASE_URL, validiert und führt es aus.
# Gibt 0 bei Erfolg zurück, 1 bei Fehler.
fetch_and_run() {
  local label="$1"
  local url="$2"
  local base_override="$3"

  local tmp
  tmp="$(mktemp)"
  local http
  http="$(curl -sSL --retry 2 --connect-timeout 5 --max-time 90 -o "$tmp" -w "%{http_code}" "$url" || true)"

  if [[ "$http" != "200" ]] || [[ ! -s "$tmp" ]]; then
    log "RUNNER: ${label} → Script nicht erreichbar (HTTP=${http}) → skip"
    rm -f "$tmp"
    return 1
  fi

  # Zeilenenden normalisieren (Windows CRLF → Unix)
  sed -i 's/\r$//' "$tmp" 2>/dev/null || true
  sed -i '1s/^\xEF\xBB\xBF//' "$tmp" 2>/dev/null || true

  # BASE_URL auf diesen Checkpoint patchen (alte Scripts haben hardcoded /latest)
  if [[ -n "$base_override" ]]; then
    sed -i "s|BASE_URL=.*|BASE_URL=\"${base_override}\"|" "$tmp" 2>/dev/null || true
  fi

  if ! bash -n "$tmp" 2>/dev/null; then
    log "RUNNER: ${label} → Syntax-Fehler im Script → skip"
    rm -f "$tmp"
    return 1
  fi

  chmod +x "$tmp"
  local rc=0
  bash "$tmp" >>"${LOG_FILE}" 2>&1 || rc=$?
  rm -f "$tmp"

  if [[ "$rc" -eq 0 ]]; then
    log "RUNNER: ${label} → OK"
  else
    log "RUNNER: ${label} → FEHLER (exit=${rc})"
  fi
  return "$rc"
}

log "===== Runner START | Kanal: ${CHANNEL} ====="

# ── Beta: Kette überspringen, direkt aus Beta/ installieren ────────────────
if [[ "$CHANNEL" == "beta" ]]; then
  log "RUNNER: Beta-Modus aktiv → Revisionskette inaktiv"
  log "RUNNER: Lade direkt aus Beta/ …"
  if fetch_and_run "Beta" \
      "${GITHUB_RAW}/Beta/autodarts-webpanel-update.sh" \
      "${GITHUB_RAW}/Beta"; then
    log "===== Runner ENDE | Beta OK ====="
    echo "OK"
  else
    log "===== Runner ENDE | Beta FEHLER ====="
    echo "ERROR"
    exit 1
  fi
  exit 0
fi

# ── Stable: Revisionskette ─────────────────────────────────────────────────
log "RUNNER: Stable-Modus → lade update-revisions.conf"

conf_tmp="$(mktemp)"
conf_http="$(curl -sSL --connect-timeout 5 --max-time 30 \
  -o "$conf_tmp" -w "%{http_code}" \
  "${GITHUB_RAW}/latest/update-revisions.conf" || true)"

if [[ "$conf_http" != "200" ]] || [[ ! -s "$conf_tmp" ]]; then
  log "RUNNER: update-revisions.conf nicht erreichbar (HTTP=${conf_http}) → Fallback: nur latest"
  rm -f "$conf_tmp"
  fetch_and_run "latest" \
    "${GITHUB_RAW}/latest/autodarts-webpanel-update.sh" \
    "${GITHUB_RAW}/latest" \
    && echo "OK" || { echo "ERROR"; exit 1; }
  log "===== Runner ENDE ====="
  exit 0
fi

# shellcheck source=/dev/null
source "$conf_tmp"; rm -f "$conf_tmp"

if [[ -z "${UPDATE_REVISIONS:-}" ]]; then
  log "RUNNER: UPDATE_REVISIONS nicht definiert → Fallback: nur latest"
  fetch_and_run "latest" \
    "${GITHUB_RAW}/latest/autodarts-webpanel-update.sh" \
    "${GITHUB_RAW}/latest" \
    && echo "OK" || { echo "ERROR"; exit 1; }
  log "===== Runner ENDE ====="
  exit 0
fi

IFS=';' read -ra REVS <<< "$UPDATE_REVISIONS"
local_ver="$(cat "${LOCAL_VER_FILE}" 2>/dev/null | tr -d '\r\n' || true)"

log "RUNNER: ─────────────────────────────────────────────"
log "RUNNER: Installierte Version : ${local_ver:-unbekannt}"
log "RUNNER: Update-Kette         : ${UPDATE_REVISIONS}"
log "RUNNER: ─────────────────────────────────────────────"

ERRORS=0
DONE=0

for rev in "${REVS[@]}"; do
  rev="$(printf '%s' "$rev" | tr -d '[:space:]')"
  [[ -z "$rev" ]] && continue

  # Versionsvergleich — "latest" immer ausführen, Zwischenstationen nur wenn nötig
  if [[ "$rev" != "latest" ]] && [[ -n "$local_ver" ]] && ! version_gt "$rev" "$local_ver"; then
    log "RUNNER: ${rev} → skip (lokal ${local_ver} >= ${rev})"
    continue
  fi

  log "RUNNER: ${rev} → starten …"

  if fetch_and_run "$rev" \
      "${GITHUB_RAW}/${rev}/autodarts-webpanel-update.sh" \
      "${GITHUB_RAW}/${rev}"; then
    DONE=$((DONE + 1))
  else
    ERRORS=$((ERRORS + 1))
    log "RUNNER: ${rev} FEHLER — Kette läuft weiter"
  fi

  local_ver="$(cat "${LOCAL_VER_FILE}" 2>/dev/null | tr -d '\r\n' || true)"
  log "RUNNER: Version nach ${rev}: ${local_ver:-unbekannt}"
  log "RUNNER: ─────────────────────────────────────────────"
done

log "RUNNER: Kette abgeschlossen | OK=${DONE} | FEHLER=${ERRORS}"
log "===== Runner ENDE ====="

[[ "$ERRORS" -eq 0 ]] && echo "OK" || echo "DONE_WITH_ERRORS"
exit 0
