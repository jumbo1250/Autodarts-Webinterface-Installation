#!/usr/bin/env bash
# BUILD: CALLER-WLED-BINARY-UPDATER-20260627-03
set -Eeuo pipefail

CALLER_REPO="Peschi90/darts-caller"
WLED_REPO="Peschi90/darts-wled"
CALLER_ASSET="darts-caller-arm64"
WLED_ASSET="darts-wled-arm64"
WLED_MANIFEST_ASSET="manifest.sig.json-darts-wled-arm64"

CALLER_BIN="/var/lib/autodarts/extensions/darts-caller/darts-caller"
WLED_BIN="/var/lib/autodarts/extensions/darts-wled/darts-wled"
WLED_MANIFEST="/var/lib/autodarts/extensions/darts-wled/manifest.sig.json"

FLAG="/var/lib/autodarts/config/extensions-v2-installed.json"
LOG="/var/log/autodarts_extensions_update.log"
RESULT="/var/lib/autodarts/extensions-update-last.json"
BACKUP_ROOT="/var/lib/autodarts/config/backups"
LOCK="/run/autodarts-extensions-update.lock"

TARGET="${1:-all}"                 # all | caller | wled
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

if [[ "$NEED_CALLER" == "1" && ("$CALLER_WAS_ACTIVE" == "1" || "$WLED_WAS_ACTIVE" == "1") ]]; then
  systemctl start darts-caller.service
  service_stable darts-caller.service 5 || fail "Caller startet nach dem Update nicht stabil."
fi

if [[ "$WLED_WAS_ACTIVE" == "1" ]]; then
  systemctl start darts-wled.service
  service_stable darts-wled.service 10 || fail "WLED startet nach dem Update nicht stabil."
fi

update_flag \
  "${CALLER_VERSION:-$CURRENT_CALLER_VERSION}" \
  "${WLED_VERSION:-$CURRENT_WLED_VERSION}"

MUTATION_STARTED=0
FINAL_STATUS="success"
log "Update erfolgreich. Caller=$CALLER_STATUS ($CALLER_VERSION) WLED=$WLED_STATUS ($WLED_VERSION)"
