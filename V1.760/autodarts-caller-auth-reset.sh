#!/usr/bin/env bash
# BUILD: CALLER-AUTH-RESET-20260806-01
set -Eeuo pipefail

LOG="/var/log/autodarts_caller_auth_reset.log"
STATE_FILE="/var/lib/autodarts/caller-auth-reset-last.json"
TS="$(date +'%Y%m%d-%H%M%S')"

mkdir -p "$(dirname "$LOG")" "$(dirname "$STATE_FILE")"
exec >>"$LOG" 2>&1

log() { echo "[$(date +'%F %T')] $*"; }

service_user() {
  local user
  user="$(systemctl show darts-caller.service -p User --value 2>/dev/null || true)"
  if [[ -z "$user" ]]; then
    echo "root"
  else
    echo "$user"
  fi
}

token_dir_for_user() {
  local user="$1"
  if [[ "$user" == "root" ]]; then
    echo "/root/.config/darts-caller"
  else
    local home
    home="$(getent passwd "$user" | cut -d: -f6 || true)"
    if [[ -n "$home" ]]; then
      echo "${home}/.config/darts-caller"
    else
      echo "/home/${user}/.config/darts-caller"
    fi
  fi
}

caller_auth_json() {
  curl -sk --max-time 3 https://127.0.0.1:8079/api/auth/status 2>/dev/null || true
}

write_state() {
  local status="$1"
  local message="$2"
  local auth="${3:-}"
  python3 - "$STATE_FILE" "$status" "$message" "$auth" <<'PY'
import datetime, json, os, sys
path, status, message, raw_auth = sys.argv[1:]
try:
    auth = json.loads(raw_auth) if raw_auth else {}
except Exception:
    auth = {"raw": raw_auth}
data = {
    "status": status,
    "message": message,
    "auth_status": auth,
    "updated_at": datetime.datetime.now().astimezone().isoformat(),
}
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
os.chmod(path, 0o777)
PY
}

schedule_wled_after_auth() {
  # WLED nach erfolgreicher Caller-Anmeldung automatisch starten.
  # Der Login-Code läuft typischerweise nach kurzer Zeit ab; deshalb max. 10 Minuten warten.
  local cmd='
for i in $(seq 1 300); do
  raw="$(curl -sk --max-time 2 https://127.0.0.1:8079/api/auth/status 2>/dev/null || true)"
  if echo "$raw" | grep -q "\"state\":\"authenticated\""; then
    sleep 4
    systemctl reset-failed darts-wled.service 2>/dev/null || true
    systemctl restart darts-wled.service 2>/dev/null || systemctl start darts-wled.service 2>/dev/null || true
    exit 0
  fi
  sleep 2
done
systemctl start darts-wled.service 2>/dev/null || true
exit 0
'
  if command -v systemd-run >/dev/null 2>&1; then
    systemd-run --unit="autodarts-wled-after-auth-${TS}" --no-block --collect bash -lc "$cmd" >/dev/null 2>&1 || true
  else
    nohup bash -lc "$cmd" >/dev/null 2>&1 &
  fi
}

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Dieses Script muss als root laufen." >&2
  exit 1
fi

USER_NAME="$(service_user)"
TOKEN_DIR="$(token_dir_for_user "$USER_NAME")"
TOKEN_FILE="${TOKEN_DIR}/tokens.json"
BACKUP_DIR="${TOKEN_DIR}/backup"

log "===== Caller Auth Reset START ====="
log "Service-User: ${USER_NAME}"
log "Token-Datei: ${TOKEN_FILE}"

systemctl stop darts-wled.service 2>/dev/null || true
systemctl stop darts-caller.service 2>/dev/null || true

mkdir -p "$BACKUP_DIR"

if [[ -f "$TOKEN_FILE" ]]; then
  mv "$TOKEN_FILE" "${BACKUP_DIR}/tokens.json.bak-${TS}"
  log "tokens.json gesichert nach ${BACKUP_DIR}/tokens.json.bak-${TS}"
else
  log "Keine tokens.json vorhanden."
fi

systemctl reset-failed darts-caller.service darts-wled.service 2>/dev/null || true
systemctl start darts-caller.service

sleep 6

AUTH="$(caller_auth_json)"
write_state "success" "Caller-Anmeldung wurde zurückgesetzt. Bitte die Caller-Anmeldung öffnen und innerhalb des Zeitfensters autorisieren." "$AUTH"
schedule_wled_after_auth

log "Auth nach Reset: ${AUTH}"
log "===== Caller Auth Reset OK ====="
echo "OK"
