import os
import json
import re
import shlex
import subprocess
import time
from pathlib import Path

from webapp.config import (
    AUTODARTS_SERVICE,
    AUTODARTS_UPDATE_LOG,
    AUTODARTS_UPDATE_LOCK,
    AUTODARTS_UPDATE_RESULT,
    AUTODARTS_UPDATE_STATE,
    V2_MIGRATION_STATE_FILE,
)
from webapp.utils.system import _systemd_execstart_path


AUTODARTS_VERSION_CACHE: dict = {"ts": 0.0, "v": None}
AUTODARTS_VERSION_CACHE_TTL_SEC = 10.0


def _get_ad_user_ctx():
    try:
        with open(V2_MIGRATION_STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        return state.get("ad_user", "peter"), state.get("ad_uid", "1000")
    except Exception:
        return "peter", "1000"


def _run_as_ad_user(cmd_args, timeout=10):
    user, uid = _get_ad_user_ctx()
    return subprocess.run(
        ["sudo", "-u", user, "env",
         f"XDG_RUNTIME_DIR=/run/user/{uid}",
         f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
         f"HOME=/home/{user}",
         ] + cmd_args,
        capture_output=True, text=True, timeout=timeout
    )


def get_autodarts_binary_path() -> str | None:
    """Findet das Autodarts Binary möglichst robust."""
    # 1) aus systemd
    p = _systemd_execstart_path(AUTODARTS_SERVICE)
    if p:
        return p

    # 2) typische Orte (User/Root)
    candidates = [
        os.path.expanduser("~/.local/bin/autodarts"),
        "/home/peter/.local/bin/autodarts",
        "/home/pi/.local/bin/autodarts",
        "/root/.local/bin/autodarts",
        "/usr/local/bin/autodarts",
        "/usr/bin/autodarts",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def _version_gte(ver_str: str, minimum: str) -> bool:
    """True wenn ver_str >= minimum (z.B. '2.1.0' >= '2.1.0')."""
    try:
        raw = re.sub(r"[^0-9.]", "", (ver_str or "").split()[0]).strip(".")
        v = tuple(int(x) for x in raw.split(".") if x)
        m = tuple(int(x) for x in minimum.split(".") if x)
        return v >= m
    except Exception:
        return False


def load_autodarts_update_state() -> dict:
    try:
        with open(AUTODARTS_UPDATE_STATE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_autodarts_update_state(state: dict):
    try:
        os.makedirs(os.path.dirname(AUTODARTS_UPDATE_STATE), exist_ok=True)
        with open(AUTODARTS_UPDATE_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


def autodarts_update_is_running() -> bool:
    try:
        if not os.path.exists(AUTODARTS_UPDATE_LOCK):
            return False
        with open(AUTODARTS_UPDATE_LOCK) as f:
            info = json.load(f)
        ts = int(info.get("ts", 0))
        if time.time() - ts > 1800:
            try:
                os.remove(AUTODARTS_UPDATE_LOCK)
            except Exception:
                pass
            return False
        return True
    except Exception:
        return False


def autodarts_update_result_code() -> int | None:
    try:
        if not os.path.exists(AUTODARTS_UPDATE_RESULT):
            return None
        txt = Path(AUTODARTS_UPDATE_RESULT).read_text(encoding="utf-8", errors="ignore").strip()
        return int(txt)
    except Exception:
        return None


def get_autodarts_version() -> str | None:
    """Liest die installierte Autodarts-Version (über autodarts --version)."""
    try:
        now = time.time()
        if (now - float(AUTODARTS_VERSION_CACHE.get("ts", 0.0))) < AUTODARTS_VERSION_CACHE_TTL_SEC:
            v = AUTODARTS_VERSION_CACHE.get("v")
            if v:
                return str(v)
    except Exception:
        pass
    bin_path = get_autodarts_binary_path()
    if not bin_path:
        return None
    try:
        r = subprocess.run([bin_path, "--version"], capture_output=True, text=True, timeout=1.5)
        if r.returncode != 0:
            r = subprocess.run([bin_path, "-V"], capture_output=True, text=True, timeout=1.5)
        out = (r.stdout or r.stderr or "").strip()
        m = re.search(r"(\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?)", out)
        ver = m.group(1) if m else (out.splitlines()[0] if out else None)
        try:
            AUTODARTS_VERSION_CACHE["ts"] = time.time()
            AUTODARTS_VERSION_CACHE["v"] = ver
        except Exception:
            pass
        return ver
    except Exception:
        return None


def start_autodarts_update_background() -> tuple[bool, str]:
    """Startet `autodarts update` als Hintergrundjob (systemd-run / nohup)."""
    if autodarts_update_is_running():
        return False, "Autodarts-Update läuft bereits."

    bin_path = get_autodarts_binary_path()
    if not bin_path:
        return False, "Autodarts-Binary nicht gefunden."

    user, uid = _get_ad_user_ctx()
    version_before = get_autodarts_version()

    save_autodarts_update_state({
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version_before": version_before,
    })

    try:
        os.makedirs(os.path.dirname(AUTODARTS_UPDATE_LOCK), exist_ok=True)
        with open(AUTODARTS_UPDATE_LOCK, "w", encoding="utf-8") as f:
            json.dump({"ts": int(time.time())}, f)
    except Exception:
        pass

    for p in [AUTODARTS_UPDATE_RESULT, AUTODARTS_UPDATE_LOG]:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

    try:
        AUTODARTS_VERSION_CACHE["ts"] = 0.0
        AUTODARTS_VERSION_CACHE["v"] = None
    except Exception:
        pass

    q = shlex.quote
    ad_cmd = " ".join([
        "sudo", "-n", "-u", q(user),
        "env",
        f"HOME=/home/{q(user)}",
        f"XDG_RUNTIME_DIR=/run/user/{q(uid)}",
        f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{q(uid)}/bus",
        q(bin_path), "update",
    ])

    wrapper = (
        f"rc=0; "
        f"({ad_cmd} >> {q(AUTODARTS_UPDATE_LOG)} 2>&1) || rc=$?; "
        f"printf '%s\\n' \"$rc\" > {q(AUTODARTS_UPDATE_RESULT)}; "
        f"rm -f {q(AUTODARTS_UPDATE_LOCK)} || true"
    )

    unit_name = f"autodarts-ad-update-{int(time.time())}"
    try:
        res = subprocess.run(
            ["systemd-run", "--unit", unit_name, "--collect", "/bin/bash", "-lc", wrapper],
            capture_output=True, text=True,
        )
        if res.returncode == 0:
            return True, ""
    except Exception:
        pass

    fallback = f"nohup /bin/bash -lc {shlex.quote(wrapper)} &"
    try:
        res2 = subprocess.run(["bash", "-lc", fallback], capture_output=True, text=True)
        if res2.returncode == 0:
            return True, ""
    except Exception:
        pass

    try:
        os.remove(AUTODARTS_UPDATE_LOCK)
    except Exception:
        pass
    return False, "Autodarts-Update konnte nicht gestartet werden."


def get_autodarts_autostart_enabled() -> bool:
    try:
        r = _run_as_ad_user(["systemctl", "--user", "is-enabled", "autodarts.service"], timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def is_autodarts_active() -> bool:
    try:
        r = subprocess.run(
            ["/usr/local/bin/autodarts-userctl", "is-active"],
            capture_output=True, text=True, timeout=2.0,
        )
        return r.returncode == 0
    except Exception:
        return False
