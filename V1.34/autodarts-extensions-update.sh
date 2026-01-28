#!/usr/bin/env bash
set -euo pipefail

# --- Einstellungen ---
CALLER_DIR="/var/lib/autodarts/extensions/darts-caller"
WLED_DIR="/var/lib/autodarts/extensions/darts-wled"

CALLER_START="${CALLER_DIR}/start-custom.sh"
WLED_WRAPPER="${WLED_DIR}/start-custom.sh"
WLED_CONFIG="/var/lib/autodarts/config/darts-wled/start-custom.sh"

BACKUP_ROOT="/var/lib/autodarts/config/backups"
TS="$(date +'%Y%m%d-%H%M%S')"
BK="${BACKUP_ROOT}/extensions-update-${TS}"

LOG_FILE="/var/log/autodarts_extensions_update.log"
RESULT_JSON="/var/lib/autodarts/extensions-update-last.json"

FORCE="${FORCE:-0}"
TARGET="${1:-all}"   # all | caller | wled

# --- Helpers ---
log() { echo "[$(date +'%F %T')] $*" >&2; }

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$RESULT_JSON")"
exec >>"$LOG_FILE" 2>&1

# Lock gegen Doppelklick
LOCK="/run/autodarts-extensions-update.lock"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"
  flock -n 9 || { log "Update läuft bereits (lock: $LOCK)."; exit 0; }
fi

CALLER_STATUS="SKIPPED"
WLED_STATUS="SKIPPED"
CALLER_VER="unknown"
WLED_VER="unknown"
ERRORS=""

write_result() {
  cat > "$RESULT_JSON" <<JSON
{
  "ts": "$(date +'%F %T')",
  "target": "$TARGET",
  "caller": "$CALLER_STATUS",
  "caller_version": "$CALLER_VER",
  "wled": "$WLED_STATUS",
  "wled_version": "$WLED_VER",
  "backup": "$BK",
  "force": "$FORCE",
  "errors": "$(echo "$ERRORS" | tr '\n' ' ' | sed 's/"/\\"/g')"
}
JSON
  chmod 666 "$RESULT_JSON" 2>/dev/null || true
}
trap write_result EXIT

exists_unitfile() {
  systemctl list-unit-files --no-legend 2>/dev/null | awk '{print $1}' | grep -qx "$1"
}
is_active() { systemctl is-active --quiet "$1"; }

stop_if_exists() {
  local svc="$1"
  if exists_unitfile "$svc"; then
    if is_active "$svc"; then
      log "Stoppe $svc ..."
      systemctl stop "$svc" || true
      echo "1"
    else
      echo "0"
    fi
  else
    echo "0"
  fi
}

restart_if_exists() {
  local svc="$1"
  if exists_unitfile "$svc"; then
    log "Starte/Restarte $svc ..."
    systemctl restart "$svc" || systemctl start "$svc" || true
  else
    log "Service nicht gefunden: $svc (ok, übersprungen)"
  fi
}

ensure_wrapper() {
  if [[ -d "$WLED_DIR" ]]; then
    log "Stelle sicher: WLED Wrapper zeigt auf Config-Startscript ..."
    cat > "$WLED_WRAPPER" <<EOF
#!/usr/bin/env bash
exec "$WLED_CONFIG"
EOF
    chmod +x "$WLED_WRAPPER" || true
  fi
  if [[ -f "$WLED_CONFIG" ]]; then
    chmod +x "$WLED_CONFIG" || true
  fi
}

backup_file_if_exists() {
  local src="$1"
  local dst="$2"
  if [[ -f "$src" ]]; then
    mkdir -p "$(dirname "$dst")"
    cp -a "$src" "$dst"
    log "Backup: $src -> $dst"
  else
    log "Backup übersprungen (nicht gefunden): $src"
  fi
}

restore_file_if_exists() {
  local src="$1"
  local dst="$2"
  if [[ -f "$src" ]]; then
    mkdir -p "$(dirname "$dst")"
    cp -a "$src" "$dst"
    log "Restore: $src -> $dst"
  else
    log "Restore übersprungen (Backup fehlt): $src"
  fi
}

canon_dir() {
  local dir="$1"
  readlink -f "$dir" 2>/dev/null || realpath -m "$dir" 2>/dev/null || echo "$dir"
}

repo_owner_user() {
  local dir="$1"
  local u
  u="$(stat -c '%U' "$dir" 2>/dev/null || echo "root")"
  [[ -n "$u" ]] || u="root"
  echo "$u"
}

run_as_user() {
  local user="$1"; shift
  if [[ "$user" == "root" ]]; then
    "$@"
    return
  fi
  if command -v runuser >/dev/null 2>&1; then
    runuser -u "$user" -- "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -u "$user" -- "$@"
  else
    # Fallback: als root ausführen (nicht ideal, aber besser als abbrechen)
    "$@"
  fi
}

git_in_repo() {
  # usage: git_in_repo <repo_dir> <git-args...>
  local dir="$1"; shift
  local rdir owner
  rdir="$(canon_dir "$dir")"
  owner="$(repo_owner_user "$rdir")"
  run_as_user "$owner" git -C "$rdir" -c safe.directory="$rdir" "$@"
}

repo_describe() {
  local dir="$1"
  local rdir
  rdir="$(canon_dir "$dir")"
  if [[ -d "$rdir/.git" ]]; then
    git_in_repo "$rdir" describe --tags --always --dirty 2>/dev/null \
      || git_in_repo "$rdir" rev-parse --short HEAD 2>/dev/null \
      || echo "unknown"
  else
    echo "no-git"
  fi
}

repo_update_status() {
  # prints: CHANGED / UNCHANGED / SKIPPED / ERROR
  local dir="$1"
  local label="$2"

  local rdir
  rdir="$(readlink -f "$dir" 2>/dev/null || realpath -m "$dir" 2>/dev/null || echo "$dir")"

  if [[ ! -d "$rdir/.git" ]]; then
    log "$label: Kein Git-Repo: $rdir (SKIPPED)"
    echo "SKIPPED"
    return 0
  fi

  # jede Git-Operation bekommt safe.directory direkt mit
  git_safe() { command git -C "$rdir" -c safe.directory="$rdir" "$@"; }

  repo_ver() {
    git_safe describe --tags --always --dirty 2>/dev/null \
      || git_safe rev-parse --short HEAD 2>/dev/null \
      || echo "unknown"
  }

  fix_git_writeability() {
    # nur reparieren was typisch Probleme macht (Locks/FETCH_HEAD + .git beschreibbar)
    rm -f "$rdir/.git/index.lock" "$rdir/.git/FETCH_HEAD" 2>/dev/null || true
    chmod -R u+rwX "$rdir/.git" 2>/dev/null || true
    # wenn wirklich root: bring owner sauber auf root (hilft bei gemischten owners)
    if [[ "$(id -u)" -eq 0 ]]; then
      chown -R root:root "$rdir/.git" 2>/dev/null || true
    fi
  }

  local before after upstream
  before="$(repo_ver)"
  log "$label: Update Repo: $rdir (before=$before)"

  # 1. Versuch
  if ! git_safe fetch --all --prune; then
    log "$label: git fetch FAIL -> versuche Repair+Retry (.git/FETCH_HEAD/locks/permissions)"
    fix_git_writeability
    if ! git_safe fetch --all --prune; then
      ERRORS="${ERRORS}\n${label}: git fetch fehlgeschlagen (Permission/readonly/ACL?)"
      log "$label: ERROR (before=$before, after=$(repo_ver))"
      echo "ERROR"
      return 0
    fi
  fi

  upstream="$(git_safe rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"

  if [[ -n "$upstream" ]]; then
    if ! git_safe reset --hard "@{u}"; then
      ERRORS="${ERRORS}\n${label}: git reset --hard @{u} fehlgeschlagen"
      log "$label: ERROR (before=$before, after=$(repo_ver))"
      echo "ERROR"
      return 0
    fi
  else
    if ! git_safe pull --rebase --autostash; then
      ERRORS="${ERRORS}\n${label}: git pull --rebase fehlgeschlagen"
      log "$label: ERROR (before=$before, after=$(repo_ver))"
      echo "ERROR"
      return 0
    fi
  fi

  after="$(repo_ver)"

  if [[ "$before" != "$after" ]]; then
    log "$label: CHANGED ($before -> $after)"
    echo "CHANGED"
  else
    log "$label: UNCHANGED ($after)"
    echo "UNCHANGED"
  fi
}

venv_refresh_install_if_needed() {
  local dir="$1"
  local label="$2"
  local changed="$3"
  local requirements="$dir/requirements.txt"

  [[ -d "$dir" ]] || return 0

  if [[ "$FORCE" == "1" ]]; then
    log "$label: FORCE=1 -> pip/venv wird ausgeführt."
  elif [[ "$changed" != "CHANGED" && -d "$dir/.venv" ]]; then
    log "$label: Keine Repo-Änderung und .venv vorhanden -> pip übersprungen."
    return 0
  fi

  log "$label: Python venv/requirements: $dir"
  pushd "$dir" >/dev/null || return 1

  export PIP_DISABLE_PIP_VERSION_CHECK=1
  export PIP_NO_INPUT=1

  if [[ ! -d ".venv" ]]; then
    if ! python3 -m venv .venv; then
      ERRORS="${ERRORS}\n${label}: python3 -m venv fehlgeschlagen (python3-venv installiert?)"
      popd >/dev/null || true
      return 0
    fi
  fi

  # shellcheck disable=SC1091
  source .venv/bin/activate

  python3 -m pip install -U pip setuptools wheel || true

  if [[ -f "$requirements" ]]; then
    if python3 -m pip install -r "$requirements" --upgrade; then
      log "$label: requirements OK"
    else
      log "$label: requirements FAIL -> Retry ohne pyinstaller/pyinstaller-hooks-contrib"
      grep -v -E '^pyinstaller(==|$)|^pyinstaller-hooks-contrib(==|$)' "$requirements" > /tmp/req-no-pyinstaller.txt
      if ! python3 -m pip install -r /tmp/req-no-pyinstaller.txt --upgrade; then
        ERRORS="${ERRORS}\n${label}: pip install requirements fehlgeschlagen"
      fi
    fi
  else
    log "$label: Keine requirements.txt gefunden (übersprungen)"
  fi

  deactivate || true
  popd >/dev/null || true
}

# ---- MAIN ----
log "===== Extensions Update START (target=${TARGET}) ====="

if [[ "$(id -u)" -ne 0 ]]; then
  log "Bitte mit sudo ausführen."
  exit 1
fi

mkdir -p "$BK"
log "Backup-Ordner: $BK"
log "FORCE=$FORCE"

DO_CALLER=0
DO_WLED=0
case "$TARGET" in
  all) DO_CALLER=1; DO_WLED=1;;
  caller) DO_CALLER=1;;
  wled) DO_WLED=1;;
  *) log "Unbekannter TARGET: $TARGET -> all"; DO_CALLER=1; DO_WLED=1;;
esac

CALLER_WAS_ACTIVE="0"
WLED_WAS_ACTIVE="0"

if [[ "$DO_CALLER" == "1" ]]; then
  CALLER_WAS_ACTIVE="$(stop_if_exists darts-caller.service)"
  backup_file_if_exists "$CALLER_START" "${BK}/darts-caller/start-custom.sh"
fi

if [[ "$DO_WLED" == "1" ]]; then
  WLED_WAS_ACTIVE="$(stop_if_exists darts-wled.service)"
  backup_file_if_exists "$WLED_WRAPPER" "${BK}/darts-wled/start-custom.wrapper.sh"
  backup_file_if_exists "$WLED_CONFIG"  "${BK}/darts-wled/start-custom.config.sh"
fi

# Repos updaten
if [[ "$DO_CALLER" == "1" ]]; then
  CALLER_STATUS="$(repo_update_status "$CALLER_DIR" "CALLER")"
fi
if [[ "$DO_WLED" == "1" ]]; then
  WLED_STATUS="$(repo_update_status "$WLED_DIR" "WLED")"
fi

# Versions/Tags ins Log (nach dem Update!)
CALLER_VER="$(repo_describe "$CALLER_DIR")"
WLED_VER="$(repo_describe "$WLED_DIR")"

# Restore start-custom
if [[ "$DO_CALLER" == "1" ]]; then
  restore_file_if_exists "${BK}/darts-caller/start-custom.sh" "$CALLER_START"
fi
if [[ "$DO_WLED" == "1" ]]; then
  restore_file_if_exists "${BK}/darts-wled/start-custom.wrapper.sh" "$WLED_WRAPPER"
  restore_file_if_exists "${BK}/darts-wled/start-custom.config.sh" "$WLED_CONFIG"
  ensure_wrapper
fi

# venv + requirements nur wenn nötig
if [[ "$DO_CALLER" == "1" ]]; then
  venv_refresh_install_if_needed "$CALLER_DIR" "CALLER" "$CALLER_STATUS" || true
fi
if [[ "$DO_WLED" == "1" ]]; then
  venv_refresh_install_if_needed "$WLED_DIR" "WLED" "$WLED_STATUS" || true
fi

# Services restart nur wenn vorher aktiv UND relevant geändert (oder FORCE oder ERROR)
if [[ "$DO_CALLER" == "1" && "$CALLER_WAS_ACTIVE" == "1" ]]; then
  if [[ "$FORCE" == "1" || "$CALLER_STATUS" == "CHANGED" || "$CALLER_STATUS" == "ERROR" ]]; then
    restart_if_exists darts-caller.service
  else
    log "CALLER: war aktiv, aber UNCHANGED -> kein Restart."
  fi
fi

if [[ "$DO_WLED" == "1" && "$WLED_WAS_ACTIVE" == "1" ]]; then
  if [[ "$FORCE" == "1" || "$WLED_STATUS" == "CHANGED" || "$WLED_STATUS" == "ERROR" ]]; then
    restart_if_exists darts-wled.service
  else
    log "WLED: war aktiv, aber UNCHANGED -> kein Restart."
  fi
fi

systemctl daemon-reload || true

log "===== SUMMARY ====="
log "CALLER: $CALLER_STATUS (version: $CALLER_VER)"
log "WLED:   $WLED_STATUS (version: $WLED_VER)"
if [[ -n "$ERRORS" ]]; then
  log "WARN/ERRORS: $ERRORS"
fi
log "Backup: $BK"
log "===== Extensions Update DONE ====="
