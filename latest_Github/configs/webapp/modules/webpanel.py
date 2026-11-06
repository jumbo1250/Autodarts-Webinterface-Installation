import os
import json
import urllib.request
from pathlib import Path

from webapp.config import (
    BASE_DIR,
    WEBPANEL_HARDCODED_VERSION,
    WEBPANEL_UI_FALLBACK_VERSION,
    WEBPANEL_VERSION_FILE,
    WEBPANEL_VERSION_URL,
    WEBPANEL_UPDATE_CHECK,
    WEBPANEL_UPDATE_STATE,
    UVC_BACKUP_ROOT,
    STATE_DIR,
)


def get_uvc_backup_info() -> dict:
    try:
        kernel = os.uname().release
    except Exception:
        import subprocess
        kernel = subprocess.run(["uname", "-r"], capture_output=True, text=True, timeout=1.0).stdout.strip() or "unknown"

    backup_dir = os.path.join(UVC_BACKUP_ROOT, kernel)
    marker_path = os.path.join(STATE_DIR, f"once-uvc-hack-{kernel}.done")
    moddir = f"/lib/modules/{kernel}/kernel/drivers/media/usb/uvc"

    backup_ko = os.path.exists(os.path.join(backup_dir, "uvcvideo.ko"))
    backup_koxz = os.path.exists(os.path.join(backup_dir, "uvcvideo.ko.xz"))
    marker_exists = os.path.exists(marker_path)

    return {
        "kernel": kernel,
        "backup_dir": backup_dir,
        "backup_exists": bool(backup_ko or backup_koxz),
        "backup_ko": bool(backup_ko),
        "backup_koxz": bool(backup_koxz),
        "marker_exists": bool(marker_exists),
        "marker_path": marker_path,
        "moddir": moddir,
        "install_safe": bool((backup_ko or backup_koxz) or not marker_exists),
    }


def get_webpanel_version() -> str | None:
    """Liest die installierte Webpanel-Version (lokale version.txt)."""
    try:
        if (WEBPANEL_HARDCODED_VERSION or "").strip():
            v = (WEBPANEL_HARDCODED_VERSION or "").strip().lstrip("v").strip()
            return v or WEBPANEL_UI_FALLBACK_VERSION
    except Exception:
        pass

    candidates = [
        WEBPANEL_VERSION_FILE,
        str(Path(BASE_DIR) / "version.txt"),
    ]
    for p in candidates:
        try:
            if p and os.path.exists(p):
                v = Path(p).read_text(encoding="utf-8", errors="ignore").strip()
                v = v.lstrip("v").strip()
                if v:
                    return v
        except Exception:
            continue
    return WEBPANEL_UI_FALLBACK_VERSION


def fetch_latest_webpanel_version(timeout_s: float = 5.0) -> str | None:
    """Liest die aktuelle Webpanel-Version aus GitHub (raw version.txt)."""
    try:
        req = urllib.request.Request(WEBPANEL_VERSION_URL, headers={"User-Agent": "AutodartsPanel"})
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            v = (r.read().decode("utf-8", errors="ignore") or "").strip()
        v = v.lstrip("v").strip()
        return v or None
    except Exception:
        return None


def load_webpanel_update_check() -> dict:
    try:
        with open(WEBPANEL_UPDATE_CHECK, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_webpanel_update_check(d: dict):
    try:
        os.makedirs(os.path.dirname(WEBPANEL_UPDATE_CHECK), exist_ok=True)
        with open(WEBPANEL_UPDATE_CHECK, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    except Exception:
        pass


def load_webpanel_update_state() -> dict:
    try:
        with open(WEBPANEL_UPDATE_STATE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_webpanel_update_state(state: dict):
    try:
        os.makedirs(os.path.dirname(WEBPANEL_UPDATE_STATE), exist_ok=True)
        with open(WEBPANEL_UPDATE_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


def webpanel_update_is_running() -> bool:
    lock_path = "/var/lib/autodarts/webpanel-update.lock"
    try:
        if not os.path.exists(lock_path):
            return False
        with open(lock_path, "r", encoding="utf-8") as f:
            info = json.load(f) or {}
        return str(info.get("mode") or "").strip() == "update"
    except Exception:
        return False


def webpanel_update_result_code() -> int | None:
    result_path = "/var/lib/autodarts/webpanel-update.rc"
    try:
        if not os.path.exists(result_path):
            return None
        raw = Path(result_path).read_text(encoding="utf-8", errors="ignore").strip()
        return int(raw)
    except Exception:
        return None
