#!/usr/bin/env bash
# BUILD: CALLER-WLED-V2-SERVICEHOOK-JSONFIX-WLEDWATCH-UNITFIX-PENDINGOK-20260826-12
set -Eeuo pipefail

CALLER_REPO="Peschi90/darts-caller"
WLED_REPO="Peschi90/darts-wled"
CALLER_ASSET="darts-caller-arm64"
WLED_ASSET="darts-wled-arm64"
WLED_MANIFEST_ASSET="manifest.sig.json-darts-wled-arm64"

CALLER_DIR="/var/lib/autodarts/extensions/darts-caller"
WLED_DIR="/var/lib/autodarts/extensions/darts-wled"
CALLER_BIN="${CALLER_DIR}/darts-caller"
WLED_BIN="${WLED_DIR}/darts-wled"
WLED_MANIFEST="${WLED_DIR}/manifest.sig.json"

CALLER_CONFIG_DIR="/var/lib/autodarts/config/darts-caller"
CALLER_CONFIG="${CALLER_CONFIG_DIR}/start-custom.sh"
WLED_CONFIG_DIR="/var/lib/autodarts/config/darts-wled"
WLED_CONFIG="${WLED_CONFIG_DIR}/start-custom.sh"

CALLER_SERVICE="/etc/systemd/system/darts-caller.service"
WLED_SERVICE="/etc/systemd/system/darts-wled.service"
CALLER_OVERRIDE_DIR="/etc/systemd/system/darts-caller.service.d"
WLED_WAIT_SCRIPT="/usr/local/bin/autodarts-wait-caller-ready.sh"
WLED_DROPIN_DIR="/etc/systemd/system/darts-wled.service.d"
WLED_DROPIN="${WLED_DROPIN_DIR}/wait-caller.conf"
WLED_WATCHDOG_SCRIPT="/usr/local/bin/autodarts-wled-reconnect-watchdog.sh"
WLED_WATCHDOG_SERVICE="/etc/systemd/system/autodarts-wled-reconnect-watchdog.service"
WLED_WATCHDOG_TIMER="/etc/systemd/system/autodarts-wled-reconnect-watchdog.timer"

FLAG="/var/lib/autodarts/config/extensions-v2-installed.json"
STATE="/var/lib/autodarts/extensions-v2-install-state.json"
LOG="/var/log/autodarts_extensions_v2_install.log"
BACKUP_ROOT="/var/lib/autodarts/config/backups"
LOCK="/run/autodarts-extensions-v2-install.lock"

# Nach erfolgreicher Installation wird der Raspberry automatisch neu gestartet.
# Für eine bewusste Diagnose kann der Installer einmalig mit AUTO_REBOOT=0
# gestartet werden.
AUTO_REBOOT="${AUTO_REBOOT:-1}"
AUTO_REBOOT_DELAY="${AUTO_REBOOT_DELAY:-8}"

TS="$(date +'%Y%m%d-%H%M%S')"
BACKUP="${BACKUP_ROOT}/extensions-v2-migration-${TS}"
TMP="$(mktemp -d /tmp/autodarts-v2.XXXXXX)"

MIGRATION_STARTED=0
SUCCESS=0
LAST_ERROR=""
CALLER_WAS_ACTIVE=0
WLED_WAS_ACTIVE=0

mkdir -p "$(dirname "$LOG")" "$(dirname "$STATE")" "$BACKUP"
exec >>"$LOG" 2>&1

log() { echo "[$(date +'%F %T')] $*"; }

write_state() {
  local status="$1"
  local message="${2:-}"
  python3 - "$STATE" "$status" "$message" <<'PY'
import datetime, json, os, sys
path, status, message = sys.argv[1:]
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump({
        "status": status,
        "message": message,
        "updated_at": datetime.datetime.now().astimezone().isoformat()
    }, f, ensure_ascii=False, indent=2)
os.chmod(path, 0o777)
PY
}

fail() {
  LAST_ERROR="$*"
  log "FEHLER: $*"
  exit 1
}

is_active() {
  systemctl is-active --quiet "$1" 2>/dev/null
}

backup_optional() {
  local src="$1"
  local dst="$2"
  local marker="$3"
  if [[ -e "$src" ]]; then
    mkdir -p "$(dirname "$dst")"
    cp -a "$src" "$dst"
    touch "$marker"
  fi
}

restore_optional() {
  local marker="$1"
  local src="$2"
  local dst="$3"
  rm -rf "$dst"
  if [[ -f "$marker" && -e "$src" ]]; then
    mkdir -p "$(dirname "$dst")"
    cp -a "$src" "$dst"
  fi
}

rollback() {
  log "Rollback wird ausgeführt …"
  systemctl stop darts-wled.service 2>/dev/null || true
  systemctl stop darts-caller.service 2>/dev/null || true

  restore_optional "$BACKUP/.caller-dir" "$BACKUP/darts-caller" "$CALLER_DIR"
  restore_optional "$BACKUP/.wled-dir" "$BACKUP/darts-wled" "$WLED_DIR"
  restore_optional "$BACKUP/.caller-config" "$BACKUP/caller-config.sh" "$CALLER_CONFIG"
  restore_optional "$BACKUP/.wled-config" "$BACKUP/wled-config.sh" "$WLED_CONFIG"
  restore_optional "$BACKUP/.caller-service" "$BACKUP/darts-caller.service" "$CALLER_SERVICE"
  restore_optional "$BACKUP/.wled-service" "$BACKUP/darts-wled.service" "$WLED_SERVICE"
  restore_optional "$BACKUP/.caller-override" "$BACKUP/darts-caller.service.d" "$CALLER_OVERRIDE_DIR"
  restore_optional "$BACKUP/.flag" "$BACKUP/extensions-v2-installed.json" "$FLAG"

  systemctl daemon-reload 2>/dev/null || true
  systemctl reset-failed darts-caller.service darts-wled.service 2>/dev/null || true

  [[ "$CALLER_WAS_ACTIVE" == "1" ]] && systemctl start darts-caller.service 2>/dev/null || true
  [[ "$WLED_WAS_ACTIVE" == "1" ]] && systemctl start darts-wled.service 2>/dev/null || true
  log "Rollback abgeschlossen. Backup: $BACKUP"
}

cleanup() {
  local rc=$?
  trap - EXIT
  if [[ "$rc" -ne 0 ]]; then
    if [[ "$MIGRATION_STARTED" == "1" && "$SUCCESS" != "1" ]]; then
      rollback
    fi
    write_state "failed" "${LAST_ERROR:-Installation fehlgeschlagen.}"
  fi
  rm -rf "$TMP"
  exit "$rc"
}
trap cleanup EXIT

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

fetch_bundle() {
  local repo="$1"
  local binary_asset="$2"
  local manifest_asset="$3"
  local binary_out="$4"
  local manifest_out="$5"
  local meta_out="$6"

  python3 - "$repo" "$binary_asset" "$manifest_asset" "$meta_out" <<'PY'
import json, sys, urllib.request
repo, binary_name, manifest_name, out = sys.argv[1:]
url = f"https://api.github.com/repos/{repo}/releases?per_page=50"
req = urllib.request.Request(url, headers={"User-Agent": "Autodarts-Webpanel"})
with urllib.request.urlopen(req, timeout=30) as r:
    releases = json.load(r)

for rel in releases:
    if rel.get("draft"):
        continue
    assets = {a.get("name"): a for a in rel.get("assets", [])}
    binary = assets.get(binary_name)
    manifest = assets.get(manifest_name) if manifest_name else None
    if not binary:
        continue
    if manifest_name and not manifest:
        continue
    data = {
        "tag": rel.get("tag_name", "unknown"),
        "binary_url": binary.get("browser_download_url", ""),
        "manifest_url": manifest.get("browser_download_url", "") if manifest else ""
    }
    if data["binary_url"] and (not manifest_name or data["manifest_url"]):
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        raise SystemExit(0)

needed = binary_name + (f" + {manifest_name}" if manifest_name else "")
raise SystemExit(f"Kein passendes Release gefunden: {repo}: {needed}")
PY

  local binary_url manifest_url
  binary_url="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["binary_url"])' "$meta_out")"
  manifest_url="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("manifest_url",""))' "$meta_out")"

  log "Download: $binary_url"
  curl -fL --silent --show-error --retry 3 --connect-timeout 20 --max-time 300 \
    "$binary_url" -o "$binary_out"
  [[ -s "$binary_out" ]] || fail "Binary-Download ist leer: $binary_asset"
  chmod 777 "$binary_out"

  if [[ -n "$manifest_asset" ]]; then
    log "Download: $manifest_url"
    curl -fL --silent --show-error --retry 3 --connect-timeout 20 --max-time 120 \
      "$manifest_url" -o "$manifest_out"
    [[ -s "$manifest_out" ]] || fail "Manifest-Download ist leer: $manifest_asset"
    chmod 777 "$manifest_out"
  fi
}

read_tag() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tag"])' "$1"
}

read_caller_values() {
  python3 - "$CALLER_CONFIG" "${CALLER_DIR}/start-custom.sh" <<'PY'
import re, sys
paths = sys.argv[1:]
text = ""
for p in paths:
    try:
        with open(p, encoding="utf-8", errors="ignore") as f:
            text += "\n" + f.read()
    except OSError:
        pass

def first(patterns, default=""):
    for pat in patterns:
        m = re.search(pat, text, re.M)
        if m:
            return m.group(1).strip()
    return default

board = first([
    r'(?m)^[ \t]*autodarts_board_id[ \t]*=[ \t]*["\']([^"\']+)["\']',
    r'(?:^|\s)-B\s+["\']?([^"\'\s\\]+)'
])
media = first([
    r'(?m)^[ \t]*media_path[ \t]*=[ \t]*["\']?([^"\'\n#]+)',
    r'(?:^|\s)-M\s+["\']?([^"\'\s\\]+)'
], "/var/lib/autodarts/media").strip()

# Nur gültige Werte übernehmen. Leere oder auskommentierte Altwerte
# wie "caller_volume = # ..." dürfen niemals "#" ergeben.
every = first([
    r'(?m)^[ \t]*call_every_dart[ \t]*=[ \t]*["\']?([01])(?:["\']|[ \t]*(?:#.*)?$)',
    r'(?:^|\s)-E\s+["\']?([01])(?:["\']|[\s\\]|$)'
], "1")

volume = first([
    r'(?m)^[ \t]*caller_volume[ \t]*=[ \t]*["\']?([0-9]+(?:\.[0-9]+)?)(?:["\']|[ \t]*(?:#.*)?$)',
    r'(?:^|\s)-V\s+["\']?([0-9]+(?:\.[0-9]+)?)(?:["\']|[\s\\]|$)'
], "0")

local_playback = first([
    r'(?m)^[ \t]*local_playback[ \t]*=[ \t]*["\']?([01])(?:["\']|[ \t]*(?:#.*)?$)',
    r'(?:^|\s)-LPB\s+["\']?([01])(?:["\']|[\s\\]|$)'
], "0")

print(board)
print(media or "/var/lib/autodarts/media")
print(every if every in {"0", "1"} else "1")
print(volume if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", volume) else "0")
print(local_playback if local_playback in {"0", "1"} else "0")
PY
}

convert_wled_config() {
  mkdir -p "$WLED_CONFIG_DIR"

  if [[ ! -f "$WLED_CONFIG" ]]; then
    cat >"$WLED_CONFIG" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

exec "/var/lib/autodarts/extensions/darts-wled/darts-wled" \
  -CON "127.0.0.1:8079" \
  -WEPS "Dart-Led1.local" \
  -OFF 1
EOF
    chmod 777 "$WLED_CONFIG"
    return
  fi

  # Nur dann als moderne Config akzeptieren, wenn:
  # - die neue Binary gestartet wird
  # - kein alter Python/venv-Aufruf mehr enthalten ist
  # - das Pflichtargument -WEPS vorhanden ist
  # - bash -n erfolgreich ist
  if grep -qF "$WLED_BIN" "$WLED_CONFIG" \
    && ! grep -q 'darts-wled.py' "$WLED_CONFIG" \
    && ! grep -q 'source .venv/bin/activate' "$WLED_CONFIG" \
    && grep -Eq '(^|[[:space:]])-WEPS([[:space:]]|=)' "$WLED_CONFIG" \
    && bash -n "$WLED_CONFIG" >/dev/null 2>&1; then
    chmod 777 "$WLED_CONFIG"
    return
  fi

  python3 - "$WLED_CONFIG" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
start = None
for i, line in enumerate(lines):
    compact = " ".join(line.strip().split())
    if "darts-wled.py" in compact and ("exec python" in compact or "python darts-wled.py" in compact):
        start = i
        break

if start is None:
    raise SystemExit(
        "WLED-Config konnte nicht automatisch migriert werden: "
        "kein alter 'darts-wled.py'-Aufruf gefunden. "
        "Bitte /var/lib/autodarts/config/darts-wled/start-custom.sh prüfen."
    )

rest = lines[start + 1:]
new_lines = [
    "#!/usr/bin/env bash",
    "set -euo pipefail",
    "",
    'exec "/var/lib/autodarts/extensions/darts-wled/darts-wled" \\',
]
new_lines.extend(rest)
path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
PY
  chmod 777 "$WLED_CONFIG"
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
# BUILD: WLED-RECONNECT-WATCHDOG-20260806-01
set -Eeuo pipefail

LOCK="/run/autodarts-wled-reconnect-watchdog.lock"
LOG="/var/log/autodarts_wled_watchdog.log"
CALLER_URL="https://127.0.0.1:8079/api/auth/status"
WLED_CONFIG="/var/lib/autodarts/config/darts-wled/start-custom.sh"

mkdir -p "$(dirname "$LOG")"
exec 9>"$LOCK"
flock -n 9 || exit 0

log() { echo "[$(date +'%F %T')] $*" >>"$LOG"; }

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

EP="${AUTODARTS_WLED_ENDPOINT:-$(read_wled_endpoint)}"
[[ -n "$EP" ]] || exit 0

AUTH="$(curl -sk --max-time 2 "$CALLER_URL" 2>/dev/null || true)"
echo "$AUTH" | grep -q '"state":"authenticated"' || exit 0

if ! curl -fsS --max-time 2 "http://${EP}/json/info" >/dev/null 2>&1; then
  exit 0
fi

if ! systemctl is-active --quiet darts-wled.service; then
  log "darts-wled ist nicht active, ESP ${EP} ist erreichbar -> start"
  systemctl start darts-wled.service >/dev/null 2>&1 || true
  exit 0
fi

if journalctl -u darts-wled.service --since "3 minutes ago" --no-pager -o cat 2>/dev/null \
  | grep -Eiq 'WLED not available|Name or service not known|WLED Controller connection lost|Connection lost: WLED|failed to resolve'; then
  log "WLED-Fehler erkannt und ESP ${EP} wieder erreichbar -> restart darts-wled"
  systemctl restart darts-wled.service >/dev/null 2>&1 || true
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

  systemctl enable autodarts-wled-reconnect-watchdog.timer >/dev/null 2>&1 || true
  systemctl start autodarts-wled-reconnect-watchdog.timer >/dev/null 2>&1 || true
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

schedule_reboot() {
  if [[ "$AUTO_REBOOT" != "1" ]]; then
    log "Automatischer Neustart wurde mit AUTO_REBOOT=0 deaktiviert."
    return 0
  fi

  local unit="autodarts-v2-reboot-${TS}"
  log "Installation erfolgreich. Raspberry Pi startet in ${AUTO_REBOOT_DELAY} Sekunden neu."

  if command -v systemd-run >/dev/null 2>&1; then
    if systemd-run \
      --unit="$unit" \
      --on-active="${AUTO_REBOOT_DELAY}s" \
      /usr/bin/systemctl reboot >/dev/null 2>&1; then
      return 0
    fi
  fi

  # Fallback, falls systemd-run auf einem älteren System nicht verfügbar ist.
  nohup /bin/bash -c \
    "sleep '${AUTO_REBOOT_DELAY}'; /usr/bin/systemctl reboot" \
    >/dev/null 2>&1 &
}

if [[ "$(id -u)" -ne 0 ]]; then
  fail "Installer muss als root gestartet werden."
fi

if [[ "$(uname -m)" != "aarch64" ]]; then
  fail "Nur ARM64/aarch64 wird unterstützt. Gefunden: $(uname -m)"
fi

exec 9>"$LOCK"
flock -n 9 || fail "Installation läuft bereits."

write_state "running" "Neue Caller-/WLED-Version wird installiert."
log "===== V2-Migration START ====="
log "Build: CALLER-WLED-V2-SERVICEHOOK-JSONFIX-WLEDWATCH-UNITFIX-PENDINGOK-20260826-12"

mapfile -t CALLER_VALUES < <(read_caller_values)
BOARD_ID="${CALLER_VALUES[0]:-}"
MEDIA_PATH="${CALLER_VALUES[1]:-/var/lib/autodarts/media}"
EVERY_DART="${CALLER_VALUES[2]:-1}"
CALLER_VOLUME="${CALLER_VALUES[3]:-0}"
LOCAL_PLAYBACK="${CALLER_VALUES[4]:-0}"

[[ -n "$BOARD_ID" ]] || fail "Keine Board-ID gefunden. Bitte im Webpanel speichern."

CALLER_META="$TMP/caller.json"
WLED_META="$TMP/wled.json"
CALLER_NEW="$TMP/darts-caller"
WLED_NEW="$TMP/darts-wled"
MANIFEST_NEW="$TMP/manifest.sig.json"

log "Lade aktuelle ARM64-Dateien …"
fetch_bundle "$CALLER_REPO" "$CALLER_ASSET" "" \
  "$CALLER_NEW" "" "$CALLER_META"
fetch_bundle "$WLED_REPO" "$WLED_ASSET" "$WLED_MANIFEST_ASSET" \
  "$WLED_NEW" "$MANIFEST_NEW" "$WLED_META"

CALLER_VERSION="$(read_tag "$CALLER_META")"
WLED_VERSION="$(read_tag "$WLED_META")"

CALLER_WAS_ACTIVE=0
WLED_WAS_ACTIVE=0
is_active darts-caller.service && CALLER_WAS_ACTIVE=1
is_active darts-wled.service && WLED_WAS_ACTIVE=1

log "Sichere bestehende Installation nach $BACKUP"
backup_optional "$CALLER_DIR" "$BACKUP/darts-caller" "$BACKUP/.caller-dir"
backup_optional "$WLED_DIR" "$BACKUP/darts-wled" "$BACKUP/.wled-dir"
backup_optional "$CALLER_CONFIG" "$BACKUP/caller-config.sh" "$BACKUP/.caller-config"
backup_optional "$WLED_CONFIG" "$BACKUP/wled-config.sh" "$BACKUP/.wled-config"
backup_optional "$CALLER_SERVICE" "$BACKUP/darts-caller.service" "$BACKUP/.caller-service"
backup_optional "$WLED_SERVICE" "$BACKUP/darts-wled.service" "$BACKUP/.wled-service"
backup_optional "$CALLER_OVERRIDE_DIR" "$BACKUP/darts-caller.service.d" "$BACKUP/.caller-override"
backup_optional "$FLAG" "$BACKUP/extensions-v2-installed.json" "$BACKUP/.flag"

MIGRATION_STARTED=1

systemctl stop darts-wled.service 2>/dev/null || true
systemctl stop darts-caller.service 2>/dev/null || true

mkdir -p "$CALLER_DIR" "$WLED_DIR" "$CALLER_CONFIG_DIR" "$WLED_CONFIG_DIR" "$MEDIA_PATH"

install -m 0777 "$CALLER_NEW" "$CALLER_BIN"
install -m 0777 "$WLED_NEW" "$WLED_BIN"
install -m 0777 "$MANIFEST_NEW" "$WLED_MANIFEST"

cat >"${CALLER_DIR}/start-custom.sh" <<'EOF'
#!/usr/bin/env bash
exec "/var/lib/autodarts/config/darts-caller/start-custom.sh"
EOF
chmod 777 "${CALLER_DIR}/start-custom.sh"

cat >"$CALLER_CONFIG" <<EOF
#!/usr/bin/env bash
set -euo pipefail

exec "$CALLER_BIN" \\
  -B "$BOARD_ID" \\
  -M "$MEDIA_PATH" \\
  -E "$EVERY_DART" \\
  -V "$CALLER_VOLUME" \\
  -LPB "$LOCAL_PLAYBACK"
EOF
chmod 777 "$CALLER_CONFIG"

cat >"${WLED_DIR}/start-custom.sh" <<'EOF'
#!/usr/bin/env bash
exec "/var/lib/autodarts/config/darts-wled/start-custom.sh"
EOF
chmod 777 "${WLED_DIR}/start-custom.sh"

convert_wled_config

cat >"$CALLER_SERVICE" <<'EOF'
[Unit]
Description=Autodarts Extension - Darts Caller
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=0
StartLimitBurst=0

[Service]
Type=simple
WorkingDirectory=/var/lib/autodarts/extensions/darts-caller
ExecStart=/var/lib/autodarts/extensions/darts-caller/start-custom.sh
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

cat >"$WLED_SERVICE" <<'EOF'
[Unit]
Description=Autodarts Extension - Darts WLED
Wants=network-online.target
After=network-online.target darts-caller.service
Requires=darts-caller.service
PartOf=darts-caller.service
BindsTo=darts-caller.service
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory=/var/lib/autodarts/extensions/darts-wled
ExecStart=/var/lib/autodarts/extensions/darts-wled/start-custom.sh
Restart=always
RestartSec=2
TimeoutStopSec=10
KillMode=process

[Install]
WantedBy=multi-user.target
EOF

chmod 777 "$CALLER_SERVICE" "$WLED_SERVICE"
rm -rf "$CALLER_OVERRIDE_DIR"

install_wled_wait_hook
fix_wled_service_startlimit_location

systemctl daemon-reload
systemctl reset-failed darts-caller.service darts-wled.service >/dev/null 2>&1 || true
systemctl enable darts-caller.service >/dev/null 2>&1 || true

# WLED wird erst aktiviert/gestartet, wenn die Caller/WLED-Anmeldung wirklich aktiv ist.
# Bei frischer Migration ist auth_state=pending normal und darf KEIN Rollback auslösen.
CALLER_AUTHENTICATED=0

log "Starte Caller-Dienst …"
systemctl start darts-caller.service
service_stable darts-caller.service 8 || fail "Neuer Caller-Dienst startet nicht stabil."

wait_for_caller_api 35 || fail "Caller API auf https://127.0.0.1:8079/api/auth/status ist nicht erreichbar."
if wait_for_caller_authenticated 20; then
  CALLER_AUTHENTICATED=1
else
  CALLER_AUTHENTICATED=0
fi

if [[ "$CALLER_AUTHENTICATED" == "1" ]]; then
  # Kleine Beruhigungszeit, damit der Caller seinen internen Event-/WebSocket-Feed
  # vollständig initialisieren kann, bevor WLED verbindet.
  sleep 4

  log "Starte WLED-Dienst …"
  systemctl enable darts-wled.service >/dev/null 2>&1 || true
  systemctl enable --now autodarts-wled-reconnect-watchdog.timer >/dev/null 2>&1 || true
  WLED_START_SINCE="$(date +'%F %T')"
  systemctl start darts-wled.service
  service_stable darts-wled.service 12 || fail "Neuer WLED-Dienst startet nicht stabil."
  wait_for_wled_process 20 || fail "WLED-Prozess läuft nicht."
  wait_for_wled_ready_log "$WLED_START_SINCE" 45 || true
else
  log "Caller/WLED Anmeldung ist noch nicht aktiv. Installation bleibt erfolgreich; WLED wird vorerst nicht gestartet."
  log "Nach der Anmeldung kann WLED über das Webpanel oder per Service-Start aktiviert werden."
  systemctl disable --now darts-wled.service >/dev/null 2>&1 || true
  systemctl disable --now autodarts-wled-reconnect-watchdog.timer >/dev/null 2>&1 || true
  systemctl reset-failed darts-wled.service >/dev/null 2>&1 || true
fi

python3 - "$FLAG" "$CALLER_VERSION" "$WLED_VERSION" "$BACKUP" <<'PY'
import datetime, json, os, sys
path, caller, wled, backup = sys.argv[1:]
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump({
        "installed": True,
        "migration_version": 2,
        "caller_version": caller,
        "wled_version": wled,
        "installed_at": datetime.datetime.now().astimezone().isoformat(),
        "backup": backup
    }, f, ensure_ascii=False, indent=2)
os.chmod(path, 0o777)
PY

SUCCESS=1
if [[ "${CALLER_AUTHENTICATED:-0}" == "1" ]]; then
  write_state "success" "Neue Caller-/WLED-Version wurde erfolgreich installiert. Raspberry Pi wird neu gestartet."
else
  write_state "success" "Neue Caller-/WLED-Version wurde erfolgreich installiert. Caller/WLED Anmeldung ist noch ausständig; WLED wurde vorerst nicht gestartet."
fi
log "Caller installiert: $CALLER_VERSION"
log "WLED installiert: $WLED_VERSION"
log "WLED-Manifest installiert: $WLED_MANIFEST"
log "===== V2-Migration ERFOLGREICH ====="

schedule_reboot
