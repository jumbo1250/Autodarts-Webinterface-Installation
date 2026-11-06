import os
import re
import subprocess

from webapp.config import DARTS_WLED_WATCHDOG_TIMER


SYSTEMCTL_CHECK_TIMEOUT = 2.0
SYSTEMCTL_ACTION_TIMEOUT = 20.0


def service_exists(service_name: str) -> bool:
    candidates = [
        f"/etc/systemd/system/{service_name}",
        f"/lib/systemd/system/{service_name}",
        f"/usr/lib/systemd/system/{service_name}",
    ]
    return any(os.path.exists(p) for p in candidates)


def _run_systemctl(args: list[str], timeout: float):
    try:
        return subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def service_is_active(service_name: str) -> bool:
    r = _run_systemctl(["is-active", service_name], timeout=SYSTEMCTL_CHECK_TIMEOUT)
    return bool(r and r.stdout.strip() == "active")


def _systemd_execstart_path(service_name: str) -> str | None:
    """Versucht den ExecStart-Pfad aus systemd herauszulesen (z.B. /home/peter/.local/bin/autodarts)."""
    try:
        out = subprocess.run(
            ["systemctl", "show", "-p", "ExecStart", service_name],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if out.returncode != 0:
            return None
        line = (out.stdout or "").strip()
        m = re.search(r"/[^\s;]+", line)
        if m:
            p = m.group(0).strip()
            return p if os.path.exists(p) else p
    except Exception:
        pass
    return None


def service_enable_now(service_name: str):
    _run_systemctl(["enable", "--now", service_name], timeout=SYSTEMCTL_ACTION_TIMEOUT)


def service_enable(service_name: str):
    _run_systemctl(["enable", service_name], timeout=SYSTEMCTL_ACTION_TIMEOUT)


def service_disable_now(service_name: str):
    _run_systemctl(["disable", "--now", service_name], timeout=SYSTEMCTL_ACTION_TIMEOUT)


def service_restart(service_name: str):
    _run_systemctl(["restart", service_name], timeout=SYSTEMCTL_ACTION_TIMEOUT)


def service_stop(service_name: str):
    _run_systemctl(["stop", service_name], timeout=SYSTEMCTL_ACTION_TIMEOUT)


def service_is_enabled(service_name: str) -> bool:
    r = _run_systemctl(["is-enabled", service_name], timeout=SYSTEMCTL_CHECK_TIMEOUT)
    return bool(r and r.stdout.strip() == "enabled")


def wled_watchdog_timer_exists() -> bool:
    return service_exists(DARTS_WLED_WATCHDOG_TIMER)


def wled_watchdog_enable_now():
    if wled_watchdog_timer_exists():
        service_enable_now(DARTS_WLED_WATCHDOG_TIMER)


def wled_watchdog_disable_now():
    if wled_watchdog_timer_exists():
        service_disable_now(DARTS_WLED_WATCHDOG_TIMER)
