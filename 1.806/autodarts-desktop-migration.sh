#!/usr/bin/env bash
# BUILD: AUTODARTS-DESKTOP-MIGRATION-V2-20260814-01
#
# Optionaler Webpanel-Migrations-Hook fuer bestehende Autodarts-Desktop-Pis.
#
# Funktionen:
# - erkennt vorhandene Autodarts-Desktop-Funktion und veraendert nur passende Pis
# - behaelt/ergänzt .com bevorzugt + .io Fallback (V1)
# - manueller Start ("now"): 4 Sekunden Zenity-Hinweis, KEIN Chromium-Kill
# - Chromium-Hilfe fuer sauberes Vollbild: maximized + fullscreen + Position 0,0
# - installiert optional Wallpaper.png
# - installiert optional autodarts.png als Desktop-Icon
# - erstellt/aktualisiert Desktop-Shortcuts:
#     Autodarts Spiel
#     Autodarts Einstellung -> http://127.0.0.1/
# - Backup + automatischer Rollback bei kritischem Fehler
#
# Manueller Rollback der letzten Änderung:
#   sudo /usr/local/bin/autodarts-desktop-migration.sh --rollback

set -Eeuo pipefail

USER_NAME="${AUTODARTS_DESKTOP_USER:-peter}"
USER_HOME_FROM_PASSWD="$(getent passwd "$USER_NAME" 2>/dev/null | awk -F: '{print $6}' || true)"
HOME_DIR="${AUTODARTS_HOME:-${USER_HOME_FROM_PASSWD:-/home/${USER_NAME}}}"

LAUNCHER="${AUTODARTS_LAUNCHER:-${HOME_DIR}/bin/autodarts-start.sh}"
AUTOSTART_FILE="${AUTODARTS_AUTOSTART:-/etc/xdg/autostart/chrome-Autodarts_Spiel.desktop}"
PLAY_DESKTOP="${AUTODARTS_DESKTOP_ICON:-${HOME_DIR}/Desktop/Autodarts_Spiel.desktop}"
SETTINGS_DESKTOP="${AUTODARTS_SETTINGS_ICON:-${HOME_DIR}/Desktop/Autodarts_Einstellungen.desktop}"

WALLPAPER_SRC="${AUTODARTS_WALLPAPER_SRC:-${HOME_DIR}/autodarts-data/Wallpaper.png}"
WALLPAPER_DST="${AUTODARTS_WALLPAPER_DST:-${HOME_DIR}/.local/share/backgrounds/Autodarts-Wallpaper.png}"

APP_ICON_SRC="${AUTODARTS_ICON_SRC:-${HOME_DIR}/autodarts-data/autodarts.png}"
APP_ICON_DST="${AUTODARTS_ICON_DST:-${HOME_DIR}/.local/share/icons/autodarts.png}"
# Bootstrap-Fallback: falls der bereits installierte alte Updater autodarts.png noch nicht kennt,
# kann diese V2 das Icon beim ersten Lauf selbst nachladen. Zukuenftige Updater laden es regulaer.
APP_ICON_URL="${AUTODARTS_ICON_URL:-https://raw.githubusercontent.com/jumbo1250/Autodarts-Webinterface-Installation/main/latest/autodarts.png}"

STATE_ROOT="${AUTODARTS_DESKTOP_STATE:-/var/lib/autodarts/desktop-migration}"
BACKUP_ROOT="${STATE_ROOT}/backups"
DOMAIN_TAG="AUTODARTS_DOMAIN_FALLBACK_V1"
TUNE_TAG="AUTODARTS_DESKTOP_TUNE_V2"

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
TMP_LAUNCHER=""
TMP_PLAY=""
TMP_SETTINGS=""

cleanup_tmp() {
  [[ -n "${TMP_LAUNCHER:-}" ]] && rm -f "$TMP_LAUNCHER" 2>/dev/null || true
  [[ -n "${TMP_PLAY:-}" ]] && rm -f "$TMP_PLAY" 2>/dev/null || true
  [[ -n "${TMP_SETTINGS:-}" ]] && rm -f "$TMP_SETTINGS" 2>/dev/null || true
}
trap cleanup_tmp EXIT

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

if ! id "$USER_NAME" >/dev/null 2>&1; then
  log "SKIP: Benutzer ${USER_NAME} existiert nicht."
  exit 0
fi

# -----------------------------------------------------------------------------
# 1) Nur bestehende Autodarts-Desktop-Pis bearbeiten.
# -----------------------------------------------------------------------------
if [[ ! -f "$LAUNCHER" ]]; then
  log "SKIP: ${LAUNCHER} fehlt - dieser Pi besitzt die Desktop-Startfunktion offenbar nicht."
  exit 0
fi

if [[ -f "$AUTOSTART_FILE" ]] && grep -Fq "$LAUNCHER" "$AUTOSTART_FILE" 2>/dev/null; then
  TARGET_FOUND=1
fi
if [[ -f "$PLAY_DESKTOP" ]] && grep -Fq "$LAUNCHER" "$PLAY_DESKTOP" 2>/dev/null; then
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

# -----------------------------------------------------------------------------
# 2) Launcher-Kandidat bauen.
#    - Domain-V1 nur ergänzen, wenn noch nicht vorhanden.
#    - V2-Tuning immer idempotent auf den gewuenschten Stand bringen.
# -----------------------------------------------------------------------------
TMP_LAUNCHER="$(mktemp)"
python3 - "$LAUNCHER" "$TMP_LAUNCHER" "$DOMAIN_TAG" "$TUNE_TAG" <<'PY'
import re
import sys

src, dst, domain_tag, tune_tag = sys.argv[1:5]
text = open(src, "r", encoding="utf-8").read()

# ---- V1: .com bevorzugt, .io Fallback --------------------------------------
if domain_tag not in text:
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
    if count == 1:
        # Nur der originale Launcher hat genau dieses sleep. Bei V1 ist es bereits ersetzt.
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
            raise SystemExit("Domain-V1: sleep $EXTRA_DELAY konnte nicht eindeutig gefunden werden")
        text = text3
    else:
        # Kein bekannter URL-Aufbau: Domainteil aus Sicherheitsgruenden nicht anfassen.
        pass

# ---- V2: manueller Start 4 Sekunden -----------------------------------------
# Bekannten now-Block finden und INFO_DELAY=4 / EXTRA_DELAY=0 setzen.
now_re = re.compile(
    r'(?ms)^(?P<indent>[ \t]*)if[ \t]+\[[ \t]+["\']?\$\{1:-\}["\']?[ \t]+=[ \t]+["\']now["\'][ \t]+\];[ \t]*then[ \t]*\n'
    r'(?P<body>.*?)'
    r'^(?P=indent)fi[ \t]*$'
)

m = now_re.search(text)
if not m:
    raise SystemExit("V2: bekannter 'now'-Block wurde nicht gefunden")

body = m.group('body')
body2, c1 = re.subn(r'(?m)^([ \t]*)INFO_DELAY=[^\n]*$', r'\1INFO_DELAY=4', body, count=1)
body3, c2 = re.subn(r'(?m)^([ \t]*)EXTRA_DELAY=[^\n]*$', r'\1EXTRA_DELAY=0', body2, count=1)
if c1 != 1 or c2 != 1:
    raise SystemExit("V2: INFO_DELAY/EXTRA_DELAY im now-Block nicht eindeutig gefunden")

new_block = m.group(0)
new_block = new_block.replace(body, body3, 1)
if tune_tag not in text:
    new_block = f'# {tune_tag}\n' + new_block
text = text[:m.start()] + new_block + text[m.end():]

# ---- V2: Chromium Fullscreen-Hilfe ------------------------------------------
# Nur die eigentliche Startzeile bearbeiten; keine Chromium-Vorkommen in Kommentaren/Funktionen.
lines = text.splitlines()
found = False
for i, line in enumerate(lines):
    stripped = line.lstrip()
    if stripped.startswith('chromium ') and '$URL' not in stripped:
        # Die bekannte erste Chromium-Kommandozeile endet mit Backslash.
        indent = line[:len(line) - len(stripped)]
        trail = ' \\' if stripped.rstrip().endswith('\\') else ''
        core = stripped.rstrip()
        if core.endswith('\\'):
            core = core[:-1].rstrip()
        tokens = core.split()
        # tokens[0] == chromium
        wanted = ['--start-maximized', '--start-fullscreen', '--window-position=0,0']
        # Vorhandene Varianten entfernen, danach genau einmal einfuegen.
        filtered = [tokens[0]]
        for t in tokens[1:]:
            if t in wanted:
                continue
            filtered.append(t)
        # Nach --new-window einfuegen, sonst direkt nach chromium.
        insert_at = 2 if '--new-window' in filtered and filtered.index('--new-window') == 1 else 1
        for w in reversed(wanted):
            filtered.insert(insert_at, w)
        lines[i] = indent + ' '.join(filtered) + trail
        found = True
        break

if not found:
    raise SystemExit("V2: Chromium-Startzeile wurde nicht gefunden")

text = '\n'.join(lines) + ('\n' if text.endswith('\n') else '')
open(dst, "w", encoding="utf-8", newline="\n").write(text)
PY

bash -n "$TMP_LAUNCHER"
NEED_LAUNCHER=0
if ! cmp -s "$LAUNCHER" "$TMP_LAUNCHER"; then
  NEED_LAUNCHER=1
fi

# -----------------------------------------------------------------------------
# 3) Optionale Dateien / Shortcuts vorbereiten.
# -----------------------------------------------------------------------------
# Bootstrap fuer genau den Fall: alter bereits installierter Updater kennt autodarts.png noch nicht.
if [[ ! -s "$APP_ICON_SRC" ]] && command -v curl >/dev/null 2>&1; then
  mkdir -p "$(dirname "$APP_ICON_SRC")"
  icon_tmp="$(mktemp)"
  if curl -fsSL --retry 2 --connect-timeout 5 --max-time 30 -o "$icon_tmp" "$APP_ICON_URL" 2>/dev/null \
      && [[ -s "$icon_tmp" ]]; then
    install -m 644 "$icon_tmp" "$APP_ICON_SRC"
    chown "$USER_NAME:$(id -gn "$USER_NAME")" "$APP_ICON_SRC" 2>/dev/null || true
    log "INFO: autodarts.png wurde einmalig direkt nachgeladen (Bootstrap fuer alten Updater)."
  fi
  rm -f "$icon_tmp" 2>/dev/null || true
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

NEED_APP_ICON=0
if [[ -s "$APP_ICON_SRC" ]]; then
  if [[ ! -f "$APP_ICON_DST" ]] || ! cmp -s "$APP_ICON_SRC" "$APP_ICON_DST"; then
    NEED_APP_ICON=1
  else
    log "Autodarts-Icon ist bereits aktuell."
  fi
else
  log "INFO: ${APP_ICON_SRC} fehlt/ist leer - eigenes Autodarts-Icon wird übersprungen."
fi

ICON_FOR_SHORTCUT="$APP_ICON_DST"
if [[ ! -s "$APP_ICON_SRC" && ! -s "$APP_ICON_DST" ]]; then
  ICON_FOR_SHORTCUT="applications-games"
fi

mkdir -p "${HOME_DIR}/Desktop"
TMP_PLAY="$(mktemp)"
cat > "$TMP_PLAY" <<EOF2
[Desktop Entry]
Version=1.0
Type=Application
Name=Autodarts Spiel
Comment=Startet Autodarts im Vollbild.
Exec=${LAUNCHER} now
Icon=${ICON_FOR_SHORTCUT}
Terminal=false
EOF2

TMP_SETTINGS="$(mktemp)"
cat > "$TMP_SETTINGS" <<'EOF2'
[Desktop Entry]
Version=1.0
Type=Application
Name=Autodarts Einstellung
Comment=Öffnet das Autodarts Webinterface.
Exec=chromium --new-window --profile-directory=Default --ignore-profile-directory-if-not-exists http://127.0.0.1/
Icon=preferences-system
Terminal=false
EOF2

NEED_PLAY=0
if [[ ! -f "$PLAY_DESKTOP" ]] || ! cmp -s "$TMP_PLAY" "$PLAY_DESKTOP"; then
  NEED_PLAY=1
fi

NEED_SETTINGS=0
if [[ ! -f "$SETTINGS_DESKTOP" ]] || ! cmp -s "$TMP_SETTINGS" "$SETTINGS_DESKTOP"; then
  NEED_SETTINGS=1
fi

if [[ "$NEED_LAUNCHER" -eq 0 && "$NEED_WALLPAPER" -eq 0 && "$NEED_APP_ICON" -eq 0 \
   && "$NEED_PLAY" -eq 0 && "$NEED_SETTINGS" -eq 0 ]]; then
  log "Keine Änderungen nötig."
  exit 0
fi

# -----------------------------------------------------------------------------
# 4) Backup fuer alle tatsaechlich betroffenen Pfade.
# -----------------------------------------------------------------------------
mkdir -p "$BACKUP_ROOT"
BACKUP_DIR="${BACKUP_ROOT}/$(date +%Y%m%d_%H%M%S)_$$"
mkdir -p "$BACKUP_DIR"
: > "${BACKUP_DIR}/files.list"

[[ "$NEED_LAUNCHER" -eq 1 ]] && backup_one "$LAUNCHER"
[[ "$NEED_WALLPAPER" -eq 1 ]] && backup_one "$WALLPAPER_DST"
if [[ "$NEED_WALLPAPER" -eq 1 ]]; then
  backup_one "${HOME_DIR}/.config/pcmanfm"
fi
[[ "$NEED_APP_ICON" -eq 1 ]] && backup_one "$APP_ICON_DST"
[[ "$NEED_PLAY" -eq 1 ]] && backup_one "$PLAY_DESKTOP"
[[ "$NEED_SETTINGS" -eq 1 ]] && backup_one "$SETTINGS_DESKTOP"

cat > "${BACKUP_DIR}/info.txt" <<EOF2
created_at=$(date '+%Y-%m-%d %H:%M:%S')
user=${USER_NAME}
home=${HOME_DIR}
launcher=${LAUNCHER}
autostart=${AUTOSTART_FILE}
play_desktop=${PLAY_DESKTOP}
settings_desktop=${SETTINGS_DESKTOP}
wallpaper_src=${WALLPAPER_SRC}
wallpaper_dst=${WALLPAPER_DST}
icon_src=${APP_ICON_SRC}
icon_dst=${APP_ICON_DST}
EOF2

ROLLBACK_ARMED=1

# -----------------------------------------------------------------------------
# 5) Launcher installieren.
# -----------------------------------------------------------------------------
if [[ "$NEED_LAUNCHER" -eq 1 ]]; then
  old_mode="$(stat -c '%a' "$LAUNCHER")"
  old_uid="$(stat -c '%u' "$LAUNCHER")"
  old_gid="$(stat -c '%g' "$LAUNCHER")"
  install -m "$old_mode" -o "$old_uid" -g "$old_gid" "$TMP_LAUNCHER" "$LAUNCHER"
  bash -n "$LAUNCHER"
  log "OK: Launcher aktualisiert (4s manueller Start + Fullscreen-Hilfe; Domainlogik erhalten/ergänzt)."
fi

# -----------------------------------------------------------------------------
# 6) Autodarts-Icon installieren.
# -----------------------------------------------------------------------------
user_uid="$(id -u "$USER_NAME")"
user_gid="$(id -g "$USER_NAME")"

if [[ "$NEED_APP_ICON" -eq 1 ]]; then
  mkdir -p "$(dirname "$APP_ICON_DST")"
  install -m 644 -o "$user_uid" -g "$user_gid" "$APP_ICON_SRC" "$APP_ICON_DST"
  log "OK: Autodarts-Icon installiert: ${APP_ICON_DST}"
fi

# -----------------------------------------------------------------------------
# 7) Desktop-Shortcuts installieren/aktualisieren.
# -----------------------------------------------------------------------------
mark_desktop_trusted() {
  local f="$1"
  chmod 755 "$f" 2>/dev/null || true
  chown "$user_uid:$user_gid" "$f" 2>/dev/null || true

  if command -v gio >/dev/null 2>&1 && command -v runuser >/dev/null 2>&1; then
    runuser -u "$USER_NAME" -- gio set "$f" metadata::trusted true >/dev/null 2>&1 || true
  fi
}

if [[ "$NEED_PLAY" -eq 1 ]]; then
  install -m 755 -o "$user_uid" -g "$user_gid" "$TMP_PLAY" "$PLAY_DESKTOP"
  mark_desktop_trusted "$PLAY_DESKTOP"
  log "OK: Desktop-Shortcut erstellt/aktualisiert: Autodarts Spiel"
fi

if [[ "$NEED_SETTINGS" -eq 1 ]]; then
  install -m 755 -o "$user_uid" -g "$user_gid" "$TMP_SETTINGS" "$SETTINGS_DESKTOP"
  mark_desktop_trusted "$SETTINGS_DESKTOP"
  log "OK: Desktop-Shortcut erstellt/aktualisiert: Autodarts Einstellung"
fi

# -----------------------------------------------------------------------------
# 8) Wallpaper wie bisher installieren und aktivieren.
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
    log "WARN: Wallpaper-Datei wurde installiert, konnte aber nicht automatisch aktiviert werden."
  fi
fi

ROLLBACK_ARMED=0
log "FERTIG. Backup für Rollback: ${BACKUP_DIR}"
exit 0
