import os
import re
import json

from webapp.config import SETTINGS_PATH, DEFAULT_SETTINGS


def load_settings() -> dict:
    cfg = {}
    try:
        if os.path.exists(SETTINGS_PATH):
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
    except Exception:
        cfg = {}

    merged = DEFAULT_SETTINGS.copy()
    for k in merged.keys():
        if k in cfg:
            merged[k] = cfg.get(k)

    # Normalize
    pw = str(merged.get("admin_password", "1234") or "1234").strip()
    merged["admin_password"] = pw if pw else "1234"

    choices = merged.get("ap_ssid_choices")
    if not isinstance(choices, list) or not choices:
        choices = DEFAULT_SETTINGS["ap_ssid_choices"]
    # unique + max length 32
    uniq = []
    for x in choices:
        s = str(x).strip()
        if not s or len(s) > 32:
            continue
        if s not in uniq:
            uniq.append(s)
    merged["ap_ssid_choices"] = uniq or DEFAULT_SETTINGS["ap_ssid_choices"]

    merged["autodarts_update_cmd"] = str(merged.get("autodarts_update_cmd") or "").strip()

    # --- Autodarts Versionsliste / stabile Version (für UI) ---
    def _sanitize_version_str(v: str) -> str:
        v = (v or "").strip()
        v = v.lstrip("v").strip()
        low = v.lower()

        if low in ("beta",):
            return "Beta"
        if low in ("latest", "aktuellste", "neueste", "neuste"):
            return "Aktuellste"
        if re.match(r"^\d+\.\d+\.\d+(?:-(?:beta|alpha)\.\d+)?$", v):
            return v
        return ""

    versions = merged.get("autodarts_versions")
    if not isinstance(versions, list):
        versions = []
    vclean = []
    for x in versions:
        s = _sanitize_version_str(str(x))
        if s and s not in vclean:
            vclean.append(s)
    merged["autodarts_versions"] = vclean

    stable = _sanitize_version_str(str(merged.get("autodarts_stable_version") or ""))
    merged["autodarts_stable_version"] = stable

    ch = str(merged.get("webpanel_channel") or "stable").strip().lower()
    merged["webpanel_channel"] = "beta" if ch == "beta" else "stable"

    return merged


def save_setting(key: str, value) -> None:
    """Schreibt einen einzelnen Schlüssel in webpanel-settings.json (atomic)."""
    try:
        cfg = {}
        if os.path.exists(SETTINGS_PATH):
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
    except Exception:
        cfg = {}
    cfg[key] = value
    tmp = SETTINGS_PATH + ".tmp"
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, SETTINGS_PATH)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
