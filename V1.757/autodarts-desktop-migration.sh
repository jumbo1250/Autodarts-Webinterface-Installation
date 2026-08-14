#!/usr/bin/env bash
# BUILD: AUTODARTS-DESKTOP-COM-FALLBACK-WALLPAPER-20260807-01
#
# Optionaler Webpanel-Migrations-Hook.
# - verändert NUR Raspberry-Pis, auf denen die bestehende Autodarts-Desktop-Funktion erkannt wird
# - behält das vorhandene Startscript inkl. Zenity/Delays/"now" bei
# - ergänzt play.autodarts.com als bevorzugte Domain mit play.autodarts.io als Fallback
# - installiert/aktiviert optional Wallpaper.png
# - erstellt vor Änderungen ein Rollback-Backup
#
# Manueller Rollback der letzten Änderung:
#   sudo /usr/local/bin/autodarts-desktop-migration.sh --rollback

set -Eeuo pipefail

USER_NAME="${AUTODARTS_DESKTOP_USER:-peter}"
USER_HOME_FROM_PASSWD="$(getent passwd "$USER_NAME" 2>/dev/null | awk -F: '{print $6}' || true)"
HOME_DIR="${AUTODARTS_HOME:-${USER_HOME_FROM_PASSWD:-/home/${USER_NAME}}}"

LAUNCHER="${AUTODARTS_LAUNCHER:-${HOME_DIR}/bin/autodarts-start.sh}"
AUTOSTART_FILE="${AUTODARTS_AUTOSTART:-/etc/xdg/autostart/chrome-Autodarts_Spiel.desktop}"
DESKTOP_ICON="${AUTODARTS_DESKTOP_ICON:-${HOME_DIR}/Desktop/Autodarts_Spiel.desktop}"
WALLPAPER_SRC="${AUTODARTS_WALLPAPER_SRC:-${HOME_DIR}/autodarts-data/Wallpaper.png}"
WALLPAPER_DST="${AUTODARTS_WALLPAPER_DST:-${HOME_DIR}/.local/share/backgrounds/Autodarts-Wallpaper.png}"

STATE_ROOT="${AUTODARTS_DESKTOP_STATE:-/var/lib/autodarts/desktop-migration}"
BACKUP_ROOT="${STATE_ROOT}/backups"
MIGRATION_TAG="AUTODARTS_DOMAIN_FALLBACK_V1"

log() {
  printf '[autodarts-desktop-migration] %s\n' "$*"
}

is_root() {
  [[ "$(id -u)" -eq 0 ]]
}

path_exists() {
  [[ -e "$1" || -L "$1" ]]
}

TARGET_FOUND=0
BACKUP_DIR=""
ROLLBACK_ARMED=0

backup_one() {
  local path="$1"
  local existed=0

  if path_exists "$path"; then
    existed=1
  fi

  printf '%s\t%s\n' "$existed" "$path" >> "${BACKUP_DIR}/files.list"

  if [[ "$existed" -eq 1 ]]; then
    mkdir -p "${BACKUP_DIR}/root$(dirname "$path")"
    cp -a "$path" "${BACKUP_DIR}/root${path}"
  fi
}

restore_backup() {
  local dir="$1"
  local existed path src

  [[ -f "${dir}/files.list" ]] || {
    log "Rollback nicht möglich: ${dir}/files.list fehlt."
    return 1
  }

  log "Rollback aus: ${dir}"

  # Rückwärts wiederherstellen, falls Pfade ineinander verschachtelt sind.
  tac "${dir}/files.list" | while IFS=$'\t' read -r existed path; do
    [[ -n "${path:-}" ]] || continue
    src="${dir}/root${path}"

    if [[ "$existed" == "1" ]]; then
      rm -rf -- "$path"
      mkdir -p -- "$(dirname "$path")"
      cp -a -- "$src" "$path"
    else
      rm -rf -- "$path"
    fi
  done

  log "Rollback abgeschlossen."
}

on_error() {
  local rc=$?
  trap - ERR
  if [[ "$ROLLBACK_ARMED" -eq 1 && -n "$BACKUP_DIR" ]]; then
    log "FEHLER (exit=${rc}) - stelle vorherigen Zustand wieder her."
    restore_backup "$BACKUP_DIR" || true
  fi
  exit "$rc"
}
trap on_error ERR

latest_backup() {
  find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr \
    | head -n1 \
    | cut -d' ' -f2-
}

if [[ "${1:-}" == "--rollback" || "${1:-}" == "rollback" ]]; then
  is_root || { log "Rollback muss als root ausgeführt werden."; exit 1; }
  dir="$(latest_backup || true)"
  [[ -n "$dir" ]] || { log "Kein Rollback-Backup gefunden."; exit 1; }
  restore_backup "$dir"
  exit 0
fi

is_root || {
  log "Dieses Script muss als root laufen (der Webpanel-Updater tut das automatisch)."
  exit 1
}

# -----------------------------------------------------------------------------
# 1) Erkennen, ob dieser Pi überhaupt die Desktop-/Autostart-Funktion besitzt.
#    Fehlt sie, wird NICHTS neu angelegt.
# -----------------------------------------------------------------------------
if [[ ! -f "$LAUNCHER" ]]; then
  log "SKIP: ${LAUNCHER} fehlt - dieser Pi besitzt die Desktop-Startfunktion offenbar nicht."
  exit 0
fi

if [[ -f "$AUTOSTART_FILE" ]] && grep -Fq "$LAUNCHER" "$AUTOSTART_FILE" 2>/dev/null; then
  TARGET_FOUND=1
fi
if [[ -f "$DESKTOP_ICON" ]] && grep -Fq "$LAUNCHER" "$DESKTOP_ICON" 2>/dev/null; then
  TARGET_FOUND=1
fi

if [[ "$TARGET_FOUND" -ne 1 ]]; then
  log "SKIP: Launcher existiert, wird aber weder vom bekannten Autostart noch vom Desktop-Icon verwendet."
  log "      Es wird aus Sicherheitsgründen nichts verändert."
  exit 0
fi

if ! grep -q 'chromium' "$LAUNCHER" 2>/dev/null; then
  log "SKIP: ${LAUNCHER} sieht nicht wie der bekannte Chromium-Launcher aus."
  exit 0
fi

NEED_LAUNCHER=0
if grep -Fq "$MIGRATION_TAG" "$LAUNCHER" 2>/dev/null; then
  log "Launcher: .com/.io-Fallback bereits vorhanden."
else
  if grep -Eq '^[[:space:]]*URL=["'"'"']https://play\.autodarts\.(io|com)/?["'"'"'][[:space:]]*$' "$LAUNCHER"; then
    NEED_LAUNCHER=1
  else
    log "WARN: Keine eindeutig erkennbare URL=play.autodarts.io/.com-Zeile gefunden."
    log "      Launcher wird nicht automatisch verändert."
  fi
fi

NEED_WALLPAPER=0
if [[ -s "$WALLPAPER_SRC" ]]; then
  if [[ ! -f "$WALLPAPER_DST" ]] || ! cmp -s "$WALLPAPER_SRC" "$WALLPAPER_DST"; then
    NEED_WALLPAPER=1
  else
    log "Wallpaper-Datei ist bereits aktuell."
  fi
else
  log "INFO: ${WALLPAPER_SRC} fehlt/ist leer - Wallpaper wird übersprungen."
fi

if [[ "$NEED_LAUNCHER" -eq 0 && "$NEED_WALLPAPER" -eq 0 ]]; then
  log "Keine Änderungen nötig."
  exit 0
fi

mkdir -p "$BACKUP_ROOT"
BACKUP_DIR="${BACKUP_ROOT}/$(date +%Y%m%d_%H%M%S)_$$"
mkdir -p "$BACKUP_DIR"
: > "${BACKUP_DIR}/files.list"

if [[ "$NEED_LAUNCHER" -eq 1 ]]; then
  backup_one "$LAUNCHER"
fi
if [[ "$NEED_WALLPAPER" -eq 1 ]]; then
  backup_one "$WALLPAPER_DST"
  # pcmanfm kann beim Setzen des Hintergrunds mehrere Dateien in diesem Ordner ändern.
  backup_one "${HOME_DIR}/.config/pcmanfm"
fi

cat > "${BACKUP_DIR}/info.txt" <<EOF2
created_at=$(date '+%Y-%m-%d %H:%M:%S')
user=${USER_NAME}
home=${HOME_DIR}
launcher=${LAUNCHER}
autostart=${AUTOSTART_FILE}
desktop_icon=${DESKTOP_ICON}
wallpaper_src=${WALLPAPER_SRC}
wallpaper_dst=${WALLPAPER_DST}
EOF2

ROLLBACK_ARMED=1

# -----------------------------------------------------------------------------
# 2) Nur die Domainlogik in das vorhandene Script einbauen.
#    Alle übrigen Zeilen (Zenity, INFO_DELAY, EXTRA_DELAY, now, Chromium-Flags)
#    bleiben unverändert.
# -----------------------------------------------------------------------------
if [[ "$NEED_LAUNCHER" -eq 1 ]]; then
  tmp_launcher="$(mktemp)"

  python3 - "$LAUNCHER" "$tmp_launcher" <<'PY'
import re
import sys

src, dst = sys.argv[1], sys.argv[2]
text = open(src, "r", encoding="utf-8").read()

url_re = re.compile(
    r'(?m)^[ \t]*URL=(["\'])https://play\.autodarts\.(?:io|com)/?\1[ \t]*$'
)

replacement = r'''# AUTODARTS_DOMAIN_FALLBACK_V1
COM_URL="https://play.autodarts.com/"
IO_URL="https://play.autodarts.io/"
URL="$COM_URL"

# Beide Domains werden parallel geprüft. .com hat immer Vorrang.
# 2xx/3xx sowie 401/403 zählen als "Server erreichbar".
probe_autodarts_url() {
    local target="$1"
    local code

    if ! command -v curl >/dev/null 2>&1; then
        printf 'FAIL\n'
        return 0
    fi

    code="$(curl -sS -L \
        --connect-timeout 2 \
        --max-time 3 \
        -A "Mozilla/5.0" \
        -o /dev/null \
        -w "%{http_code}" \
        "$target" 2>/dev/null || true)"

    case "$code" in
        2??|3??|401|403) printf 'OK\n' ;;
        *)               printf 'FAIL\n' ;;
    esac
}

start_autodarts_url_check() {
    AD_COM_RESULT="/tmp/autodarts-com-$$.result"
    AD_IO_RESULT="/tmp/autodarts-io-$$.result"

    ( probe_autodarts_url "$COM_URL" > "$AD_COM_RESULT" ) &
    AD_COM_PID=$!
    ( probe_autodarts_url "$IO_URL" > "$AD_IO_RESULT" ) &
    AD_IO_PID=$!
}

finish_autodarts_url_check() {
    wait "$AD_COM_PID" 2>/dev/null || true
    wait "$AD_IO_PID" 2>/dev/null || true

    # .com ist bevorzugt. Nur wenn .com nicht antwortet und .io antwortet,
    # wird .io verwendet. Sind BEIDE nicht erreichbar (z.B. Internet/DNS down),
    # bleibt .com die Standardwahl.
    if grep -qx 'OK' "$AD_COM_RESULT" 2>/dev/null; then
        URL="$COM_URL"
    elif grep -qx 'OK' "$AD_IO_RESULT" 2>/dev/null; then
        URL="$IO_URL"
    else
        URL="$COM_URL"
    fi

    rm -f "$AD_COM_RESULT" "$AD_IO_RESULT" 2>/dev/null || true
}
'''

text2, count = url_re.subn(lambda _m: replacement, text, count=1)
if count != 1:
    raise SystemExit("URL-Zeile konnte nicht eindeutig ersetzt werden")

sleep_re = re.compile(r'(?m)^([ \t]*)sleep[ \t]+["\']?\$EXTRA_DELAY["\']?[ \t]*$')

def repl_sleep(m):
    indent = m.group(1)
    return (
        f'{indent}start_autodarts_url_check\n'
        f'{indent}sleep "$EXTRA_DELAY"\n'
        f'{indent}finish_autodarts_url_check'
    )

text3, sleep_count = sleep_re.subn(repl_sleep, text2, count=1)
if sleep_count != 1:
    raise SystemExit("sleep $EXTRA_DELAY konnte nicht eindeutig gefunden werden")

open(dst, "w", encoding="utf-8", newline="\n").write(text3)
PY

  bash -n "$tmp_launcher"
  grep -Fq "$MIGRATION_TAG" "$tmp_launcher"
  grep -Fq 'https://play.autodarts.com/' "$tmp_launcher"
  grep -Fq 'https://play.autodarts.io/' "$tmp_launcher"

  old_mode="$(stat -c '%a' "$LAUNCHER")"
  old_uid="$(stat -c '%u' "$LAUNCHER")"
  old_gid="$(stat -c '%g' "$LAUNCHER")"
  install -m "$old_mode" -o "$old_uid" -g "$old_gid" "$tmp_launcher" "$LAUNCHER"
  rm -f "$tmp_launcher"

  log "OK: Launcher ergänzt (.com bevorzugt, .io Fallback)."
fi

# -----------------------------------------------------------------------------
# 3) Wallpaper installieren und möglichst sofort aktivieren.
# -----------------------------------------------------------------------------
set_wallpaper_live() {
  command -v pcmanfm >/dev/null 2>&1 || return 1

  local uid pid env_dump profile args
  uid="$(id -u "$USER_NAME")"
  pid="$(pgrep -u "$uid" -o pcmanfm 2>/dev/null || true)"
  [[ -n "$pid" ]] || pid="$(pgrep -u "$uid" -o chromium 2>/dev/null || true)"

  local -a env_args
  env_args=("HOME=${HOME_DIR}" "USER=${USER_NAME}" "LOGNAME=${USER_NAME}")

  if [[ -n "$pid" && -r "/proc/${pid}/environ" ]]; then
    env_dump="$(tr '\0' '\n' < "/proc/${pid}/environ" 2>/dev/null || true)"
    local key val
    for key in DISPLAY WAYLAND_DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS XAUTHORITY; do
      val="$(printf '%s\n' "$env_dump" | sed -n "s/^${key}=//p" | head -n1)"
      [[ -n "$val" ]] && env_args+=("${key}=${val}")
    done
  fi

  # Sinnvolle Fallbacks für den normalen Raspberry-Pi-Desktop.
  if ! printf '%s\n' "${env_args[@]}" | grep -q '^XDG_RUNTIME_DIR=' && [[ -d "/run/user/${uid}" ]]; then
    env_args+=("XDG_RUNTIME_DIR=/run/user/${uid}")
  fi
  if ! printf '%s\n' "${env_args[@]}" | grep -q '^DISPLAY=' && [[ -S /tmp/.X11-unix/X0 ]]; then
    env_args+=("DISPLAY=:0")
  fi

  profile=""
  if [[ -n "$pid" ]]; then
    args="$(ps -o args= -p "$pid" 2>/dev/null || true)"
    profile="$(printf '%s\n' "$args" | sed -nE 's/.*--profile(=|[[:space:]])([^[:space:]]+).*/\2/p' | head -n1)"
  fi
  if [[ -z "$profile" && -d "${HOME_DIR}/.config/pcmanfm/LXDE-pi" ]]; then
    profile="LXDE-pi"
  fi

  local -a cmd
  cmd=(pcmanfm)
  [[ -n "$profile" ]] && cmd+=("--profile=${profile}")
  cmd+=("--set-wallpaper=${WALLPAPER_DST}" "--wallpaper-mode=crop")

  if command -v runuser >/dev/null 2>&1; then
    runuser -u "$USER_NAME" -- env "${env_args[@]}" "${cmd[@]}" >/dev/null 2>&1
  elif command -v sudo >/dev/null 2>&1; then
    sudo -u "$USER_NAME" env "${env_args[@]}" "${cmd[@]}" >/dev/null 2>&1
  else
    return 1
  fi
}

patch_existing_pcmanfm_configs() {
  local cfg_root="${HOME_DIR}/.config/pcmanfm"
  [[ -d "$cfg_root" ]] || return 1

  python3 - "$cfg_root" "$WALLPAPER_DST" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
wallpaper = sys.argv[2]
files = list(root.rglob("desktop-items-*.conf"))
changed = 0

for p in files:
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception:
        continue

    saw_wallpaper = False
    saw_mode = False
    out = []
    for line in lines:
        if line.startswith("wallpaper="):
            out.append(f"wallpaper={wallpaper}")
            saw_wallpaper = True
        elif line.startswith("wallpaper_mode="):
            out.append("wallpaper_mode=crop")
            saw_mode = True
        else:
            out.append(line)

    if saw_wallpaper:
        if not saw_mode:
            out.append("wallpaper_mode=crop")
        p.write_text("\n".join(out) + "\n", encoding="utf-8")
        changed += 1

print(changed)
PY
}

if [[ "$NEED_WALLPAPER" -eq 1 ]]; then
  mkdir -p "$(dirname "$WALLPAPER_DST")"
  user_uid="$(id -u "$USER_NAME")"
  user_gid="$(id -g "$USER_NAME")"
  install -m 644 -o "$user_uid" -g "$user_gid" "$WALLPAPER_SRC" "$WALLPAPER_DST"

  wallpaper_ok=0
  if set_wallpaper_live; then
    wallpaper_ok=1
    log "OK: Wallpaper sofort über pcmanfm aktiviert."
  else
    log "INFO: Live-Wallpaper konnte nicht gesetzt werden - versuche vorhandene pcmanfm-Konfiguration."
    changed="$(patch_existing_pcmanfm_configs 2>/dev/null || true)"
    if [[ "${changed:-0}" =~ ^[0-9]+$ ]] && [[ "$changed" -gt 0 ]]; then
      wallpaper_ok=1
      chown -R "$user_uid:$user_gid" "${HOME_DIR}/.config/pcmanfm" 2>/dev/null || true
      log "OK: Wallpaper in ${changed} pcmanfm-Konfiguration(en) eingetragen (spätestens nach nächstem Desktop-Start aktiv)."
    fi
  fi

  if [[ "$wallpaper_ok" -ne 1 ]]; then
    # Das Wallpaper ist optisch/optional. Deshalb NICHT den wichtigen Launcher zurückrollen.
    log "WARN: Wallpaper-Datei wurde installiert, konnte aber nicht automatisch aktiviert werden."
  fi
fi

ROLLBACK_ARMED=0
log "FERTIG. Backup für Rollback: ${BACKUP_DIR}"
exit 0
