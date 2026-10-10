#!/usr/bin/env bash
# BUILD: CALLER-WLED-BINARY-UPDATER-SERVICEHOOK-CALLERTOGGLE-WATCHER-20260902-01
set -Eeuo pipefail

CALLER_REPO="Peschi90/darts-caller"
WLED_REPO="Peschi90/darts-wled"
CALLER_ASSET="darts-caller-arm64"
WLED_ASSET="darts-wled-arm64"
WLED_MANIFEST_ASSET="manifest.sig.json-darts-wled-arm64"

CALLER_BIN="/var/lib/autodarts/extensions/darts-caller/darts-caller"
WLED_BIN="/var/lib/autodarts/extensions/darts-wled/darts-wled"
WLED_MANIFEST="/var/lib/autodarts/extensions/darts-wled/manifest.sig.json"
WLED_SERVICE="/etc/systemd/system/darts-wled.service"
WLED_WAIT_SCRIPT="/usr/local/bin/autodarts-wait-caller-ready.sh"
WLED_DROPIN_DIR="/etc/systemd/system/darts-wled.service.d"
WLED_DROPIN="${WLED_DROPIN_DIR}/wait-caller.conf"
WLED_WATCHDOG_SCRIPT="/usr/local/bin/autodarts-wled-reconnect-watchdog.sh"
WLED_WATCHDOG_SERVICE="/etc/systemd/system/autodarts-wled-reconnect-watchdog.service"
WLED_WATCHDOG_TIMER="/etc/systemd/system/autodarts-wled-reconnect-watchdog.timer"

FLAG="/var/lib/autodarts/config/extensions-v2-installed.json"
LOG="/var/log/autodarts_extensions_update.log"
RESULT="/var/lib/autodarts/extensions-update-last.json"
BACKUP_ROOT="/var/lib/autodarts/config/backups"
LOCK="/run/autodarts-extensions-update.lock"

TARGET="${1:-all}"                 # all | caller | wled | service-repair
FORCE="${FORCE:-0}"                # FORCE=1 installiert auch dieselbe Version erneut
TS="$(date +'%Y%m%d-%H%M%S')"
BACKUP="${BACKUP_ROOT}/extensions-binary-update-${TS}"
TMP="$(mktemp -d /tmp/autodarts-update.XXXXXX)"

CALLER_STATUS="SKIPPED"
WLED_STATUS="SKIPPED"
CALLER_VERSION=""
WLED_VERSION=""
FINAL_STATUS="failed"
LAST_ERROR=""
MUTATION_STARTED=0

CALLER_WAS_ACTIVE=0
WLED_WAS_ACTIVE=0

mkdir -p "$(dirname "$LOG")" "$(dirname "$RESULT")"
exec >>"$LOG" 2>&1

log() { echo "[$(date +'%F %T')] $*"; }

write_result() {
  python3 - "$RESULT" "$TARGET" "$CALLER_STATUS" "$WLED_STATUS" \
    "$CALLER_VERSION" "$WLED_VERSION" "$BACKUP" "$FORCE" \
    "$FINAL_STATUS" "$LAST_ERROR" <<'PY'
import datetime, json, os, sys
(path, target, caller_status, wled_status, caller_version, wled_version,
 backup, force, status, error) = sys.argv[1:]
data = {
    "ts": datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
    "target": target,
    "caller": caller_status,
    "wled": wled_status,
    "caller_version": caller_version,
    "wled_version": wled_version,
    "backup": backup if os.path.isdir(backup) else "",
    "force": force == "1",
    "status": status,
    "errors": error,
}
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
os.chmod(path, 0o777)
PY
}

flag_value() {
  local key="$1"
  python3 - "$FLAG" "$key" <<'PY'
import json, sys
path, key = sys.argv[1:]
try:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    data = {}
value = data.get(key, "")
if isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

fail() {
  LAST_ERROR="$*"
  log "FEHLER: $*"
  exit 1
}

service_stable() {
  local service="$1"
  local seconds="${2:-10}"
  local before after i
  before="$(systemctl show "$service" -p NRestarts --value 2>/dev/null || echo 0)"
  for ((i=0; i<seconds; i++)); do
    sleep 1
    systemctl is-active --quiet "$service" || return 1
  done
  after="$(systemctl show "$service" -p NRestarts --value 2>/dev/null || echo 0)"
  [[ "$before" == "$after" ]]
}

caller_auth_json() {
  curl -sk --max-time 2 https://127.0.0.1:8079/api/auth/status 2>/dev/null || true
}

json_field() {
  local key="$1"
  python3 -c '
import json, sys
key = sys.argv[1]
raw = sys.stdin.read()
try:
    data = json.loads(raw) if raw.strip() else {}
except Exception:
    data = {}
value = data.get(key, "")
if value is None:
    value = ""
print(value)
' "$key"
}

wait_for_caller_api() {
  local timeout="${1:-30}"
  local i raw state
  for ((i=0; i<timeout; i++)); do
    raw="$(caller_auth_json)"
    state="$(printf '%s' "$raw" | json_field state)"
    if [[ -n "$state" ]]; then
      log "Caller API erreichbar: auth_state=$state"
      return 0
    fi
    sleep 1
  done
  log "WARN: Caller API nach ${timeout}s nicht erreichbar."
  return 1
}

wait_for_caller_authenticated() {
  local timeout="${1:-20}"
  local i raw state account
  for ((i=0; i<timeout; i++)); do
    raw="$(caller_auth_json)"
    state="$(printf '%s' "$raw" | json_field state)"
    account="$(printf '%s' "$raw" | json_field account)"
    if [[ "$state" == "authenticated" ]]; then
      if [[ -n "$account" ]]; then
        log "Caller authentifiziert: account erkannt."
      else
        log "Caller authentifiziert: account noch leer."
      fi
      return 0
    fi
    sleep 1
  done
  log "WARN: Caller ist noch nicht authenticated. WLED wird trotzdem gestartet und verbindet sich später erneut."
  return 1
}

wait_for_wled_process() {
  local timeout="${1:-20}"
  local i
  for ((i=0; i<timeout; i++)); do
    if pgrep -f "$WLED_BIN" >/dev/null 2>&1; then
      log "WLED-Prozess läuft."
      return 0
    fi
    sleep 1
  done
  log "WARN: WLED-Prozess wurde nach ${timeout}s nicht gefunden."
  return 1
}

wait_for_wled_ready_log() {
  local since="$1"
  local timeout="${2:-45}"
  local i
  for ((i=0; i<timeout; i++)); do
    if journalctl -u darts-wled.service --since "$since" --no-pager -o cat 2>/dev/null \
      | grep -Eq '\[OK\] Caller successfully connected|CONNECTED TO DATA-FEEDER|APPLICATION RUNNING'; then
      log "WLED-Start bestätigt: Dienst läuft und Log zeigt Verbindung/Laufzustand."
      return 0
    fi
    sleep 1
  done
  log "WARN: WLED läuft, aber Log-Bestätigung für Caller-Verbindung wurde nach ${timeout}s nicht gesehen."
  return 1
}


install_wled_wait_hook() {
  log "Installiere dauerhaften WLED-Start-Wait und sparsamen Reconnect-Watchdog …"

  cat >"$WLED_WAIT_SCRIPT" <<'EOF'
#!/usr/bin/env bash
# BUILD: WAIT-CALLER-WLED-READY-20260806-02
set -Eeuo pipefail

CALLER_TIMEOUT="${AUTODARTS_WAIT_CALLER_TIMEOUT:-90}"
WLED_TIMEOUT="${AUTODARTS_WAIT_WLED_TIMEOUT:-90}"
AFTER_AUTH_SLEEP="${AUTODARTS_WAIT_CALLER_AFTER_AUTH_SLEEP:-20}"
CALLER_URL="https://127.0.0.1:8079/api/auth/status"
WLED_CONFIG="/var/lib/autodarts/config/darts-wled/start-custom.sh"

read_wled_endpoint() {
  python3 - "$WLED_CONFIG" <<'PY'
import shlex, sys
path = sys.argv[1]
try:
    text = open(path, encoding="utf-8", errors="ignore").read()
except OSError:
    print("Dart-Led1.local")
    raise SystemExit(0)
try:
    parts = shlex.split(text, comments=True, posix=True)
except Exception:
    parts = text.replace("\\", " ").split()
for i, part in enumerate(parts):
    if part == "-WEPS" and i + 1 < len(parts):
        print(parts[i + 1])
        break
else:
    print("Dart-Led1.local")
PY
}

wled_ready() {
  local ep="$1"
  [[ -n "$ep" ]] || return 1
  curl -fsS --max-time 2 "http://${ep}/json/info" >/dev/null 2>&1
}

if ! command -v curl >/dev/null 2>&1; then
  echo "[wait-caller-wled] curl fehlt, WLED startet ohne Warteprüfung." >&2
  exit 0
fi

for i in $(seq 1 "$CALLER_TIMEOUT"); do
  raw="$(curl -sk --max-time 2 "$CALLER_URL" 2>/dev/null || true)"
  if echo "$raw" | grep -q '"state":"authenticated"'; then
    echo "[wait-caller-wled] Caller ist authenticated. Warte ${AFTER_AUTH_SLEEP}s auf internen Event-Feed ..."
    sleep "$AFTER_AUTH_SLEEP"
    break
  fi
  if [[ "$i" == "$CALLER_TIMEOUT" ]]; then
    echo "[wait-caller-wled] WARN: Caller wurde nach ${CALLER_TIMEOUT}s nicht authenticated erkannt. WLED startet trotzdem." >&2
  fi
  sleep 1
done

WLED_EP="${AUTODARTS_WLED_ENDPOINT:-$(read_wled_endpoint)}"
if [[ -n "$WLED_EP" ]]; then
  for i in $(seq 1 "$WLED_TIMEOUT"); do
    if wled_ready "$WLED_EP"; then
      echo "[wait-caller-wled] WLED-ESP erreichbar: ${WLED_EP}"
      exit 0
    fi
    sleep 1
  done
  echo "[wait-caller-wled] WARN: WLED-ESP ${WLED_EP} nach ${WLED_TIMEOUT}s nicht erreichbar. Dienst startet trotzdem; Watchdog verbindet später neu." >&2
fi

exit 0
EOF

  chmod 777 "$WLED_WAIT_SCRIPT"

  mkdir -p "$WLED_DROPIN_DIR"
  cat >"$WLED_DROPIN" <<EOF
[Service]
ExecStartPre=$WLED_WAIT_SCRIPT
EOF
  chmod 777 "$WLED_DROPIN"

  cat >"$WLED_WATCHDOG_SCRIPT" <<'EOF'
#!/usr/bin/env bash
# BUILD: EXTENSIONS-HEALTH-WATCHDOG-CALLERTOGGLE-CALLER-WLED-20260902-01
set -Eeuo pipefail

LOCK="/run/autodarts-wled-reconnect-watchdog.lock"
LOG="/var/log/autodarts_wled_watchdog.log"
CALLER_SERVICE="darts-caller.service"
WLED_SERVICE="darts-wled.service"
CALLER_URL="https://127.0.0.1:8079/api/auth/status"
CALLER_ENABLED_FLAG="/var/lib/autodarts/caller-enabled.json"
WLED_TARGETS_JSON="/var/lib/autodarts/wled-targets.json"
WLED_CONFIG="/var/lib/autodarts/config/darts-wled/start-custom.sh"

mkdir -p "$(dirname "$LOG")"
exec 9>"$LOCK"
flock -n 9 || exit 0

log() { echo "[$(date +'%F %T')] $*" >>"$LOG"; }

json_state() {
  python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    data = json.loads(raw) if raw.strip() else {}
except Exception:
    print("")
    raise SystemExit(0)
print(data.get("state") or "")
' 2>/dev/null || true
}

caller_enabled() {
  python3 - "$CALLER_ENABLED_FLAG" <<'PY'
import json, os, sys
path = sys.argv[1]
try:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f) or {}
        enabled = bool(data.get("enabled", True))
    else:
        enabled = True
except Exception:
    enabled = True
raise SystemExit(0 if enabled else 1)
PY
}

wled_enabled() {
  python3 - "$WLED_TARGETS_JSON" <<'PY'
import json, os, sys
path = sys.argv[1]
try:
    if not os.path.exists(path):
        # Alte/ungepflegte Systeme: bestehendes Verhalten beibehalten.
        raise SystemExit(0)
    with open(path, encoding="utf-8") as f:
        data = json.load(f) or {}
    if not bool(data.get("master_enabled", True)):
        raise SystemExit(1)
    targets = data.get("targets") or []
    for t in targets:
        if isinstance(t, dict) and bool(t.get("enabled")) and str(t.get("host") or "").strip():
            raise SystemExit(0)
    raise SystemExit(1)
except SystemExit:
    raise
except Exception:
    # Bei kaputter Config lieber nichts zerstören: altes Verhalten beibehalten.
    raise SystemExit(0)
PY
}

caller_auth_json() {
  curl -sk --max-time 3 "$CALLER_URL" 2>/dev/null || true
}

CALLER_RESTARTED=0
restart_caller() {
  local reason="${1:-unknown}"
  CALLER_RESTARTED=1
  log "Caller wirkt nicht gesund (${reason}) -> restart ${CALLER_SERVICE}"
  systemctl reset-failed "$CALLER_SERVICE" 2>/dev/null || true
  systemctl restart "$CALLER_SERVICE" >/dev/null 2>&1 || systemctl start "$CALLER_SERVICE" >/dev/null 2>&1 || true
  sleep 8
}

ensure_caller_healthy() {
  local raw state

  if ! caller_enabled; then
    log "Caller ist per Webpanel deaktiviert -> stop Caller/WLED und exit"
    systemctl stop "$WLED_SERVICE" >/dev/null 2>&1 || true
    systemctl stop "$CALLER_SERVICE" >/dev/null 2>&1 || true
    return 2
  fi

  if ! systemctl status "$CALLER_SERVICE" >/dev/null 2>&1; then
    log "Caller-Service existiert nicht -> skip"
    return 2
  fi

  if ! systemctl is-active --quiet "$CALLER_SERVICE"; then
    restart_caller "service inactive/failed"
  fi

  raw="$(caller_auth_json)"
  if [[ -z "$raw" ]]; then
    restart_caller "API antwortet nicht"
    raw="$(caller_auth_json)"
  fi

  if [[ -z "$raw" ]]; then
    log "Caller API nach Restart weiterhin ohne Antwort -> WLED-Prüfung übersprungen"
    return 2
  fi

  state="$(printf '%s' "$raw" | json_state)"
  case "$state" in
    authenticated)
      return 0
      ;;
    pending|unauthenticated|not_authenticated)
      # Kein Fehler: nach Token-Reset/frischer Installation normal. Nicht durch Neustarts nervös machen.
      log "Caller API erreichbar, aber nicht authenticated (state=${state}) -> kein Restart"
      return 2
      ;;
    "")
      restart_caller "API liefert kein gültiges state-Feld"
      raw="$(caller_auth_json)"
      state="$(printf '%s' "$raw" | json_state)"
      [[ "$state" == "authenticated" ]] && return 0
      log "Caller nach Restart nicht authenticated/ungueltig (state=${state:-empty})"
      return 2
      ;;
    *)
      restart_caller "unerwarteter auth_state=${state}"
      raw="$(caller_auth_json)"
      state="$(printf '%s' "$raw" | json_state)"
      [[ "$state" == "authenticated" ]] && return 0
      log "Caller nach Restart nicht authenticated (state=${state:-empty})"
      return 2
      ;;
  esac
}

read_wled_endpoint() {
  python3 - "$WLED_CONFIG" <<'PY'
import shlex, sys
path = sys.argv[1]
try:
    text = open(path, encoding="utf-8", errors="ignore").read()
except OSError:
    print("")
    raise SystemExit(0)
try:
    parts = shlex.split(text, comments=True, posix=True)
except Exception:
    parts = text.replace("\\", " ").split()
for i, part in enumerate(parts):
    if part == "-WEPS" and i + 1 < len(parts):
        print(parts[i + 1])
        break
PY
}

# 1) Caller leichtgewichtig pruefen und nur bei echter Funktionsstoerung neu starten.
if ! ensure_caller_healthy; then
  exit 0
fi

# 2) WLED nur anfassen, wenn WLED im Webpanel wirklich aktiv ist.
if ! wled_enabled; then
  if systemctl is-active --quiet "$WLED_SERVICE"; then
    log "WLED ist im Webpanel deaktiviert/kein Target aktiv -> stop ${WLED_SERVICE}"
    systemctl stop "$WLED_SERVICE" >/dev/null 2>&1 || true
  fi
  exit 0
fi

# 3) Wenn Caller neu gestartet wurde, WLED danach einmal sauber neu verbinden.
if [[ "$CALLER_RESTARTED" == "1" ]] && systemctl status "$WLED_SERVICE" >/dev/null 2>&1; then
  if systemctl is-active --quiet "$WLED_SERVICE"; then
    log "Caller wurde neu gestartet -> restart ${WLED_SERVICE} fuer saubere Neuverbindung"
    systemctl reset-failed "$WLED_SERVICE" 2>/dev/null || true
    systemctl restart "$WLED_SERVICE" >/dev/null 2>&1 || true
    exit 0
  fi
fi

# 4) Bestehende WLED-Pruefung: nur wenn Caller authenticated ist und WLED aktiv sein soll.
EP="${AUTODARTS_WLED_ENDPOINT:-$(read_wled_endpoint)}"
[[ -n "$EP" ]] || exit 0

if ! curl -fsS --max-time 2 "http://${EP}/json/info" >/dev/null 2>&1; then
  exit 0
fi

if ! systemctl is-active --quiet "$WLED_SERVICE"; then
  log "darts-wled ist nicht active, ESP ${EP} ist erreichbar -> start"
  systemctl start "$WLED_SERVICE" >/dev/null 2>&1 || true
  exit 0
fi

if journalctl -u "$WLED_SERVICE" --since "3 minutes ago" --no-pager -o cat 2>/dev/null   | grep -Eiq 'WLED not available|Name or service not known|WLED Controller connection lost|Connection lost: WLED|failed to resolve'; then
  log "WLED-Fehler erkannt und ESP ${EP} wieder erreichbar -> restart darts-wled"
  systemctl restart "$WLED_SERVICE" >/dev/null 2>&1 || true
fi
EOF
  chmod 777 "$WLED_WATCHDOG_SCRIPT"

  cat >"$WLED_WATCHDOG_SERVICE" <<EOF
[Unit]
Description=Autodarts WLED reconnect watchdog
After=network-online.target darts-caller.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$WLED_WATCHDOG_SCRIPT
EOF
  chmod 777 "$WLED_WATCHDOG_SERVICE"

  cat >"$WLED_WATCHDOG_TIMER" <<'EOF'
[Unit]
Description=Run Autodarts WLED reconnect watchdog periodically

[Timer]
OnBootSec=120s
OnUnitActiveSec=120s
AccuracySec=30s
Persistent=false

[Install]
WantedBy=timers.target
EOF
  chmod 777 "$WLED_WATCHDOG_TIMER"

  caller_enabled_for_timer() {
    python3 - "/var/lib/autodarts/caller-enabled.json" <<'PY'
import json, os, sys
path = sys.argv[1]
try:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f) or {}
        enabled = bool(data.get("enabled", True))
    else:
        enabled = True
except Exception:
    enabled = True
raise SystemExit(0 if enabled else 1)
PY
  }

  if caller_enabled_for_timer; then
    systemctl enable autodarts-wled-reconnect-watchdog.timer >/dev/null 2>&1 || true
    systemctl start autodarts-wled-reconnect-watchdog.timer >/dev/null 2>&1 || true
  else
    systemctl disable --now autodarts-wled-reconnect-watchdog.timer >/dev/null 2>&1 || true
    systemctl stop darts-wled.service >/dev/null 2>&1 || true
    systemctl stop darts-caller.service >/dev/null 2>&1 || true
    log "Caller ist per Webpanel deaktiviert -> Watcher/Caller/WLED bleiben aus."
  fi
}

fix_wled_service_startlimit_location() {
  [[ -f "$WLED_SERVICE" ]] || return 0
  python3 - "$WLED_SERVICE" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
lines = [line for line in lines if not line.strip().startswith("StartLimitIntervalSec=")]

out = []
inserted = False
for line in lines:
    out.append(line)
    if line.strip() == "[Unit]" and not inserted:
        out.append("StartLimitIntervalSec=0")
        inserted = True

if not inserted:
    out = ["[Unit]", "StartLimitIntervalSec=0"] + out

path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
PY
  chmod 777 "$WLED_SERVICE"
}

resolve_release() {
  local repo="$1"
  local binary_asset="$2"
  local manifest_asset="$3"
  local meta_out="$4"

  python3 - "$repo" "$binary_asset" "$manifest_asset" "$meta_out" <<'PY'
import json, sys, urllib.request
repo, binary_name, manifest_name, out = sys.argv[1:]
req = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/releases?per_page=50",
    headers={"User-Agent": "Autodarts-Webpanel"}
)
with urllib.request.urlopen(req, timeout=30) as response:
    releases = json.load(response)

for release in releases:
    if release.get("draft"):
        continue
    assets = {asset.get("name"): asset for asset in release.get("assets", [])}
    binary = assets.get(binary_name)
    manifest = assets.get(manifest_name) if manifest_name else None

    if not binary:
        continue
    if manifest_name and not manifest:
        continue

    data = {
        "tag": release.get("tag_name", "unknown"),
        "binary_url": binary.get("browser_download_url", ""),
        "manifest_url": manifest.get("browser_download_url", "") if manifest else "",
    }
    if data["binary_url"] and (not manifest_name or data["manifest_url"]):
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        raise SystemExit(0)

needed = binary_name + (f" + {manifest_name}" if manifest_name else "")
raise SystemExit(f"Kein passendes Release gefunden: {repo}: {needed}")
PY
}

meta_value() {
  local file="$1"
  local key="$2"
  python3 - "$file" "$key" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
print(data.get(sys.argv[2], ""))
PY
}

download_file() {
  local url="$1"
  local destination="$2"
  local label="$3"

  log "Download $label: $url"
  curl -fL --silent --show-error --retry 3 \
    --connect-timeout 20 --max-time 300 \
    "$url" -o "$destination"

  [[ -s "$destination" ]] || fail "$label wurde leer heruntergeladen."
  chmod 777 "$destination"
}

update_flag() {
  local caller_version="$1"
  local wled_version="$2"

  python3 - "$FLAG" "$caller_version" "$wled_version" <<'PY'
import datetime, json, os, sys
path, caller, wled = sys.argv[1:]
data = {}
try:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    pass

data["installed"] = True
if caller:
    data["caller_version"] = caller
if wled:
    data["wled_version"] = wled
data["updated_at"] = datetime.datetime.now().astimezone().isoformat()

os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
os.chmod(path, 0o777)
PY
}

rollback() {
  log "Rollback wird ausgeführt …"

  systemctl stop darts-wled.service 2>/dev/null || true
  systemctl stop darts-caller.service 2>/dev/null || true

  if [[ -f "$BACKUP/darts-caller" ]]; then
    install -m 0777 "$BACKUP/darts-caller" "$CALLER_BIN"
  fi

  if [[ -f "$BACKUP/darts-wled" ]]; then
    install -m 0777 "$BACKUP/darts-wled" "$WLED_BIN"
  fi

  if [[ -f "$BACKUP/manifest.sig.json" ]]; then
    install -m 0777 "$BACKUP/manifest.sig.json" "$WLED_MANIFEST"
  elif [[ -f "$BACKUP/.wled-manifest-missing" ]]; then
    rm -f "$WLED_MANIFEST"
  fi

  systemctl reset-failed darts-caller.service darts-wled.service 2>/dev/null || true

  if [[ "$CALLER_WAS_ACTIVE" == "1" || "$WLED_WAS_ACTIVE" == "1" ]]; then
    systemctl start darts-caller.service 2>/dev/null || true
  fi
  if [[ "$WLED_WAS_ACTIVE" == "1" ]]; then
    systemctl start darts-wled.service 2>/dev/null || true
  fi

  log "Rollback abgeschlossen."
}

cleanup() {
  local rc=$?
  trap - EXIT

  if [[ "$rc" -ne 0 && "$MUTATION_STARTED" == "1" ]]; then
    rollback
  fi

  if [[ "$rc" -ne 0 ]]; then
    [[ "$CALLER_STATUS" == "CHANGED" || "$CALLER_STATUS" == "REINSTALLED" ]] && CALLER_STATUS="ERROR"
    [[ "$WLED_STATUS" == "CHANGED" || "$WLED_STATUS" == "REINSTALLED" ]] && WLED_STATUS="ERROR"
    FINAL_STATUS="failed"
  fi

  write_result
  rm -rf "$TMP"
  exit "$rc"
}
trap cleanup EXIT

[[ "$(id -u)" == "0" ]] || fail "Updater muss als root laufen."
[[ "$(uname -m)" == "aarch64" ]] || fail "Nur ARM64/aarch64 wird unterstützt."
[[ -f "$FLAG" ]] || fail "V2-Installation wurde noch nicht erfolgreich abgeschlossen."
[[ "$(flag_value installed)" == "true" ]] || fail "V2-Installation ist nicht als erfolgreich markiert."

exec 9>"$LOCK"
flock -n 9 || fail "Update läuft bereits."

install_wled_wait_hook
fix_wled_service_startlimit_location
systemctl daemon-reload

if [[ "$TARGET" == "service-repair" || "$TARGET" == "services" || "$TARGET" == "repair" ]]; then
  CALLER_STATUS="SKIPPED"
  WLED_STATUS="SERVICE_REPAIR"
  FINAL_STATUS="success"
  LAST_ERROR=""
  log "Service-Reparatur erfolgreich: WLED-Wait-Hook installiert und systemd neu geladen."
  exit 0
fi

DO_CALLER=0
DO_WLED=0
case "$TARGET" in
  all) DO_CALLER=1; DO_WLED=1 ;;
  caller) DO_CALLER=1 ;;
  wled) DO_WLED=1 ;;
  *) fail "Ungültiges Ziel: $TARGET" ;;
esac

CURRENT_CALLER_VERSION="$(flag_value caller_version)"
CURRENT_WLED_VERSION="$(flag_value wled_version)"

NEED_CALLER=0
NEED_WLED=0

if [[ "$DO_CALLER" == "1" ]]; then
  resolve_release "$CALLER_REPO" "$CALLER_ASSET" "" "$TMP/caller.json"
  CALLER_VERSION="$(meta_value "$TMP/caller.json" tag)"

  if [[ "$FORCE" == "1" || ! -x "$CALLER_BIN" || "$CALLER_VERSION" != "$CURRENT_CALLER_VERSION" ]]; then
    NEED_CALLER=1
    if [[ "$FORCE" == "1" && "$CALLER_VERSION" == "$CURRENT_CALLER_VERSION" && -x "$CALLER_BIN" ]]; then
      CALLER_STATUS="REINSTALLED"
    else
      CALLER_STATUS="CHANGED"
    fi
  else
    CALLER_STATUS="UNCHANGED"
  fi
fi

if [[ "$DO_WLED" == "1" ]]; then
  resolve_release "$WLED_REPO" "$WLED_ASSET" "$WLED_MANIFEST_ASSET" "$TMP/wled.json"
  WLED_VERSION="$(meta_value "$TMP/wled.json" tag)"

  if [[ "$FORCE" == "1" || ! -x "$WLED_BIN" || ! -s "$WLED_MANIFEST" || "$WLED_VERSION" != "$CURRENT_WLED_VERSION" ]]; then
    NEED_WLED=1
    if [[ "$FORCE" == "1" && "$WLED_VERSION" == "$CURRENT_WLED_VERSION" && -x "$WLED_BIN" && -s "$WLED_MANIFEST" ]]; then
      WLED_STATUS="REINSTALLED"
    else
      WLED_STATUS="CHANGED"
    fi
  else
    WLED_STATUS="UNCHANGED"
  fi
fi

if [[ "$NEED_CALLER" == "0" && "$NEED_WLED" == "0" ]]; then
  FINAL_STATUS="success"
  log "Kein Update erforderlich. Caller=$CALLER_STATUS WLED=$WLED_STATUS"
  exit 0
fi

if [[ "$NEED_CALLER" == "1" ]]; then
  download_file "$(meta_value "$TMP/caller.json" binary_url)" "$TMP/darts-caller" "Caller"
fi

if [[ "$NEED_WLED" == "1" ]]; then
  download_file "$(meta_value "$TMP/wled.json" binary_url)" "$TMP/darts-wled" "WLED"
  download_file "$(meta_value "$TMP/wled.json" manifest_url)" "$TMP/manifest.sig.json" "WLED-Manifest"
fi

CALLER_WAS_ACTIVE=0
WLED_WAS_ACTIVE=0
systemctl is-active --quiet darts-caller.service && CALLER_WAS_ACTIVE=1
systemctl is-active --quiet darts-wled.service && WLED_WAS_ACTIVE=1

mkdir -p "$BACKUP"
if [[ "$NEED_CALLER" == "1" && -f "$CALLER_BIN" ]]; then
  cp -a "$CALLER_BIN" "$BACKUP/darts-caller"
fi
if [[ "$NEED_WLED" == "1" && -f "$WLED_BIN" ]]; then
  cp -a "$WLED_BIN" "$BACKUP/darts-wled"
fi
if [[ "$NEED_WLED" == "1" ]]; then
  if [[ -f "$WLED_MANIFEST" ]]; then
    cp -a "$WLED_MANIFEST" "$BACKUP/manifest.sig.json"
  else
    touch "$BACKUP/.wled-manifest-missing"
  fi
fi

MUTATION_STARTED=1

if [[ "$WLED_WAS_ACTIVE" == "1" ]]; then
  systemctl stop darts-wled.service
fi
if [[ "$NEED_CALLER" == "1" && "$CALLER_WAS_ACTIVE" == "1" ]]; then
  systemctl stop darts-caller.service
fi

if [[ "$NEED_CALLER" == "1" ]]; then
  install -m 0777 "$TMP/darts-caller" "$CALLER_BIN"
fi

if [[ "$NEED_WLED" == "1" ]]; then
  install -m 0777 "$TMP/darts-wled" "$WLED_BIN"
  install -m 0777 "$TMP/manifest.sig.json" "$WLED_MANIFEST"
fi

# WLED darf bei auth_state=pending kein Update-Rollback auslösen.
# Pending ist z.B. nach Token-Reset oder frischer Installation ein normaler Zustand.
CALLER_AUTHENTICATED=0

caller_enabled_for_main() {
  python3 - "/var/lib/autodarts/caller-enabled.json" <<'PY'
import json, os, sys
path = sys.argv[1]
try:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data=json.load(f) or {}
        enabled=bool(data.get("enabled", True))
    else:
        enabled=True
except Exception:
    enabled=True
raise SystemExit(0 if enabled else 1)
PY
}

wled_enabled_for_main() {
  python3 - "/var/lib/autodarts/wled-targets.json" <<'PY'
import json, os, sys
path = sys.argv[1]
try:
    if not os.path.exists(path):
        raise SystemExit(0)
    with open(path, encoding="utf-8") as f:
        data=json.load(f) or {}
    if not bool(data.get("master_enabled", True)):
        raise SystemExit(1)
    for t in data.get("targets") or []:
        if isinstance(t, dict) and bool(t.get("enabled")) and str(t.get("host") or "").strip():
            raise SystemExit(0)
    raise SystemExit(1)
except SystemExit:
    raise
except Exception:
    raise SystemExit(0)
PY
}

if ! caller_enabled_for_main; then
  log "Caller ist per Webpanel deaktiviert. Update-Dateien wurden aktualisiert; Caller/WLED/Watcher bleiben aus."
  systemctl disable --now darts-wled.service >/dev/null 2>&1 || true
  systemctl disable --now darts-caller.service >/dev/null 2>&1 || true
  systemctl disable --now autodarts-wled-reconnect-watchdog.timer >/dev/null 2>&1 || true
  systemctl reset-failed darts-caller.service darts-wled.service >/dev/null 2>&1 || true
else
  if [[ "$NEED_CALLER" == "1" && ("$CALLER_WAS_ACTIVE" == "1" || "$WLED_WAS_ACTIVE" == "1") ]]; then
    log "Starte Caller-Dienst nach Update …"
    systemctl start darts-caller.service
    service_stable darts-caller.service 8 || fail "Caller startet nach dem Update nicht stabil."
    wait_for_caller_api 35 || fail "Caller API ist nach dem Update nicht erreichbar."
    if wait_for_caller_authenticated 20; then
      CALLER_AUTHENTICATED=1
    else
      CALLER_AUTHENTICATED=0
    fi
    sleep 4
  elif [[ "$WLED_WAS_ACTIVE" == "1" ]]; then
    # Caller wurde nicht aktualisiert, aber WLED wird neu gestartet.
    # Trotzdem kurz sicherstellen, dass der Data-Feeder erreichbar ist.
    wait_for_caller_api 20 || true
    if wait_for_caller_authenticated 8; then
      CALLER_AUTHENTICATED=1
    else
      CALLER_AUTHENTICATED=0
    fi
    sleep 2
  fi

  # Der Timer ist jetzt ein gemeinsamer Caller/WLED-Watcher. Wenn Caller aktiv ist,
  # soll er laufen, auch wenn WLED aus ist.
  systemctl enable --now autodarts-wled-reconnect-watchdog.timer >/dev/null 2>&1 || true

  if [[ "$WLED_WAS_ACTIVE" == "1" ]]; then
    if [[ "$CALLER_AUTHENTICATED" == "1" ]] && wled_enabled_for_main; then
      log "Starte WLED-Dienst nach Update …"
      systemctl enable darts-wled.service >/dev/null 2>&1 || true
      WLED_START_SINCE="$(date +'%F %T')"
      systemctl start darts-wled.service
      service_stable darts-wled.service 12 || fail "WLED startet nach dem Update nicht stabil."
      wait_for_wled_process 20 || fail "WLED-Prozess läuft nach dem Update nicht."
      wait_for_wled_ready_log "$WLED_START_SINCE" 45 || true
    else
      log "Caller nicht authenticated oder WLED deaktiviert/kein Target aktiv. WLED bleibt aus; Caller-Watcher bleibt aktiv."
      systemctl disable --now darts-wled.service >/dev/null 2>&1 || true
      systemctl reset-failed darts-wled.service >/dev/null 2>&1 || true
    fi
  fi
fi

update_flag \
  "${CALLER_VERSION:-$CURRENT_CALLER_VERSION}" \
  "${WLED_VERSION:-$CURRENT_WLED_VERSION}"

MUTATION_STARTED=0
FINAL_STATUS="success"
log "Update erfolgreich. Caller=$CALLER_STATUS ($CALLER_VERSION) WLED=$WLED_STATUS ($WLED_VERSION)"
