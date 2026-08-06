#!/usr/bin/env bash
# BUILD: CALLER-WLED-V2-AUTOREBOOT-20260629-05
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

  if grep -q '/var/lib/autodarts/extensions/darts-wled/darts-wled' "$WLED_CONFIG"; then
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
    raise SystemExit("Alter WLED-Python-Aufruf wurde nicht gefunden.")

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
log "Build: CALLER-WLED-V2-AUTOREBOOT-20260629-05"

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

[Service]
Type=simple
WorkingDirectory=/var/lib/autodarts/extensions/darts-wled
ExecStart=/var/lib/autodarts/extensions/darts-wled/start-custom.sh
Restart=always
RestartSec=2
StartLimitIntervalSec=0
TimeoutStopSec=10
KillMode=process

[Install]
WantedBy=multi-user.target
EOF

chmod 777 "$CALLER_SERVICE" "$WLED_SERVICE"
rm -rf "$CALLER_OVERRIDE_DIR"

systemctl daemon-reload
systemctl reset-failed darts-caller.service darts-wled.service >/dev/null 2>&1 || true
systemctl enable darts-caller.service darts-wled.service >/dev/null 2>&1 || true

systemctl start darts-caller.service
service_stable darts-caller.service 5 || fail "Neuer Caller-Dienst startet nicht stabil."

systemctl start darts-wled.service
service_stable darts-wled.service 10 || fail "Neuer WLED-Dienst startet nicht stabil."

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
write_state "success" "Neue Caller-/WLED-Version wurde erfolgreich installiert. Raspberry Pi wird neu gestartet."
log "Caller installiert: $CALLER_VERSION"
log "WLED installiert: $WLED_VERSION"
log "WLED-Manifest installiert: $WLED_MANIFEST"
log "===== V2-Migration ERFOLGREICH ====="

schedule_reboot
