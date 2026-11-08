#!/usr/bin/env python3
# BUILD: WEBPANEL-CALLERTOGGLE-CALLERWATCH-UPDATEFALLBACK-20260902-01

# Copyright (c) 2026 Peter Rottmann
# All rights reserved.
# Proprietary and not open source.
# No use, modification, distribution, publication, sublicensing,
# or commercial use without prior express written permission.
# Applies only to parts created by Peter Rottmann.
# Third-party components remain under their respective licenses.
# Provided "as is", without warranty.

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs"))
import json
import re
import socket
import subprocess
import shutil
import shlex
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
import urllib.request
import urllib.error
import ssl
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
import time  # für weichen Dongle-Reset
import uuid
from datetime import datetime
import threading
import pwd

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    render_template,
    send_file,
    send_from_directory,
    jsonify,
    session,
    flash,
    Response,
    stream_with_context,
    has_request_context,
    g,)


app = Flask(__name__)
app.secret_key = os.environ.get('AUTODARTS_WEB_SECRET', 'autodarts-web-admin')

# === KONFIGURATION ===
from webapp.config import *
from webapp.settings import load_settings, save_setting
from webapp.utils.i18n import *
from webapp.utils.i18n import (
    _get_current_lang_code,
    _load_all_lang_jsons,
    _resolve_default_lang_code,
    _normalize_lang_code,
    _write_lang_default_config,
    _load_lang_json,
)

WLED_STATUS_CACHE: dict[str, tuple[float, dict]] = {}

def redact_journal_line(line: str) -> str:
    if not line:
        return line
    line = re.sub(r"(?i)(\s-[Pp]\s+)(\S+)", r"\1***", line)
    line = re.sub(r"(?i)(\s-[Uu]\s+)(\S+)", r"\1***", line)
    line = re.sub(r"(?i)(\bpassword\b\s*[:=]\s*)(\S+)", r"\1***", line)
    line = re.sub(r"(?i)(\btoken\b\s*[:=]\s*)(\S+)", r"\1***", line)
    return line


# --- Settings (werden automatisch neu geladen, wenn sich webpanel-settings.json ändert) ---
SETTINGS = load_settings()
ADMIN_PASSWORD = SETTINGS.get("admin_password", "admin")
AP_SSID_CHOICES = SETTINGS.get("ap_ssid_choices", [])

_SETTINGS_MTIME = None

def _settings_mtime():
    try:
        return os.stat(SETTINGS_PATH).st_mtime
    except Exception:
        return None

def refresh_settings_if_needed(force: bool = False) -> None:
    global SETTINGS, ADMIN_PASSWORD, AP_SSID_CHOICES, _SETTINGS_MTIME
    mt = _settings_mtime()
    if force or (mt != _SETTINGS_MTIME):
        SETTINGS = load_settings()
        ADMIN_PASSWORD = SETTINGS.get("admin_password", ADMIN_PASSWORD)
        AP_SSID_CHOICES = SETTINGS.get("ap_ssid_choices", AP_SSID_CHOICES)
        _SETTINGS_MTIME = mt



@app.before_request
def _autoload_settings():
    refresh_settings_if_needed()
    g.settings = SETTINGS
    g.admin_password = ADMIN_PASSWORD
    g.ap_ssid_choices = AP_SSID_CHOICES

# === leichte Caches für Statusdaten (reduziert subprocess-Last) ===
INDEX_STATS_CACHE = {'ts': 0.0, 'data': None}
INDEX_STATS_TTL_SEC = 30.0  # Startseite: Statuswerte max. alle 30s neu holen



# =====================




# ---------------- System / Stats ----------------



def is_autodarts_v2_running() -> bool:
    """Prüft ob Autodarts v2 auf Port 3180 antwortet."""
    import socket
    try:
        socket.create_connection(("127.0.0.1", 3180), timeout=1.5).close()
        return True
    except OSError:
        return False


def is_ttyd_running() -> bool:
    """Prüft ob ttyd auf Port 7681 antwortet."""
    import socket
    try:
        socket.create_connection(("127.0.0.1", 7681), timeout=1.0).close()
        return True
    except OSError:
        return False


def get_system_stats():
    """CPU-Last (grob), RAM und Temperatur."""
    cpu_pct = None
    mem_used = None
    mem_total = None
    temp_c = None

    # CPU
    try:
        with open("/proc/loadavg", "r") as f:
            load1 = float(f.read().split()[0])
        cores = multiprocessing.cpu_count()
        cpu_pct = round(min(100.0, (load1 / cores) * 100.0), 1)
    except Exception:
        pass

    # RAM
    try:
        with open("/proc/meminfo", "r") as f:
            data = f.read().splitlines()
        kv = {}
        for line in data:
            parts = line.split(":")
            if len(parts) == 2:
                key = parts[0].strip()
                val = parts[1].strip().split()[0]
                kv[key] = int(val)
        mem_total_kb = kv.get("MemTotal")
        mem_avail_kb = kv.get("MemAvailable")
        if mem_total_kb and mem_avail_kb:
            mem_used_kb = mem_total_kb - mem_avail_kb
            mem_total = round(mem_total_kb / 1024 / 1024, 2)
            mem_used = round(mem_used_kb / 1024 / 1024, 2)
    except Exception:
        pass

    # Temperatur
    try:
        out = subprocess.run(
            ["vcgencmd", "measure_temp"],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        if out.returncode == 0:
            s = out.stdout.strip()
            if "temp=" in s and "'C" in s:
                val = s.split("temp=")[1].split("'C")[0]
                temp_c = float(val)
    except FileNotFoundError:
        pass
    except Exception:
        pass

    if temp_c is None:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp_c = int(f.read().strip()) / 1000.0
        except Exception:
            pass

    return cpu_pct, mem_used, mem_total, temp_c


def get_cpu_actual_pct():
    """Echter CPU-%, gemessen über /proc/stat mit 300 ms Abstand."""
    def _read():
        with open("/proc/stat", "r") as f:
            parts = f.readline().split()
        vals = [int(x) for x in parts[1:8]]  # user nice sys idle iowait irq softirq
        return sum(vals), vals[3]             # total, idle
    try:
        t1, i1 = _read()
        time.sleep(0.3)
        t2, i2 = _read()
        diff = t2 - t1
        if diff == 0:
            return None
        return round((1.0 - (i2 - i1) / diff) * 100.0, 1)
    except Exception:
        return None


def get_index_stats_cached():
    """
    Sammel-Status für die Startseite (mit kurzem Cache), damit nicht bei jedem Reload
    viele externe Tools gestartet werden (systemctl/nmcli/vcgencmd).
    """
    now = time.time()
    try:
        if INDEX_STATS_CACHE.get('data') and (now - float(INDEX_STATS_CACHE.get('ts', 0.0))) < INDEX_STATS_TTL_SEC:
            return INDEX_STATS_CACHE['data']
    except Exception:
        pass

    ssid, ip = get_wifi_status()
    lan_ip = get_lan_status()

    autodarts_active = is_autodarts_active()
    autodarts_version = get_autodarts_version()
    cpu_pct, mem_used, mem_total, temp_c = get_system_stats()
    current_ap_ssid = get_ap_ssid()
    ping_uplink_iface = get_ping_uplink_interface()
    net_ok = bool(ping_uplink_iface)
    ping_uplink_label = ping_iface_label(ping_uplink_iface) if ping_uplink_iface else ""

    wifi_ok = bool(ssid and ip)
    lan_ok = bool(lan_ip)

    # Dongle nur dann problematisch, wenn weder WLAN noch LAN aktiv ist
    dongle_ok = lan_ok or wifi_ok or wifi_dongle_present()

    data = (
        ssid, ip, lan_ip,
        autodarts_active, autodarts_version,
        cpu_pct, mem_used, mem_total, temp_c,
        wifi_ok, lan_ok, dongle_ok,
        net_ok, ping_uplink_label,
        current_ap_ssid,
    )
    try:
        INDEX_STATS_CACHE['ts'] = now
        INDEX_STATS_CACHE['data'] = data
    except Exception:
        pass
    return data



# ---------------- Notes / Cam config ----------------



# ---------------- systemd helpers ----------------
from webapp.utils.system import *
from webapp.modules.autodarts import *
from webapp.modules.autodarts import _version_gte, _get_ad_user_ctx, _run_as_ad_user
from webapp.modules.webpanel import *
from webapp.utils.web import *
from webapp.utils.web import _inline_notice_page
from webapp.modules.camera import *
from webapp.modules.camera import (
    _camera_mode_runtime_active,
    _set_camera_mode_state,
    _legacy_inventory_from_devices,
    _normalize_camera_slots,
    _devices_for_slots,
)

# ---------------- Blueprints ----------------
from webapp.blueprints.camera import camera_bp
from webapp.blueprints.audio import audio_bp
app.register_blueprint(camera_bp)
app.register_blueprint(audio_bp)

# ---------------- WLAN / AP (Netzwerk) ----------------
from webapp.modules.wifi import (
    get_wifi_status,
    get_lan_status,
    get_ap_ssid,
    wifi_dongle_present,
    get_ping_uplink_interface,
    ping_iface_label,
    get_active_wifi_autoconnect_state,
    get_direct_connect_state,
)
from webapp.blueprints.wifi import network_bp
app.register_blueprint(network_bp)


def start_webpanel_update_background(mode: str = "update", allow_self_update: bool = True) -> tuple[bool, str]:
    """Startet das Webpanel-Update oder UVC-Spezialjobs außerhalb des Service-CGroups."""
    if not os.path.exists(WEBPANEL_UPDATE_SCRIPT):
        return False, t("webpanel.update_script_missing", "Update-Script nicht gefunden: {path}", path=WEBPANEL_UPDATE_SCRIPT)

    mode = (mode or "update").strip() or "update"
    lock_path = "/var/lib/autodarts/webpanel-update.lock"
    result_path = "/var/lib/autodarts/webpanel-update.rc"

    def _unit_is_active(unit: str) -> bool:
        try:
            r = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True)
            return r.returncode == 0
        except Exception:
            return False

    try:
        if os.path.exists(lock_path):
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    info = json.load(f) or {}
            except Exception:
                info = {}

            unit = str(info.get("unit") or "").strip()
            ts = int(info.get("ts") or 0)

            if unit and _unit_is_active(unit):
                return False, t("webpanel.job_already_running", "Webpanel-Job läuft bereits.")
            if ts and (time.time() - ts) > 1800:
                try:
                    os.remove(lock_path)
                except Exception:
                    return False, t("webpanel.job_already_running_lock", "Webpanel-Job läuft bereits (Lock konnte nicht entfernt werden).")
            else:
                return False, t("webpanel.job_already_running", "Webpanel-Job läuft bereits.")
    except Exception:
        pass

    try:
        if os.path.exists(result_path):
            os.remove(result_path)
    except Exception:
        pass

    state = load_webpanel_update_state()
    state["started"] = time.strftime("%Y-%m-%d %H:%M:%S")
    state["finished"] = None
    state["success"] = None
    state["error"] = None
    state["mode"] = mode
    save_webpanel_update_state(state)

    unit_suffix = re.sub(r"[^a-zA-Z0-9_.-]+", "-", mode).strip("-") or "update"
    unit_name = f"autodarts-webpanel-{unit_suffix}-{int(time.time())}"

    try:
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        with open(lock_path, "w", encoding="utf-8") as f:
            json.dump({"ts": int(time.time()), "unit": unit_name, "mode": mode}, f)
    except Exception:
        pass

    mode_arg = shlex.quote(mode)

    # Kanal aus Settings lesen und als Env-Variable weitergeben
    _channel = str(load_settings().get("webpanel_channel") or "stable").strip().lower()
    _channel = "beta" if _channel == "beta" else "stable"
    channel_env = f"AUTODARTS_WEBPANEL_CHANNEL={shlex.quote(_channel)} "

    # Runner-URL: immer aus latest/ �?? der Runner entscheidet selbst ob Beta oder Stable
    _github_raw = WEBPANEL_RAW_BASE.rsplit("/", 1)[0]  # strip /latest → .../main
    _runner_url = f"{_github_raw}/latest/autodarts-webpanel-update-runner.sh"

    if allow_self_update and mode == "update":
        # Runner frisch aus latest/ laden und direkt ausführen (kein lokales Install).
        # Fallback auf lokales Script falls Runner nicht erreichbar oder Syntax-Fehler.
        _run_cmd = (
            "tmp_r=$(mktemp); "
            f"if curl -fsSL --retry 2 --connect-timeout 5 --max-time 30 {shlex.quote(_runner_url)} -o \"$tmp_r\" "
            "&& sed -i 's/\\r$//' \"$tmp_r\" 2>/dev/null "
            "&& bash -n \"$tmp_r\" 2>/dev/null; then "
            f"sudo -n {channel_env}bash \"$tmp_r\"; "
            "else "
            f"sudo -n {channel_env}{shlex.quote(WEBPANEL_UPDATE_SCRIPT)} {mode_arg}; "
            "fi; "
            "rm -f \"$tmp_r\" || true"
        )
    else:
        # UVC-Hack und andere Spezialmodi: lokales Script direkt aufrufen
        _run_cmd = f"sudo -n {channel_env}{shlex.quote(WEBPANEL_UPDATE_SCRIPT)} {mode_arg}"

    wrapper_cmd = (
        "set -euo pipefail; "
        f"lock={shlex.quote(lock_path)}; "
        "rc=0; "
        f"({_run_cmd} >> {shlex.quote(WEBPANEL_UPDATE_LOG)} 2>&1) || rc=$?; "
        f"printf '%s\\n' \"$rc\" > {shlex.quote(result_path)} || true; "
        'rm -f "$lock" || true; '
        "exit $rc"
    )

    try:
        res = subprocess.run(
            [
                "systemd-run",
                "--unit", unit_name,
                "--collect",
                "/bin/bash", "-lc", wrapper_cmd,
            ],
            capture_output=True,
            text=True,
        )

        if res.returncode == 0:
            state["unit"] = unit_name
            state["method"] = "systemd-run"
            state["mode"] = mode
            save_webpanel_update_state(state)
            return True, ""

        # Keep the lock in place while the nohup fallback is running too.
        # The fallback removes it itself when the job has finished.
        fallback = (
            "nohup /bin/bash -lc "
            + shlex.quote(
                f"rc=0; ({_run_cmd} >> {WEBPANEL_UPDATE_LOG} 2>&1) || rc=$?; "
                f"printf '%s\\n' \"$rc\" > {shlex.quote(result_path)} || true; "
                f"rm -f {shlex.quote(lock_path)} || true; exit $rc"
            )
            + " &"
        )
        res2 = subprocess.run(["bash", "-lc", fallback], capture_output=True, text=True)
        if res2.returncode == 0:
            state["unit"] = None
            state["method"] = "nohup-fallback"
            state["mode"] = mode
            state["error"] = (res.stderr or res.stdout or "").strip()[:300]
            save_webpanel_update_state(state)
            return True, state.get("error", "")

        try:
            if os.path.exists(lock_path):
                os.remove(lock_path)
        except Exception:
            pass

        state["unit"] = None
        state["method"] = "failed"
        state["mode"] = mode
        state["error"] = (res.stderr or res.stdout or "").strip()[:300]
        save_webpanel_update_state(state)
        return False, state["error"]

    except Exception as e:
        try:
            if os.path.exists(lock_path):
                os.remove(lock_path)
        except Exception:
            pass
        state["unit"] = None
        state["method"] = "exception"
        state["mode"] = mode
        state["error"] = str(e)[:300]
        save_webpanel_update_state(state)
        return False, state["error"]

# ---------------- System Update (apt + reboot) ----------------

def load_os_update_state() -> dict:
    try:
        with open(OS_UPDATE_STATE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_os_update_state(state: dict):
    try:
        os.makedirs(os.path.dirname(OS_UPDATE_STATE), exist_ok=True)
        with open(OS_UPDATE_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


def load_ufw_state() -> dict:
    try:
        with open(UFW_STATE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_ufw_state(state: dict):
    try:
        os.makedirs(os.path.dirname(UFW_STATE), exist_ok=True)
        with open(UFW_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


UFW_BIN_CANDIDATES = (
    "/usr/sbin/ufw",
    "/sbin/ufw",
    "/usr/bin/ufw",
    "/bin/ufw",
)


def _get_ufw_cmd() -> str | None:
    found = shutil.which("ufw")
    if found:
        return found
    for candidate in UFW_BIN_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def ufw_is_installed() -> bool:
    # robust gegen fehlendes /usr/sbin im PATH des Webpanel-Services
    return bool(_get_ufw_cmd())


def _run_root(cmd: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess:
    if os.geteuid() != 0:
        cmd = ["sudo", "-n"] + cmd
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _systemd_unit_state(unit_name: str, timeout: float = 5.0) -> str:
    unit_name = str(unit_name or "").strip()
    if not unit_name:
        return "missing"
    try:
        r = _run_root(["systemctl", "show", unit_name, "--property=ActiveState,LoadState", "--value"], timeout=timeout)
        if r.returncode != 0:
            return "missing"
        values = [line.strip() for line in (r.stdout or "").splitlines() if line.strip()]
        active = values[0] if len(values) > 0 else ""
        load = values[1] if len(values) > 1 else ""
        if load == "not-found":
            return "missing"
        if active in {"active", "activating", "reloading"}:
            return "running"
        if active in {"failed", "inactive", "deactivating"}:
            return "finished"
    except Exception:
        pass
    return "missing"


def ufw_refresh_state() -> dict:
    """
    Liest UFW Status (active/inactive) aus. Wird auf Button-Klick und bei hängendem Install-Status genutzt.
    """
    st = load_ufw_state() or {}
    st["checked_ts"] = time.time()
    st["checked"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    ufw_cmd = _get_ufw_cmd()
    st["installed"] = bool(ufw_cmd)

    if not st["installed"]:
        unit_state = _systemd_unit_state(st.get("unit") or "") if st.get("status") == "installing" else "missing"
        st["enabled"] = False
        if st.get("status") == "installing" and unit_state == "running":
            st["status"] = "installing"
        elif st.get("status") == "installing":
            st["status"] = "install_failed"
            tail = tail_file(UFW_LOG, n=50, max_chars=4000)
            if tail:
                st["raw"] = tail
            if not st.get("error"):
                st["error"] = t("ufw.install_not_completed", "UFW Installations-Job beendet, aber UFW wurde nicht gefunden. Bitte Log prüfen und erneut versuchen.")
        else:
            st["status"] = "not_installed"
            st["raw"] = ""
        save_ufw_state(st)
        return st

    try:
        r = _run_root([ufw_cmd, "status"], timeout=6.0)
        raw = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
        st["raw"] = raw.strip()
        st.pop("error", None)
        if "Status: active" in raw:
            st["enabled"] = True
            st["status"] = "active"
        elif "Status: inactive" in raw:
            st["enabled"] = False
            st["status"] = "inactive"
        else:
            st["enabled"] = None
            st["status"] = "unknown"
            st["error"] = (r.stderr or "").strip() or None
    except Exception as e:
        st["enabled"] = None
        st["status"] = "error"
        st["error"] = str(e)

    save_ufw_state(st)
    return st


def ufw_apply_port_rules() -> tuple[bool, str]:
    """
    Setzt die erlaubten Ports idempotent (mehrfach ausführen ist ok).
    """
    ufw_cmd = _get_ufw_cmd()
    if not ufw_cmd:
        return False, t("ufw.not_installed", "UFW ist nicht installiert.")

    logs = []
    ok = True
    for rule in UFW_PORT_RULES:
        try:
            r = _run_root([ufw_cmd, "allow", rule], timeout=10.0)
            if r.returncode != 0:
                ok = False
            out = (r.stdout or "").strip()
            err = (r.stderr or "").strip()
            if out:
                logs.append(out)
            if err:
                logs.append(err)
        except Exception as e:
            ok = False
            logs.append(str(e))

    msg = t("ufw.ports_applied", "Ports angewendet.") if ok else t("ufw.ports_applied_with_errors", "Ports angewendet, aber es gab Fehler.")
    return ok, msg + ("\n" + "\n".join(logs[-10:]) if logs else "")


def ufw_set_enabled(enable: bool) -> tuple[bool, str]:
    ufw_cmd = _get_ufw_cmd()
    if not ufw_cmd:
        return False, t("ufw.not_installed", "UFW ist nicht installiert.")

    if enable:
        ok_ports, msg_ports = ufw_apply_port_rules()
        try:
            r = _run_root([ufw_cmd, "--force", "enable"], timeout=10.0)
            ok = (r.returncode == 0) and ok_ports
            msg = t("ufw.enabled", "UFW aktiviert.") if ok else t("ufw.enable_failed", "UFW Aktivierung fehlgeschlagen.")
            extra = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
            extra = extra.strip()
            if msg_ports:
                extra = (msg_ports + "\n" + extra).strip()
            return ok, (msg + ("\n" + extra if extra else ""))
        except Exception as e:
            return False, t("ufw.enable_failed_error", "UFW Aktivierung fehlgeschlagen: {error}", error=e)
    else:
        try:
            r = _run_root([ufw_cmd, "disable"], timeout=10.0)
            ok = (r.returncode == 0)
            extra = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
            extra = extra.strip()
            return ok, (t("ufw.disabled", "UFW deaktiviert.") + ("\n" + extra if extra else ""))
        except Exception as e:
            return False, t("ufw.disable_failed_error", "UFW deaktivieren fehlgeschlagen: {error}", error=e)


def start_ufw_install_background() -> tuple[bool, str]:
    """
    Installiert UFW + setzt Ports + aktiviert UFW im Hintergrund via systemd-run.
    Damit blockiert die Weboberfläche nicht.
    """
    unit_name = f"autodarts-ufw-install-{int(time.time())}"
    st = load_ufw_state() or {}
    st["started"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st["unit"] = unit_name
    st["status"] = "installing"
    save_ufw_state(st)

    rules = " ".join([f"ufw allow {r} || true;" for r in UFW_PORT_RULES])
    cmdline = (
        "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH; "
        "set -e; "
        "echo '[ufw] apt-get update'; apt-get update -y; "
        "echo '[ufw] install'; apt-get install -y ufw; "
        f"echo '[ufw] rules'; {rules} "
        "echo '[ufw] enable'; ufw --force enable; "
        "echo '[ufw] done'; ufw status"
    )
    # Logfile
    cmdline = f"{{ {cmdline}; }} >> '{UFW_LOG}' 2>&1"

    try:
        cmd = [
            "systemd-run",
            "--unit", unit_name,
            "--no-block",
            "--collect",
            "bash", "-lc", cmdline,
        ]
        if os.geteuid() != 0:
            cmd = ["sudo", "-n"] + cmd

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            return True, t("ufw.install_started", "UFW Installation gestartet (läuft im Hintergrund).")
        st["status"] = "install_failed"
        st["error"] = (res.stderr or res.stdout or "").strip() or "systemd-run fehlgeschlagen."
        save_ufw_state(st)
        return False, st["error"]
    except Exception as e:
        st["status"] = "install_error"
        st["error"] = str(e)
        save_ufw_state(st)
        return False, str(e)


def start_os_update_background() -> tuple[bool, str]:
    """Startet 'apt update + apt upgrade' im Hintergrund und rebootet danach IMMER.

    Läuft via `systemd-run` in einem eigenen transienten Unit (damit es einen Webpanel-Restart überlebt).
    """

    state = load_os_update_state()
    state["started"] = time.strftime("%Y-%m-%d %H:%M:%S")
    state.pop("error", None)

    unit_name = f"autodarts-os-update-{int(time.time())}"

    # Non-interactive apt (damit es nicht hängen bleibt bei config prompts)
    cmdline = (
        f"exec >>{OS_UPDATE_LOG} 2>&1; "
        f"echo '===== OS Update START {time.strftime('%Y-%m-%d %H:%M:%S')} ====='; "
        f"apt-get update || echo 'apt-get update FAILED (continue)'; "
        f"DEBIAN_FRONTEND=noninteractive "
        f"apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade "
        f"|| echo 'apt-get upgrade FAILED (continue)'; "
        f"echo '===== OS Update DONE -> reboot ====='; "
        f"sync; /sbin/reboot"
    )

    try:
        cmd = [
            "sudo", "-n",
            "systemd-run",
            "--unit", unit_name,
            "--no-block",
            "--collect",
            "bash", "-lc", cmdline,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)

        if res.returncode == 0:
            state["unit"] = unit_name
            state["method"] = "systemd-run"
            save_os_update_state(state)
            return True, t("system.update_started", "System-Update gestartet – der Pi rebootet danach automatisch.")

        state["unit"] = None
        state["method"] = "systemd-run-failed"
        state["error"] = (res.stderr or res.stdout or "").strip()[:300]
        save_os_update_state(state)
        return False, t("system.update_start_failed", "System-Update konnte nicht gestartet werden: {error}", error=state["error"])

    except Exception as e:
        state["unit"] = None
        state["method"] = "exception"
        state["error"] = str(e)[:300]
        save_os_update_state(state)
        return False, state["error"]


# ---------------- Caller enable/disable state ----------------

def caller_enabled_config() -> dict:
    """Persistenter Schalter: Caller absichtlich AN/AUS. Default ist AN für bestehende Systeme."""
    data = {"enabled": True}
    try:
        if os.path.exists(CALLER_ENABLED_PATH):
            with open(CALLER_ENABLED_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f) or {}
            if isinstance(loaded, dict):
                data.update(loaded)
    except Exception:
        pass
    data["enabled"] = bool(data.get("enabled", True))
    return data


def caller_extensions_enabled() -> bool:
    return bool(caller_enabled_config().get("enabled", True))


def save_caller_enabled_config(enabled: bool):
    try:
        os.makedirs(os.path.dirname(CALLER_ENABLED_PATH), exist_ok=True)
        with open(CALLER_ENABLED_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "enabled": bool(enabled),
                "updated_at": datetime.now().astimezone().isoformat(),
                "source": "webpanel",
            }, f, ensure_ascii=False, indent=2)
        os.chmod(CALLER_ENABLED_PATH, 0o777)
    except Exception:
        pass


def sync_extensions_watchdog_for_caller_state():
    """Der gemeinsame Caller/WLED-Watcher soll laufen, wenn Caller AN ist; sonst aus sein."""
    if caller_extensions_enabled():
        wled_watchdog_enable_now()
    else:
        wled_watchdog_disable_now()


def set_caller_extensions_enabled(enabled: bool) -> tuple[bool, str]:
    """Caller absichtlich aktivieren/deaktivieren und Dienste passend setzen."""
    enabled = bool(enabled)
    save_caller_enabled_config(enabled)
    msg_parts: list[str] = []
    ok = True

    try:
        if not enabled:
            # Caller AUS bedeutet: keine Caller/WLED-Extension im Hintergrund.
            wled_watchdog_disable_now()
            if service_exists(DARTS_WLED_SERVICE):
                service_disable_now(DARTS_WLED_SERVICE)
            if service_exists(DARTS_CALLER_SERVICE):
                service_disable_now(DARTS_CALLER_SERVICE)
            try:
                _run_systemctl(["reset-failed", DARTS_CALLER_SERVICE, DARTS_WLED_SERVICE], timeout=SYSTEMCTL_ACTION_TIMEOUT)
            except Exception:
                pass
            msg_parts.append(t("caller.toggle_disabled", "Caller wurde deaktiviert. darts-caller, darts-wled und der Watcher wurden gestoppt."))
        else:
            if service_exists(DARTS_CALLER_SERVICE):
                service_enable_now(DARTS_CALLER_SERVICE)
                msg_parts.append(t("caller.toggle_enabled", "Caller wurde aktiviert und gestartet."))
            else:
                ok = False
                msg_parts.append(t("caller.toggle_service_missing", "Caller wurde aktiviert, aber darts-caller.service wurde nicht gefunden."))

            # Der Timer ist jetzt ein gemeinsamer Caller/WLED-Health-Watcher.
            # Er darf auch laufen, wenn kein WLED-Band aktiv ist, weil er den Caller überwacht.
            wled_watchdog_enable_now()

            cfg = load_wled_config()
            hosts = get_enabled_wled_hosts(cfg) if bool(cfg.get("master_enabled", True)) else []
            if hosts and service_exists(DARTS_WLED_SERVICE):
                ok_weps, msg_weps = update_darts_wled_start_custom_weps(hosts)
                msg_parts.append(msg_weps)
                service_enable(DARTS_WLED_SERVICE)
            elif not hosts:
                if service_exists(DARTS_WLED_SERVICE):
                    service_disable_now(DARTS_WLED_SERVICE)
                msg_parts.append(t("caller.toggle_wled_left_off", "Kein LED-Band aktiv → WLED bleibt aus. Der Caller-Watcher bleibt aktiv."))
    except Exception as e:
        ok = False
        msg_parts.append(t("caller.toggle_failed", "Caller-Schalter konnte nicht vollständig angewendet werden: {error}", error=e))

    return ok, "\n".join(msg_parts)


def apply_wled_services_from_config(cfg: dict, restart: bool = False) -> tuple[bool, str]:
    """Wendet die WLED-Service-Logik an und respektiert den Caller-AUS-Schalter."""
    if not service_exists(DARTS_WLED_SERVICE):
        return True, t("wled.toggle_saved_service_missing", "WLED Toggle gespeichert (Service nicht gefunden).")

    master = bool((cfg or {}).get("master_enabled", True))
    hosts = get_enabled_wled_hosts(cfg) if master else []

    if not caller_extensions_enabled():
        sync_extensions_watchdog_for_caller_state()
        service_disable_now(DARTS_WLED_SERVICE)
        return True, t("wled.caller_disabled_service_stopped", "Caller ist deaktiviert → WLED bleibt aus.")

    if (not master) or (not hosts):
        sync_extensions_watchdog_for_caller_state()
        service_disable_now(DARTS_WLED_SERVICE)
        if not master:
            return True, t("wled.disabled_service_stopped", "WLED ist deaktiviert → darts-wled wurde gestoppt.")
        return True, t("wled.no_target_service_stopped", "Kein Target aktiv → darts-wled wurde gestoppt.")

    ok_weps, msg_weps = update_darts_wled_start_custom_weps(hosts)
    if not ok_weps:
        return False, msg_weps

    sync_extensions_watchdog_for_caller_state()
    service_enable_now(DARTS_WLED_SERVICE)
    if restart:
        service_restart(DARTS_WLED_SERVICE)
    return True, msg_weps


# ---------------- Extensions V2 migration/status ----------------

def load_extensions_v2_flag() -> dict:
    try:
        with open(EXTENSIONS_V2_FLAG, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_extensions_v2_install_state() -> dict:
    try:
        with open(EXTENSIONS_V2_INSTALL_STATE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def extensions_v2_status() -> dict:
    flag = load_extensions_v2_flag()
    caller_ok = os.path.isfile(CALLER_V2_BINARY) and os.access(CALLER_V2_BINARY, os.X_OK)
    wled_ok = os.path.isfile(WLED_V2_BINARY) and os.access(WLED_V2_BINARY, os.X_OK)
    installed = bool(flag.get("installed")) and caller_ok and wled_ok
    damaged = bool(flag.get("installed")) and not installed
    state = load_extensions_v2_install_state()
    process_running = False
    try:
        probe = subprocess.run(["pgrep", "-f", "[/]usr/local/bin/autodarts-extensions-v2-install.sh"], capture_output=True, text=True)
        process_running = probe.returncode == 0
    except Exception:
        process_running = False
    if state.get("status") == "running" and not process_running:
        state = dict(state)
        state["status"] = "failed"
        state["message"] = t("extensions_v2.interrupted", "Die Installation wurde unterbrochen oder ist mit einem Fehler beendet worden.")
    return {
        "installed": installed,
        "damaged": damaged,
        "flag_present": bool(flag),
        "caller_binary": caller_ok,
        "wled_binary": wled_ok,
        "caller_version": str(flag.get("caller_version") or ""),
        "wled_version": str(flag.get("wled_version") or ""),
        "installed_at": str(flag.get("installed_at") or ""),
        "running": process_running,
        "state": state,
    }


def start_extensions_v2_install_background() -> tuple[bool, str]:
    if not os.path.isfile(EXTENSIONS_V2_INSTALL_SCRIPT):
        return False, t("extensions_v2.script_missing", "Installationsskript fehlt: {path}", path=EXTENSIONS_V2_INSTALL_SCRIPT)
    unit_name = f"autodarts-extensions-v2-install-{int(time.time())}"
    cmdline = "\n".join([
        "set -e",
        f"sed -i 's/\\r$//' {shlex.quote(EXTENSIONS_V2_INSTALL_SCRIPT)} || true",
        f"chmod +x {shlex.quote(EXTENSIONS_V2_INSTALL_SCRIPT)}",
        shlex.quote(EXTENSIONS_V2_INSTALL_SCRIPT),
        "",
    ])
    try:
        res = subprocess.run([
            "sudo", "-n", "systemd-run", "--unit", unit_name,
            "--no-block", "--collect", "bash", "-lc", cmdline,
        ], capture_output=True, text=True)
        if res.returncode != 0:
            err = (res.stderr or res.stdout or "systemd-run fehlgeschlagen").strip()
            return False, err
        return True, t("extensions_v2.started", "Installation wurde gestartet.")
    except Exception as exc:
        return False, t("extensions_v2.start_failed", "Installation konnte nicht gestartet werden: {error}", error=exc)


def start_caller_auth_reset_background() -> tuple[bool, str]:
    if not os.path.isfile(CALLER_AUTH_RESET_SCRIPT):
        return False, t("extensions_v2.auth_reset_script_missing", "Reset-Script fehlt: {path}", path=CALLER_AUTH_RESET_SCRIPT)
    unit_name = f"autodarts-caller-auth-reset-{int(time.time())}"
    cmdline = "\n".join([
        "set -e",
        f"sed -i 's/\\r$//' {shlex.quote(CALLER_AUTH_RESET_SCRIPT)} || true",
        f"chmod +x {shlex.quote(CALLER_AUTH_RESET_SCRIPT)}",
        shlex.quote(CALLER_AUTH_RESET_SCRIPT),
        "",
    ])
    try:
        res = subprocess.run([
            "sudo", "-n", "systemd-run", "--unit", unit_name,
            "--no-block", "--collect", "bash", "-lc", cmdline,
        ], capture_output=True, text=True)
        if res.returncode != 0:
            err = (res.stderr or res.stdout or "systemd-run fehlgeschlagen").strip()
            return False, err
        return True, t("extensions_v2.auth_reset_started", "Caller-Anmeldung wird zurückgesetzt. Die Anmeldeseite wird geöffnet.")
    except Exception as exc:
        return False, t("extensions_v2.auth_reset_failed", "Caller-Anmeldung konnte nicht zurückgesetzt werden: {error}", error=exc)


# ---------------- Extensions Update (darts-caller / darts-wled) ----------------

def load_extensions_update_state() -> dict:
    try:
        with open(EXTENSIONS_UPDATE_STATE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_extensions_update_state(state: dict):
    try:
        os.makedirs(os.path.dirname(EXTENSIONS_UPDATE_STATE), exist_ok=True)
        with open(EXTENSIONS_UPDATE_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


def load_extensions_update_last() -> dict:
    try:
        with open(EXTENSIONS_UPDATE_LAST, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


EXTENSIONS_UPDATE_SCRIPT_TEMPLATE = r"""#!/usr/bin/env bash
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
log() { echo "[$(date +'%F %T')] $*"; }

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
ERRORS=""

write_result() {
  cat > "$RESULT_JSON" <<JSON
{
  "ts": "$(date +'%F %T')",
  "target": "$TARGET",
  "caller": "$CALLER_STATUS",
  "wled": "$WLED_STATUS",
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

repo_update_status() {
  # prints: CHANGED / UNCHANGED / SKIPPED
  local dir="$1"
  local label="$2"

  if [[ ! -d "$dir/.git" ]]; then
    log "$label: Kein Git-Repo: $dir (SKIPPED)"
    echo "SKIPPED"
    return 0
  fi

  git config --global --add safe.directory "$dir" >/dev/null 2>&1 || true

  log "$label: Update Repo: $dir"
  pushd "$dir" >/dev/null || { echo "SKIPPED"; return 0; }

  local before after upstream
  before="$(git rev-parse HEAD 2>/dev/null || echo "")"
  upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || echo "")"

  git fetch --all --prune || true

  if [[ -n "$upstream" ]]; then
    git reset --hard "@{u}" || true
  else
    git pull --rebase --autostash || true
  fi

  after="$(git rev-parse HEAD 2>/dev/null || echo "")"
  popd >/dev/null || true

  if [[ -n "$before" && -n "$after" && "$before" != "$after" ]]; then
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

# Services restart nur wenn vorher aktiv UND relevant geändert (oder FORCE)
if [[ "$DO_CALLER" == "1" && "$CALLER_WAS_ACTIVE" == "1" ]]; then
  if [[ "$FORCE" == "1" || "$CALLER_STATUS" == "CHANGED" ]]; then
    restart_if_exists darts-caller.service
  else
    log "CALLER: war aktiv, aber UNCHANGED -> kein Restart."
  fi
fi

if [[ "$DO_WLED" == "1" && "$WLED_WAS_ACTIVE" == "1" ]]; then
  if [[ "$FORCE" == "1" || "$WLED_STATUS" == "CHANGED" ]]; then
    restart_if_exists darts-wled.service
  else
    log "WLED: war aktiv, aber UNCHANGED -> kein Restart."
  fi
fi

systemctl daemon-reload || true

log "===== SUMMARY ====="
log "CALLER: $CALLER_STATUS"
log "WLED:   $WLED_STATUS"
if [[ -n "$ERRORS" ]]; then
  log "WARN/ERRORS: $ERRORS"
fi
log "Backup: $BK"
log "===== Extensions Update DONE ====="
"""

def start_extensions_update_background(target: str = "all") -> tuple[bool, str]:
    """Startet das Extensions-Update (darts-caller + darts-wled) im Hintergrund via systemd-run.

    Button-Name im UI: "WLED UPDATE" (historisch), aktualisiert aber beide Extensions nach Bedarf.

    WICHTIG: Es wird **keine** neue Script-Version aus dem Webpanel hineingeschrieben.
    Es wird die lokal installierte Datei EXTENSIONS_UPDATE_SCRIPT ausgeführt (die du z.B.
    über dein Webpanel-Update-Script aus GitHub aktualisieren lässt).

    Das Script schreibt:
      - Log:  EXTENSIONS_UPDATE_LOG
      - Last: EXTENSIONS_UPDATE_LAST (JSON: caller/wled CHANGED/UNCHANGED)
    """
    # nur erlaubte Targets akzeptieren
    if target not in ("all", "caller", "wled"):
        target = "all"

    # State aktualisieren
    state = load_extensions_update_state()
    state["started"] = time.strftime("%Y-%m-%d %H:%M:%S")
    state["target"] = target
    state.pop("error", None)

    unit_name = f"autodarts-wled-update-{int(time.time())}"

    # Script muss lokal vorhanden sein
    if not os.path.isfile(EXTENSIONS_UPDATE_SCRIPT):
        state["unit"] = None
        state["method"] = "missing-script"
        state["error"] = t("extensions.update_script_missing", "{path} fehlt. Bitte Webpanel-Update ausführen oder das Script manuell installieren.", path=EXTENSIONS_UPDATE_SCRIPT)
        save_extensions_update_state(state)
        return False, state["error"]

    # Im Root-Context: CRLF entfernen (falls per Download reingekommen), ausführbar machen, dann starten
    cmdline = "\n".join([
        "set -e",
        f"sed -i 's/\\r$//' {EXTENSIONS_UPDATE_SCRIPT} || true",
        f"chmod +x {EXTENSIONS_UPDATE_SCRIPT} || true",
        f"{EXTENSIONS_UPDATE_SCRIPT} {target}",
        "",
    ])

    try:
        cmd = [
            "sudo", "-n",
            "systemd-run",
            "--unit", unit_name,
            "--no-block",
            "--collect",
            "bash", "-lc", cmdline,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)

        if res.returncode == 0:
            state["unit"] = unit_name
            state["method"] = "systemd-run"
            save_extensions_update_state(state)
            return True, t("extensions.update_started", "WLED UPDATE gestartet (Caller/WLED werden nur aktualisiert, wenn nötig).")

        state["unit"] = None
        state["method"] = "systemd-run-failed"
        state["error"] = (res.stderr or res.stdout or "").strip()[:300]
        save_extensions_update_state(state)
        return False, state["error"] or "systemd-run fehlgeschlagen."

    except Exception as e:
        state["unit"] = None
        state["method"] = "exception"
        state["error"] = str(e)[:300]
        save_extensions_update_state(state)
        return False, state["error"]


def caller_wled_connection_status() -> dict:
    """Liest den Caller/WLED-Anmeldestatus und prüft, ob ein Verbindungsschlüssel vorhanden ist."""
    token_paths = [p for p in CALLER_TOKEN_PATHS if os.path.exists(p)]
    caller_enabled = caller_extensions_enabled()
    caller_exists = service_exists(DARTS_CALLER_SERVICE)
    status = {
        "caller_enabled": caller_enabled,
        "caller_service_exists": caller_exists,
        "caller_service_active": service_is_active(DARTS_CALLER_SERVICE) if caller_exists else None,
        "caller_service_enabled": service_is_enabled(DARTS_CALLER_SERVICE) if caller_exists else None,
        "caller_wled_token_exists": bool(token_paths),
        "caller_wled_token_path": token_paths[0] if token_paths else None,
        "caller_wled_auth_state": "disabled" if not caller_enabled else "unknown",
        "caller_wled_auth_active": False,
        "caller_wled_account": None,
        "caller_wled_auth_error": None,
    }
    if not caller_enabled:
        return status
    if not caller_exists or not bool(status.get("caller_service_active")):
        return status
    try:
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(CALLER_AUTH_STATUS_URL, headers={"User-Agent": "AutodartsWebpanel/CallerWLED"})
        with urllib.request.urlopen(req, timeout=2.0, context=ctx) as r:
            raw = r.read(4096).decode("utf-8", "replace")
        data = json.loads(raw or "{}")
        state = str(data.get("state") or "unknown")
        status["caller_wled_auth_state"] = state
        status["caller_wled_auth_active"] = (state == "authenticated")
        status["caller_wled_account"] = data.get("account")
        status["caller_wled_auth_error"] = data.get("error")
    except Exception as e:
        status["caller_wled_auth_error"] = str(e)[:220]
    return status









# ---------------- WLED reachability ----------------

def is_wled_reachable(ip_or_host: str, timeout_sec: float = 1.2) -> bool:
    url = f"http://{ip_or_host}/json/info"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AutodartsPanel"})
        with urllib.request.urlopen(req, timeout=timeout_sec) as r:
            status = getattr(r, "status", 200)
            return 200 <= status < 300
    except (urllib.error.URLError, TimeoutError, Exception):
        return False



# ---------------- Host / HTTP Reachability (schnell + gecached) ----------------

_DNS_CACHE: dict[str, tuple[float, str | None]] = {}
_HTTP_CACHE: dict[tuple[str, float], tuple[float, bool, str | None]] = {}
_DNS_TTL_SEC = 60.0
_HTTP_TTL_SEC = 4.0

def resolve_host_to_ip_fast(host: str, timeout_s: float = 0.6) -> str | None:
    """
    Schnelle, robuste Namensauflösung (wichtig bei .local/mDNS):
    - IP bleibt IP
    - .local bevorzugt via avahi-resolve-host-name (mit Timeout)
    - sonst via getent (mit Timeout)
    - KEIN socket.gethostbyname (kann sonst lange blockieren)
    """
    host = (host or "").strip()
    if not host:
        return None

    # Wenn schon IP
    try:
        socket.inet_aton(host)
        return host
    except OSError:
        pass

    # .local -> avahi
    if host.endswith(".local") and shutil.which("avahi-resolve-host-name"):
        try:
            r = subprocess.run(
                ["avahi-resolve-host-name", "-4", host],
                capture_output=True,
                text=True,
                timeout=max(0.2, timeout_s),
            )
            if r.returncode == 0 and r.stdout.strip():
                parts = r.stdout.strip().split()
                if len(parts) >= 2:
                    return parts[1].strip()
        except Exception:
            pass

    # Fallback -> getent
    if shutil.which("getent"):
        try:
            r = subprocess.run(
                ["getent", "hosts", host],
                capture_output=True,
                text=True,
                timeout=max(0.2, timeout_s),
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().split()[0].strip()
        except Exception:
            pass

    return None


def resolve_host_to_ip(host: str) -> str | None:
    # Wrapper für bestehende Aufrufe (mit Cache)
    host = (host or "").strip()
    if not host:
        return None

    now = time.time()
    cached = _DNS_CACHE.get(host)
    if cached and (now - cached[0]) < _DNS_TTL_SEC:
        return cached[1]

    ip = resolve_host_to_ip_fast(host, timeout_s=0.6)
    _DNS_CACHE[host] = (now, ip)
    return ip


def is_http_reachable(host: str, timeout_s: float = 0.6) -> tuple[bool, str | None]:
    """
    Prüft, ob WLED unter http://<host>/json/info erreichbar ist.
    Gibt (ok, ip) zurück. ip kann None sein (z.B. wenn DNS nicht auflösbar war).
    """
    host = (host or "").strip()
    if not host:
        return False, None

    # Cache pro Host+Timeout (damit schnelle Reloads nicht 3x DNS/HTTP machen)
    key = (host, float(timeout_s))
    now = time.time()
    c = _HTTP_CACHE.get(key)
    if c and (now - c[0]) < _HTTP_TTL_SEC:
        return c[1], c[2]

    ip = resolve_host_to_ip(host)
    if not ip:
        _HTTP_CACHE[key] = (now, False, None)
        return False, None
    target = ip

    ok = False
    try:
        url = f"http://{target}/json/info"
        req = urllib.request.Request(url, headers={"User-Agent": "AutodartsPanel"})
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            status = getattr(r, "status", 200)
            data = r.read(64)  # reicht als Lebenszeichen
            ok = (200 <= status < 300) and bool(data)
    except Exception:
        ok = False

    _HTTP_CACHE[key] = (now, ok, ip)
    return ok, ip



# ---------------- WLED persistent config (Multi, migriert Legacy) ----------------

def load_wled_config() -> dict:
    """
    Multi-WLED Konfiguration laden.
    Format:
      {
        "master_enabled": true/false,
        "targets": [
          {"label":"Dart LED1","host":"Dart-Led1.local","enabled":true},
          {"label":"Dart LED2","host":"Dart-Led2.local","enabled":false},
          {"label":"Dart LED3","host":"Dart-Led3.local","enabled":false}
        ]
      }

    Migration:
      - Wenn WLED_CONFIG_PATH fehlt, aber WLED_FLAG_PATH existiert, wird von der alten Single-Variante migriert.
    """
    default_cfg = {
        "master_enabled": True,
        "targets": [
            {"label": "Dart LED1", "host": "Dart-Led1.local", "enabled": True},
            {"label": "Dart LED2", "host": "Dart-Led2.local", "enabled": False},
            {"label": "Dart LED3", "host": "Dart-Led3.local", "enabled": False},
        ],
    }

    # Neu vorhanden?
    try:
        with open(WLED_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
    except FileNotFoundError:
        cfg = None
    except Exception:
        cfg = None

    # Migration von legacy
    if cfg is None:
        legacy_enabled = True
        try:
            with open(WLED_FLAG_PATH, "r", encoding="utf-8") as f:
                d = json.load(f) or {}
                legacy_enabled = bool(d.get("enabled", True))
        except Exception:
            legacy_enabled = True

        cfg = default_cfg
        cfg["master_enabled"] = legacy_enabled
        cfg["targets"][0]["enabled"] = legacy_enabled
        cfg["targets"][0]["host"] = WLED_MDNS_NAME  # alte Single-Default

        save_wled_config(cfg)
        return cfg

    # Normalisieren / Defaults
    master_enabled = bool(cfg.get("master_enabled", True))
    targets = cfg.get("targets", [])
    if not isinstance(targets, list):
        targets = []

    # Ensure exactly 3 targets
    norm_targets = []
    for i in range(3):
        base = default_cfg["targets"][i].copy()
        if i < len(targets) and isinstance(targets[i], dict):
            base["label"] = str(targets[i].get("label", base["label"]))[:40]
            base["host"] = str(targets[i].get("host", base["host"])).strip()
            base["enabled"] = bool(targets[i].get("enabled", base["enabled"]))
        norm_targets.append(base)

    return {"master_enabled": master_enabled, "targets": norm_targets}


def save_wled_config(cfg: dict):
    os.makedirs(os.path.dirname(WLED_CONFIG_PATH), exist_ok=True)
    with open(WLED_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def get_enabled_wled_hosts(cfg: dict) -> list[str]:
    hosts: list[str] = []
    for t in cfg.get("targets", []):
        if not isinstance(t, dict):
            continue
        if bool(t.get("enabled", False)):
            h = str(t.get("host", "")).strip()
            if h:
                hosts.append(h)
    return hosts


def update_darts_wled_start_custom_weps(hosts: list[str]) -> tuple[bool, str]:
    """
    Schreibt NUR die -WEPS Zeile in /var/lib/autodarts/config/darts-wled/start-custom.sh um.
    Mapping/Presets bleiben unberührt.
    """
    if not os.path.exists(DARTS_WLED_START_CUSTOM):
        return False, t("wled.start_custom_missing", "start-custom.sh nicht gefunden: {path}", path=DARTS_WLED_START_CUSTOM)

    try:
        with open(DARTS_WLED_START_CUSTOM, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        return False, t("wled.start_custom_read_failed", "start-custom.sh konnte nicht gelesen werden: {error}", error=e)

    new_lines = []
    replaced = False
    weps_re = re.compile(r'^\s*-WEPS\b')

    for line in lines:
        if (not replaced) and weps_re.match(line):
            indent = line.split("-WEPS", 1)[0]
            has_backslash = line.rstrip().endswith("\\")
            args = " ".join([f'"{h}"' for h in hosts]) if hosts else f'"{WLED_MDNS_NAME}"'
            new_line = f"{indent}-WEPS {args} "
            new_line += "\\\n" if has_backslash else "\n"
            new_lines.append(new_line)
            replaced = True
        else:
            new_lines.append(line)

    if not replaced:
        return False, t("wled.weps_line_missing_unexpected", "Keine -WEPS Zeile in start-custom.sh gefunden (unerwartetes Format).")

    # Backup einmalig
    try:
        bak = DARTS_WLED_START_CUSTOM + ".bak"
        if not os.path.exists(bak):
            with open(bak, "w", encoding="utf-8") as f:
                f.writelines(lines)
    except Exception:
        pass

    try:
        with open(DARTS_WLED_START_CUSTOM, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    except Exception as e:
        return False, t("wled.start_custom_write_failed", "start-custom.sh konnte nicht geschrieben werden: {error}", error=e)

    return True, "start-custom.sh (-WEPS) aktualisiert."


# ---------------- WLED legacy flag compatibility ----------------



# ---------------- WLED Presets load/save ----------------

WLED_PRESET_FIXED_TYPES = {
    "-IDE": {"typeId": "player1", "label": "Spieler 1 / Player 1", "duration": False},
    "-IDE2": {"typeId": "player2", "label": "Spieler 2 / Player 2", "duration": False},
    "-IDE3": {"typeId": "player3", "label": "Spieler 3 / Player 3", "duration": False},
    "-IDE4": {"typeId": "player4", "label": "Spieler 4 / Player 4", "duration": False},
    "-IDE5": {"typeId": "player5", "label": "Spieler 5 / Player 5", "duration": False},
    "-IDE6": {"typeId": "player6", "label": "Spieler 6 / Player 6", "duration": False},
    "-G": {"typeId": "leg", "label": "Leg gewonnen / Game won", "duration": True},
    "-M": {"typeId": "match", "label": "Match gewonnen / Match won", "duration": True},
    "-B": {"typeId": "busted", "label": "Busted / Überworfen", "duration": True},
    "-PJ": {"typeId": "player_joined", "label": "Spieler beigetreten / Player joined", "duration": True},
    "-PL": {"typeId": "player_left", "label": "Spieler verlassen / Player left", "duration": True},
    "-BSE": {"typeId": "board_stop_effect", "label": "Board gestoppt / Board stopped", "duration": True},
    "-CE": {"typeId": "calibration", "label": "Kalibrierung / Calibration", "duration": True},
    "-HF": {"typeId": "high_finish", "label": "High Finish", "duration": True},
    "-TOE": {"typeId": "checkout", "label": "Checkout / Takeout", "duration": True},
    "-DSBULL": {"typeId": "bull", "label": "Bull / Bullseye", "duration": True},
    "-S0": {"typeId": "score0", "label": "Score 0 / Score 0", "duration": True},
    "-SLE": {"typeId": "sleep_effect", "label": "Sleep-Effekt / Sleep effect", "duration": True},
}
for _score in range(1, 21):
    WLED_PRESET_FIXED_TYPES[f"-DS{_score}"] = {
        "typeId": f"single_dart_{_score}",
        "label": f"Single Dart {_score}",
        "duration": True,
    }

WLED_PRESET_OPTION_TYPES = {
    "-HFO": {"typeId": "high_finish_on", "label": "High-Finish ab Score / High finish threshold", "kind": "option_int", "default": "100", "min": 2, "max": 170},
    "-OFF": {"typeId": "wled_off", "label": "Nach Match-Ende ausschalten / Turn off after match end", "kind": "option_bool", "default": "1"},
    "-SOFF": {"typeId": "wled_off_at_start", "label": "Beim Verbinden ausschalten / Turn off at start", "kind": "option_bool", "default": "1"},
    "-SLET": {"typeId": "sleep_timeout", "label": "Sleep nach Sekunden / Sleep timeout seconds", "kind": "option_int", "default": "300", "min": 1, "max": 86400},
    "-SLEOFF": {"typeId": "sleep_off_timeout", "label": "Nach Sleep aus in Minuten / Turn off after sleep minutes", "kind": "option_int", "default": "0", "min": 0, "max": 1440},
    "-BRI": {"typeId": "brightness", "label": "Effekt-Helligkeit / Effect brightness", "kind": "option_int", "default": "175", "min": 1, "max": 255},
    "-DU": {"typeId": "duration", "label": "Globale Dauer / Global duration", "kind": "option_int", "default": "5", "min": 0, "max": 999},
    "-BSS": {"typeId": "board_stop_start", "label": "Board Stop/Start Delay / Board stop-start delay", "kind": "option_float", "default": "0.4", "min": 0.0, "max": 60.0},
    "-BSW": {"typeId": "board_stop_after_win", "label": "Board nach Sieg stoppen / Stop board after win", "kind": "option_bool", "default": "1"},
}

WLED_PRESET_ADVANCED_TYPES = {
    "-DMU": {"typeId": "dart_multiplier", "label": "Dart-Multiplikator/Feld / Dart multiplier or field", "placeholder": '"3=ps|5"'},
    "-CMB": {"typeId": "combo", "label": "Dart-Kombination / Dart combo", "placeholder": '"t20,t20,t20=ps|4|d:8"'},
    "-PIDE": {"typeId": "player_idle_name", "label": "Spielername Idle / Player-name idle", "placeholder": '"john=ps|10" "jane=ps|11"'},
}


def _wled_presets_default_weps_text() -> str:
    return f'"{WLED_MDNS_NAME}"'


def _wled_presets_strip_line(line: str) -> str:
    s = (line or "").rstrip("\n")
    if s.rstrip().endswith("\\"):
        s = s.rstrip()
        s = s[:-1].rstrip()
    return s.strip()


def _wled_presets_read_file() -> list[str]:
    with open(DARTS_WLED_START_CUSTOM, "r", encoding="utf-8") as f:
        return f.readlines()


def _wled_presets_find_weps_index(lines: list[str]) -> int:
    for idx, line in enumerate(lines):
        if re.match(r'^\s*-WEPS\b', line or ""):
            return idx
    return -1


def _wled_presets_extract_weps_text(lines: list[str], weps_idx: int) -> str:
    if weps_idx < 0 or weps_idx >= len(lines):
        return _wled_presets_default_weps_text()
    cleaned = _wled_presets_strip_line(lines[weps_idx])
    m = re.match(r'^-WEPS\s+(.*)$', cleaned)
    if not m:
        return _wled_presets_default_weps_text()
    value = (m.group(1) or "").strip()
    return value or _wled_presets_default_weps_text()


def _wled_presets_block_end(lines: list[str], start_idx: int) -> int:
    idx = start_idx
    while idx < len(lines):
        stripped = (lines[idx] or "").strip()
        if stripped == "" or stripped.startswith("#"):
            idx += 1
            continue
        if re.match(r'^-[A-Za-z0-9]+\b', stripped):
            idx += 1
            continue
        break
    return idx


def _wled_presets_int(value, default: int, min_val: int = 0, max_val: int = 999) -> int:
    try:
        num = int(str(value).strip())
    except Exception:
        num = default
    return max(min_val, min(max_val, num))


def _wled_presets_float(value, default: float, min_val: float = 0.0, max_val: float = 999.0) -> str:
    try:
        num = float(str(value).strip().replace(",", "."))
    except Exception:
        num = default
    num = max(min_val, min(max_val, num))
    text = f"{num:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _wled_presets_bool(value, default: str = "1") -> str:
    raw = str(value if value is not None else default).strip().lower()
    if raw in ("1", "true", "yes", "on", "ja", "ein"):
        return "1"
    if raw in ("0", "false", "no", "off", "nein", "aus"):
        return "0"
    return "1" if str(default).strip() not in ("0", "false", "False") else "0"


def _wled_presets_clean_cli_value(value: str) -> str:
    # Keeps normal darts-wled syntax such as quotes, pipes, commas and equals,
    # but removes characters that would be dangerous in start-custom.sh.
    v = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    v = v.rstrip("\\").strip()
    for bad in (";", "`", "$", "&&", "||"):
        v = v.replace(bad, "")
    return " ".join(v.split())


def _wled_presets_needs_preset(row: dict) -> bool:
    return str((row or {}).get("kind") or "") in ("fixed", "score_exact", "score_range", "unknown", "dmu_field")


def _wled_presets_sort_key(row: dict) -> tuple[int, int, str]:
    kind = str((row or {}).get("kind") or "")
    preset = _wled_presets_int((row or {}).get("preset"), 0, 0, 999)
    arg = str((row or {}).get("arg") or "")
    group = 0 if _wled_presets_needs_preset(row) else (1 if kind.startswith("option_") else 2)
    return (group, preset if preset > 0 else 10**9, arg)


def _wled_presets_prepare_rows(rows_payload) -> list[dict]:
    rows_in = rows_payload if isinstance(rows_payload, list) else []
    normalized = [_wled_presets_normalize_row(r if isinstance(r, dict) else {}) for r in rows_in]

    # DMU is stored visually as one row per key, but written as one combined -DMU line.
    # Also convert old/expert -DMU advanced rows into visual dmu_field rows, otherwise
    # repeated saves can create multiple separate -DMU lines.
    deduped: list[dict] = []
    dmu_pos: dict[str, int] = {}

    def add_dmu_row(dmu_row: dict):
        key = str(dmu_row.get("dmuKey") or "").strip().lower()
        if not re.match(r'^(?:[123]|[sdt](?:[1-9]|1[0-9]|20)|s25|d25)$', key):
            return
        dmu_row["kind"] = "dmu_field"
        dmu_row["arg"] = "-DMU"
        dmu_row["dmuKey"] = key
        dmu_row["typeId"] = f"dmu_{key}"
        if not dmu_row.get("label"):
            dmu_row["label"] = f"DMU {key}"
        if key in dmu_pos:
            deduped[dmu_pos[key]] = dmu_row
        else:
            dmu_pos[key] = len(deduped)
            deduped.append(dmu_row)

    for row in normalized:
        if row.get("kind") == "advanced" and row.get("arg") == "-DMU":
            raw_value = str(row.get("value") or "")
            for key, preset, seconds in re.findall(r'"([^"=]+)=ps\|(\d+)(?:\|([^"|]+))?"', raw_value):
                add_dmu_row({
                    "preset": _wled_presets_int(preset, 0, 0, 999),
                    "kind": "dmu_field",
                    "typeId": f"dmu_{key.strip().lower()}",
                    "label": f"DMU {key.strip().lower()}",
                    "arg": "-DMU",
                    "duration": True,
                    "seconds": (seconds or "").strip(),
                    "dmuKey": key.strip().lower(),
                    "score": 180,
                    "from": 0,
                    "to": 60,
                })
            continue
        if row.get("kind") == "dmu_field":
            add_dmu_row(row)
        else:
            deduped.append(row)
    normalized = deduped

    used: set[int] = set()
    missing: list[dict] = []
    for row in normalized:
        if not _wled_presets_needs_preset(row):
            row["preset"] = 0
            continue
        preset = _wled_presets_int(row.get("preset"), 0, 0, 999)
        if preset >= 1 and preset not in used:
            row["preset"] = preset
            used.add(preset)
        else:
            row["preset"] = 0
            missing.append(row)

    # Reuse the first free WLED preset slot, same behavior as the old dropdown.
    # Example: if PS17 was deleted, the next new entry proposes PS17 again.
    next_preset = 1
    for row in missing:
        while next_preset in used:
            next_preset += 1
        row["preset"] = next_preset
        used.add(next_preset)
        next_preset += 1

    normalized.sort(key=_wled_presets_sort_key)
    return normalized


def _wled_presets_row_from_line(line: str) -> dict | None:
    cleaned = _wled_presets_strip_line(line)
    if not cleaned or cleaned.startswith("#"):
        return None

    m = re.match(r'^(-A\d+)\s+(\d+)-(\d+)\s+"ps\|(\d+)(?:\|([^\"]+))?"$', cleaned)
    if m:
        return {
            "preset": int(m.group(4)),
            "kind": "score_range",
            "typeId": "score_range",
            "label": "Score-Bereich / Score range",
            "arg": m.group(1),
            "duration": True,
            "seconds": (m.group(5) or "").strip(),
            "score": 180,
            "from": int(m.group(2)),
            "to": int(m.group(3)),
        }

    m = re.match(r'^(-S(\d+))\s+"ps\|(\d+)(?:\|([^\"]+))?"$', cleaned)
    if m:
        score_val = int(m.group(2))
        preset = int(m.group(3))
        if score_val == 0:
            meta = WLED_PRESET_FIXED_TYPES["-S0"]
            return {
                "preset": preset,
                "kind": "fixed",
                "typeId": meta["typeId"],
                "label": meta["label"],
                "arg": "-S0",
                "duration": True,
                "seconds": (m.group(4) or "").strip(),
                "score": 0,
                "from": 0,
                "to": 60,
            }
        return {
            "preset": preset,
            "kind": "score_exact",
            "typeId": "score_exact",
            "label": "Exakter Score / Exact score",
            "arg": m.group(1),
            "duration": True,
            "seconds": (m.group(4) or "").strip(),
            "score": score_val,
            "from": 0,
            "to": 60,
        }

    m = re.match(r'^(-(?:HFO|OFF|SOFF|SLET|SLEOFF|BRI|DU|BSS|BSW))\s+(.+)$', cleaned)
    if m:
        arg = m.group(1)
        meta = WLED_PRESET_OPTION_TYPES.get(arg) or {}
        value = _wled_presets_clean_cli_value(m.group(2)).strip('"')
        kind = meta.get("kind") or "option_text"
        if kind == "option_bool":
            value = _wled_presets_bool(value, meta.get("default", "1"))
        elif kind == "option_int":
            value = str(_wled_presets_int(value, int(meta.get("default", 0)), int(meta.get("min", 0)), int(meta.get("max", 999))))
        elif kind == "option_float":
            value = _wled_presets_float(value, float(meta.get("default", 0.0)), float(meta.get("min", 0.0)), float(meta.get("max", 999.0)))
        return {
            "preset": 0,
            "kind": kind,
            "typeId": meta.get("typeId") or arg,
            "label": meta.get("label") or arg,
            "arg": arg,
            "duration": False,
            "seconds": "",
            "value": value,
            "score": 180,
            "from": 0,
            "to": 60,
        }

    m = re.match(r'^(-(?:DMU|CMB|PIDE))\s+(.+)$', cleaned)
    if m:
        arg = m.group(1)
        meta = WLED_PRESET_ADVANCED_TYPES.get(arg) or {}
        raw_value = _wled_presets_clean_cli_value(m.group(2))
        if arg == "-DMU":
            # Visual UI stores every DMU key as its own row, but writes one combined -DMU line.
            # Parse definitions like "d25=ps|22|5" back into visual rows.
            matches = re.findall(r'"([^"=]+)=ps\|(\d+)(?:\|([^"|]+))?"', raw_value)
            # This parser normally returns a single row. Multiple DMU rows are expanded by
            # _wled_presets_expand_parsed_row() below.
            if matches:
                return {
                    "kind": "_dmu_group",
                    "items": [
                        {
                            "preset": int(preset),
                            "kind": "dmu_field",
                            "typeId": f"dmu_{key}",
                            "label": f"DMU {key}",
                            "arg": "-DMU",
                            "duration": True,
                            "seconds": (seconds or "").strip(),
                            "dmuKey": key.strip().lower(),
                            "score": 180,
                            "from": 0,
                            "to": 60,
                        }
                        for key, preset, seconds in matches
                    ],
                }
        return {
            "preset": 0,
            "kind": "advanced",
            "typeId": meta.get("typeId") or arg,
            "label": meta.get("label") or arg,
            "arg": arg,
            "duration": False,
            "seconds": "",
            "value": raw_value,
            "placeholder": meta.get("placeholder") or "",
            "score": 180,
            "from": 0,
            "to": 60,
        }

    m = re.match(r'^(-[A-Z0-9]+)\s+"ps\|(\d+)(?:\|([^\"]+))?"$', cleaned)
    if m:
        arg = m.group(1)
        preset = int(m.group(2))
        sec = (m.group(3) or "").strip()
        meta = WLED_PRESET_FIXED_TYPES.get(arg)
        if meta:
            return {
                "preset": preset,
                "kind": "fixed",
                "typeId": meta["typeId"],
                "label": meta["label"],
                "arg": arg,
                "duration": bool(meta.get("duration", False)),
                "seconds": sec,
                "score": 180,
                "from": 0,
                "to": 60,
            }
        return {
            "preset": preset,
            "kind": "unknown",
            "typeId": "unknown",
            "label": f"Unbekannt / {arg}",
            "arg": arg,
            "duration": bool(sec),
            "seconds": sec,
            "score": 180,
            "from": 0,
            "to": 60,
        }

    return {
        "preset": 0,
        "kind": "unknown",
        "typeId": "unknown",
        "label": f"Unbekannt / {cleaned}",
        "arg": cleaned.split()[0] if cleaned.startswith("-") else "-?",
        "duration": False,
        "seconds": "",
        "score": 180,
        "from": 0,
        "to": 60,
        "raw": cleaned,
    }


def load_wled_presets_state() -> tuple[bool, str, list[dict], str]:
    if not os.path.exists(DARTS_WLED_START_CUSTOM):
        return False, t("wled.start_custom_missing", "start-custom.sh nicht gefunden: {path}", path=DARTS_WLED_START_CUSTOM), [], _wled_presets_default_weps_text()

    try:
        lines = _wled_presets_read_file()
    except Exception as e:
        return False, t("wled.start_custom_read_failed", "start-custom.sh konnte nicht gelesen werden: {error}", error=e), [], _wled_presets_default_weps_text()

    weps_idx = _wled_presets_find_weps_index(lines)
    if weps_idx < 0:
        return False, t("wled.weps_line_missing", "Keine -WEPS Zeile gefunden."), [], _wled_presets_default_weps_text()

    weps_text = _wled_presets_extract_weps_text(lines, weps_idx)
    block_end = _wled_presets_block_end(lines, weps_idx + 1)
    rows: list[dict] = []
    for line in lines[weps_idx + 1:block_end]:
        row = _wled_presets_row_from_line(line)
        if row:
            if row.get("kind") == "_dmu_group":
                rows.extend(row.get("items") or [])
            else:
                rows.append(row)

    rows = _wled_presets_prepare_rows(rows)
    return True, "Aktuelle Einstellungen geladen.", rows, weps_text


def _wled_presets_normalize_row(row: dict) -> dict:
    preset = _wled_presets_int((row or {}).get("preset"), 0, 0, 999)
    kind = str((row or {}).get("kind") or "fixed").strip()
    type_id = str((row or {}).get("typeId") or "").strip()
    arg = str((row or {}).get("arg") or "").strip()
    label = str((row or {}).get("label") or "").strip()
    seconds = str((row or {}).get("seconds") or "").strip()

    if kind == "score_exact":
        score = _wled_presets_int((row or {}).get("score"), 180, 0, 180)
        return {
            "preset": preset,
            "kind": "score_exact",
            "typeId": "score_exact",
            "label": label or "Exakter Score / Exact score",
            "arg": f"-S{score}",
            "duration": True,
            "seconds": seconds,
            "score": score,
            "from": 0,
            "to": 60,
        }

    if kind == "score_range":
        from_val = _wled_presets_int((row or {}).get("from"), 0, 0, 180)
        to_val = _wled_presets_int((row or {}).get("to"), 60, 0, 180)
        return {
            "preset": preset,
            "kind": "score_range",
            "typeId": "score_range",
            "label": label or "Score-Bereich / Score range",
            "arg": "",
            "duration": True,
            "seconds": seconds,
            "score": 180,
            "from": from_val,
            "to": to_val,
        }

    if kind in ("option_bool", "option_int", "option_float", "option_text"):
        meta = WLED_PRESET_OPTION_TYPES.get(arg) or {}
        value = str((row or {}).get("value") if (row or {}).get("value") is not None else meta.get("default", "")).strip()
        if kind == "option_bool":
            value = _wled_presets_bool(value, meta.get("default", "1"))
        elif kind == "option_int":
            value = str(_wled_presets_int(value, int(meta.get("default", 0)), int(meta.get("min", 0)), int(meta.get("max", 999))))
        elif kind == "option_float":
            value = _wled_presets_float(value, float(meta.get("default", 0.0)), float(meta.get("min", 0.0)), float(meta.get("max", 999.0)))
        else:
            value = _wled_presets_clean_cli_value(value)
        return {
            "preset": 0,
            "kind": kind,
            "typeId": meta.get("typeId") or type_id or arg or "option",
            "label": label or meta.get("label") or arg or "Option",
            "arg": arg,
            "duration": False,
            "seconds": "",
            "value": value,
            "score": 180,
            "from": 0,
            "to": 60,
        }

    if kind == "dmu_field":
        dmu_key = str((row or {}).get("dmuKey") or (row or {}).get("key") or "").strip().lower()
        if not re.match(r'^(?:[123]|[sdt](?:[1-9]|1[0-9]|20)|s25|d25)$', dmu_key):
            dmu_key = "1"
        label = str((row or {}).get("label") or f"DMU {dmu_key}").strip()
        return {
            "preset": preset,
            "kind": "dmu_field",
            "typeId": f"dmu_{dmu_key}",
            "label": label,
            "arg": "-DMU",
            "duration": True,
            "seconds": seconds,
            "dmuKey": dmu_key,
            "score": 180,
            "from": 0,
            "to": 60,
        }

    if kind == "advanced":
        meta = WLED_PRESET_ADVANCED_TYPES.get(arg) or {}
        value = _wled_presets_clean_cli_value(str((row or {}).get("value") or meta.get("placeholder") or ""))
        return {
            "preset": 0,
            "kind": "advanced",
            "typeId": meta.get("typeId") or type_id or arg or "advanced",
            "label": label or meta.get("label") or arg or "Advanced",
            "arg": arg,
            "duration": False,
            "seconds": "",
            "value": value,
            "placeholder": meta.get("placeholder") or "",
            "score": 180,
            "from": 0,
            "to": 60,
        }

    if kind == "unknown":
        return {
            "preset": preset,
            "kind": "unknown",
            "typeId": "unknown",
            "label": label or (f"Unbekannt / {arg}" if arg else "Unbekannt"),
            "arg": arg or "-?",
            "duration": bool(seconds),
            "seconds": seconds,
            "score": 180,
            "from": 0,
            "to": 60,
        }

    meta = WLED_PRESET_FIXED_TYPES.get(arg)
    if not meta and type_id:
        for fixed_arg, fixed_meta in WLED_PRESET_FIXED_TYPES.items():
            if fixed_meta.get("typeId") == type_id:
                arg = fixed_arg
                meta = fixed_meta
                break
    if not meta:
        meta = {"typeId": type_id or "fixed", "label": label or arg or "Eintrag", "duration": bool(seconds)}

    return {
        "preset": preset,
        "kind": "fixed",
        "typeId": meta.get("typeId") or type_id or "fixed",
        "label": label or meta.get("label") or arg or "Eintrag",
        "arg": arg,
        "duration": bool(meta.get("duration", False)),
        "seconds": seconds,
        "score": 0 if arg == "-S0" else 180,
        "from": 0,
        "to": 60,
    }


def _wled_presets_line_for_row(row: dict, preset_index: int, area_index: int | None) -> str:
    kind = row.get("kind")
    seconds = str(row.get("seconds") or "").strip()
    sec_part = f"|{seconds}" if seconds else ""

    if kind == "score_exact":
        score = _wled_presets_int(row.get("score"), 180, 0, 180)
        return f'  -S{score} "ps|{preset_index}{sec_part}"'

    if kind == "score_range":
        from_val = _wled_presets_int(row.get("from"), 0, 0, 180)
        to_val = _wled_presets_int(row.get("to"), 60, 0, 180)
        return f'  -A{area_index} {from_val}-{to_val} "ps|{preset_index}{sec_part}"'

    if kind == "dmu_field":
        return ""

    arg = str(row.get("arg") or "").strip() or "-?"
    spacing = "   " if len(arg) < 4 else "  "

    if kind == "option_bool":
        return f'  {arg}{spacing}{_wled_presets_bool(row.get("value"), "1")}'
    if kind == "option_int":
        meta = WLED_PRESET_OPTION_TYPES.get(arg) or {}
        value = _wled_presets_int(row.get("value"), int(meta.get("default", 0)), int(meta.get("min", 0)), int(meta.get("max", 999)))
        return f'  {arg}{spacing}{value}'
    if kind == "option_float":
        meta = WLED_PRESET_OPTION_TYPES.get(arg) or {}
        value = _wled_presets_float(row.get("value"), float(meta.get("default", 0.0)), float(meta.get("min", 0.0)), float(meta.get("max", 999.0)))
        return f'  {arg}{spacing}{value}'
    if kind == "advanced":
        value = _wled_presets_clean_cli_value(row.get("value") or "")
        return f'  {arg}{spacing}{value}'

    if bool(row.get("duration", False)) or bool(seconds):
        return f'  {arg}{spacing}"ps|{preset_index}{sec_part}"'
    return f'  {arg}{spacing}"ps|{preset_index}"'


def save_wled_presets_state(rows_payload) -> tuple[bool, str, list[dict], str]:
    if not os.path.exists(DARTS_WLED_START_CUSTOM):
        return False, t("wled.start_custom_missing", "start-custom.sh nicht gefunden: {path}", path=DARTS_WLED_START_CUSTOM), [], _wled_presets_default_weps_text()

    try:
        lines = _wled_presets_read_file()
    except Exception as e:
        return False, t("wled.start_custom_read_failed", "start-custom.sh konnte nicht gelesen werden: {error}", error=e), [], _wled_presets_default_weps_text()

    weps_idx = _wled_presets_find_weps_index(lines)
    if weps_idx < 0:
        return False, t("wled.weps_line_missing", "Keine -WEPS Zeile gefunden."), [], _wled_presets_default_weps_text()

    weps_text = _wled_presets_extract_weps_text(lines, weps_idx)
    rows = _wled_presets_prepare_rows(rows_payload)

    area_counter = 0
    rendered_lines: list[str] = []
    dmu_parts: list[str] = []
    for row in rows:
        area_idx = None
        if row.get("kind") == "score_range":
            area_counter += 1
            area_idx = area_counter

        preset_number = 0
        if _wled_presets_needs_preset(row):
            preset_number = _wled_presets_int(row.get("preset"), 0, 0, 999)
            if preset_number < 1:
                continue

        if row.get("kind") == "dmu_field":
            dmu_key = str(row.get("dmuKey") or "").strip().lower()
            if not dmu_key:
                continue
            seconds = str(row.get("seconds") or "").strip()
            sec_part = f"|{seconds}" if seconds else ""
            dmu_parts.append(f'"{dmu_key}=ps|{preset_number}{sec_part}"')
            continue

        rendered = _wled_presets_line_for_row(row, preset_number, area_idx)
        if rendered:
            rendered_lines.append(rendered)

    if dmu_parts:
        rendered_lines.append('  -DMU   ' + ' '.join(dmu_parts))

    block_end = _wled_presets_block_end(lines, weps_idx + 1)
    prefix = list(lines[:weps_idx + 1])
    suffix = [line for line in lines[block_end:] if not re.match(r'^\s*-DMU\b', line or '')]

    weps_line = prefix[-1].rstrip("\n")
    if rendered_lines:
        if not weps_line.rstrip().endswith("\\"):
            weps_line = weps_line.rstrip() + " \\"
        prefix[-1] = weps_line + "\n"
    else:
        if weps_line.rstrip().endswith("\\"):
            weps_line = weps_line.rstrip()
            weps_line = weps_line[:-1].rstrip()
        prefix[-1] = weps_line + "\n"

    body: list[str] = []
    for i, rendered in enumerate(rendered_lines):
        if i < len(rendered_lines) - 1:
            body.append(rendered + " \\\n")
        else:
            body.append(rendered + "\n")

    new_lines = prefix + body + suffix

    try:
        bak = DARTS_WLED_START_CUSTOM + ".bak"
        if not os.path.exists(bak):
            with open(bak, "w", encoding="utf-8") as f:
                f.writelines(lines)
    except Exception:
        pass

    try:
        with open(DARTS_WLED_START_CUSTOM, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    except Exception as e:
        return False, t("wled.start_custom_write_failed", "start-custom.sh konnte nicht geschrieben werden: {error}", error=e), rows, weps_text

    restarted = False
    try:
        if service_exists(DARTS_WLED_SERVICE) and service_is_active(DARTS_WLED_SERVICE):
            service_restart(DARTS_WLED_SERVICE)
            restarted = True
    except Exception:
        restarted = False

    msg = t("wled.presets_saved", "Preset-Einstellungen gespeichert.")
    if restarted:
        msg += " darts-wled.service wurde neu gestartet."
    return True, msg, rows, weps_text

def load_wled_flag() -> bool:
    # Rückwärtskompatibel: master_enabled
    try:
        cfg = load_wled_config()
        return bool(cfg.get("master_enabled", True))
    except Exception:
        return True


def save_wled_flag(enabled: bool):
    # 1) in Multi-Config spiegeln
    cfg = load_wled_config()
    cfg["master_enabled"] = bool(enabled)
    # wenn master aus, lassen wir Targets wie sie sind (nur master verhindert Start)
    save_wled_config(cfg)

    # 2) Legacy-Flag weiterhin schreiben (falls andere Teile es noch lesen)
    try:
        os.makedirs(os.path.dirname(WLED_FLAG_PATH), exist_ok=True)
        with open(WLED_FLAG_PATH, "w", encoding="utf-8") as f:
            json.dump({"enabled": bool(enabled)}, f, indent=2)
    except Exception:
        pass


# ---------------- Misc helpers ----------------


# ---------------- darts-caller start-custom.sh read/write ----------------

# === Pi monitor test: status/lock helpers (leichtgewichtig, damit man nicht mehrfach startet) ===
def _pid_cmdline_contains(pid: int, needle: str) -> bool:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read().decode("utf-8", "ignore").replace("\x00", " ")
        return needle in cmd
    except Exception:
        return False


def _is_pi_monitor_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except Exception:
        return False
    # sicherstellen, dass es wirklich unser Script ist (pid reuse vermeiden)
    return _pid_cmdline_contains(pid, "pi_monitor_test.sh")


def _read_pi_monitor_state() -> dict:
    state: dict = {}
    try:
        with open(PI_MONITOR_STATE, "r", encoding="utf-8") as f:
            state = json.load(f) or {}
    except Exception:
        state = {}
    pid = None
    try:
        with open(PI_MONITOR_PIDFILE, "r", encoding="utf-8") as f:
            pid = int((f.read() or "").strip() or "0")
    except Exception:
        pid = None

    running = _is_pi_monitor_running(pid)
    now = time.time()
    started_ts = float(state.get("started_ts") or 0)
    ends_ts = float(state.get("ends_ts") or 0)
    interval_s = int(state.get("interval_s") or 0)
    duration_min = int(state.get("duration_min") or 0)
    remaining_sec = max(0, int(ends_ts - now)) if ends_ts else 0

    if not running:
        # stale files aufräumen, wenn Prozess wirklich weg ist
        try:
            if os.path.exists(PI_MONITOR_PIDFILE):
                os.remove(PI_MONITOR_PIDFILE)
        except Exception:
            pass
        try:
            if os.path.exists(PI_MONITOR_STATE):
                os.remove(PI_MONITOR_STATE)
        except Exception:
            pass

    return {
        "running": bool(running),
        "pid": int(pid) if (pid and running) else None,
        "started_ts": started_ts if started_ts else None,
        "ends_ts": ends_ts if ends_ts else None,
        "interval_s": interval_s if interval_s else None,
        "duration_min": duration_min if duration_min else None,
        "remaining_sec": remaining_sec if running else 0,
    }


def get_pi_monitor_status() -> dict:
    st = _read_pi_monitor_state()
    if not st.get("running"):
        return {"running": False, "msg": t("pi_monitor.not_active", "Nicht aktiv.")}

    # menschenlesbare Infos
    try:
        started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st["started_ts"]))
    except Exception:
        started = ""
    try:
        ends = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st["ends_ts"]))
    except Exception:
        ends = ""
    return {
        "running": True,
        "msg": t("pi_monitor.running_status", "Läuft (PID {pid}). Start: {started} · Ende: {ends}", pid=st.get("pid"), started=started, ends=ends),
        **st,
    }


def start_pi_monitor(interval_s: int, duration_min: int) -> dict:
    # Guard: nicht mehrfach starten
    st = _read_pi_monitor_state()
    if st.get("running"):
        return {"ok": False, "running": True, "msg": t("pi_monitor.already_running", "Pi Monitor läuft bereits – bitte warten bis er fertig ist.")}

    if interval_s < 1 or interval_s > 3600:
        return {"ok": False, "running": False, "msg": t("pi_monitor.invalid_interval", "Intervall ungültig.")}
    if duration_min < 1 or duration_min > 24 * 60:
        return {"ok": False, "running": False, "msg": t("pi_monitor.invalid_duration", "Dauer ungültig.")}

    if not os.path.exists(PI_MONITOR_SCRIPT):
        return {"ok": False, "running": False, "msg": t("pi_monitor.script_missing", "Script nicht gefunden: {path}", path=PI_MONITOR_SCRIPT)}

    # Command bauen (wenn nicht root → sudo -n, damit es nicht hängen kann)
    cmd = [PI_MONITOR_SCRIPT, str(interval_s), str(duration_min)]
    if os.geteuid() != 0:
        cmd = ["sudo", "-n"] + cmd

    # Output mitschreiben (hilft bei Debug, kost kaum was)
    try:
        out = open(PI_MONITOR_OUTLOG, "a", encoding="utf-8")
    except Exception:
        out = open(os.devnull, "w", encoding="utf-8")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=out,
            stderr=out,
            start_new_session=True,
        )
    except Exception as e:
        try:
            out.close()
        except Exception:
            pass
        return {"ok": False, "running": False, "msg": t("generic.start_failed", "Start fehlgeschlagen: {error}", error=e)}

    now = time.time()
    ends = now + (duration_min * 60)
    state = {
        "started_ts": now,
        "ends_ts": ends,
        "interval_s": interval_s,
        "duration_min": duration_min,
    }
    try:
        with open(PI_MONITOR_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f)
        with open(PI_MONITOR_PIDFILE, "w", encoding="utf-8") as f:
            f.write(str(proc.pid))
    except Exception:
        pass

    return {"ok": True, "running": True, "msg": t("pi_monitor.started", "Pi Monitor gestartet."), **get_pi_monitor_status()}


def stop_pi_monitor() -> dict:
    st = _read_pi_monitor_state()
    if not st.get("running"):
        return {"ok": True, "running": False, "msg": "Nicht aktiv."}
    pid = st.get("pid")
    try:
        os.kill(int(pid), 15)  # SIGTERM
    except Exception as e:
        return {"ok": False, "running": True, "msg": t("generic.stop_failed", "Konnte nicht stoppen: {error}", error=e)}
    return {"ok": True, "running": False, "msg": t("generic.stop_sent", "Stop gesendet.")}

def _read_var_from_line(line: str) -> str:
    if "=" not in line:
        return ""
    val = line.split("=", 1)[1].strip()
    if val.startswith('"') and val.endswith('"'):
        return val[1:-1]
    if val.startswith("'") and val.endswith("'"):
        return val[1:-1]
    return val


def read_darts_caller_credentials():
    """
    Liest NUR diese Variablen:
      autodarts_email
      autodarts_password
      autodarts_board_id
    """
    email = ""
    password = ""
    board_id = ""

    if not os.path.exists(DARTS_CALLER_START_CUSTOM):
        return email, password, board_id, False, ""

    try:
        with open(DARTS_CALLER_START_CUSTOM, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        return email, password, board_id, True, t("caller.read_start_custom_failed", "Fehler beim Lesen von start-custom.sh: {error}", error=e)

    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("autodarts_email"):
            email = _read_var_from_line(s)
        elif s.startswith("autodarts_password"):
            password = _read_var_from_line(s)
        elif s.startswith("autodarts_board_id"):
            board_id = _read_var_from_line(s)

    return email, password, board_id, True, ""


def _set_var_line(lines, key, value):
    pattern = re.compile(rf'^(\s*{re.escape(key)}\s*=\s*).*$')
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        m = pattern.match(line)
        if m:
            prefix = m.group(1)
            safe = str(value).replace("\\", "\\\\").replace('"', '\\"')
            lines[i] = f'{prefix}"{safe}"\n'
            return True
    return False


def write_darts_caller_credentials_strict(path, email, password, board_id):
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    ok1 = _set_var_line(lines, "autodarts_email", email) if email is not None else True
    ok2 = _set_var_line(lines, "autodarts_password", password) if password is not None else True
    ok3 = _set_var_line(lines, "autodarts_board_id", board_id) if board_id is not None else True

    if not (ok1 and ok2 and ok3):
        raise RuntimeError(t("caller.required_lines_missing", "start-custom.sh: benötigte Variablenzeilen nicht gefunden – es wurde NICHT geschrieben."))

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def write_darts_caller_credentials(email, password, board_id):
    return write_darts_caller_credentials_strict(
        DARTS_CALLER_START_CUSTOM,
        email=email,
        password=password,
        board_id=board_id,
    )


# ---------------- ROUTES ----------------

# i18n-Funktionen sind in configs/webapp/utils/i18n.py





def get_pi_model_name() -> str:
    for path in ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model"):
        try:
            raw = Path(path).read_bytes()
            model = raw.decode("utf-8", "ignore").replace("\x00", "").strip()
            if model:
                return model
        except Exception:
            continue
    return "unknown"


def is_pi5_model(model: str | None = None) -> bool:
    model = (model or get_pi_model_name()).strip()
    return "Raspberry Pi 5" in model


def _pi5_headless_token(output: str | None = None) -> str:
    out = (output or PI5_HEADLESS_OUTPUTS[0]).strip() or PI5_HEADLESS_OUTPUTS[0]
    # KMS/Wayland/Bookworm: großes D erzwingt den Display-Framebuffer auch ohne Monitor.
    return f"video={out}:{PI5_HEADLESS_RESOLUTION}@{PI5_HEADLESS_RATE}D"


def _pi5_headless_cmdline_path() -> Path | None:
    for path in (Path("/boot/firmware/cmdline.txt"), Path("/boot/cmdline.txt")):
        try:
            if path.exists():
                return path
        except Exception:
            continue
    return None


def _load_pi5_headless_state() -> dict:
    try:
        if PI5_HEADLESS_STATE_PATH.exists():
            data = json.loads(PI5_HEADLESS_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_pi5_headless_state(data: dict) -> None:
    try:
        PI5_HEADLESS_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PI5_HEADLESS_STATE_PATH.write_text(json.dumps(data or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _pi5_headless_known_tokens() -> set[str]:
    tokens = {_pi5_headless_token(out) for out in PI5_HEADLESS_OUTPUTS}
    try:
        state = _load_pi5_headless_state()
        token = str(state.get("token") or "").strip()
        if token:
            tokens.add(token)
    except Exception:
        pass
    return tokens


def _read_boot_cmdline(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore").replace("\x00", "").strip()


def _write_boot_cmdline(path: Path, cmdline: str) -> None:
    # cmdline.txt muss eine einzige Zeile bleiben.
    normalized = " ".join(str(cmdline or "").replace("\n", " ").split()).strip()
    if not normalized:
        raise RuntimeError("cmdline.txt wäre leer – Änderung abgebrochen.")
    payload = normalized + "\n"

    if os.geteuid() == 0:
        path.write_text(payload, encoding="utf-8")
        return

    if shutil.which("sudo"):
        result = subprocess.run(
            ["sudo", "-n", "tee", str(path)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=8.0,
        )
        if result.returncode == 0:
            return
        raise RuntimeError((result.stderr or result.stdout or "sudo tee fehlgeschlagen").strip())

    path.write_text(payload, encoding="utf-8")


def get_pi5_headless_info() -> dict:
    model = get_pi_model_name()
    is_pi5 = is_pi5_model(model)
    path = _pi5_headless_cmdline_path()
    state = _load_pi5_headless_state()
    tokens = _pi5_headless_known_tokens()
    has_token = False

    if path is not None:
        try:
            cmdline = _read_boot_cmdline(path)
            parts = set(cmdline.split())
            has_token = bool(parts.intersection(tokens))
        except Exception:
            has_token = False

    enabled = bool(is_pi5 and has_token)
    return {
        "show": bool(is_pi5),
        "available": bool(is_pi5 and path is not None),
        "enabled": enabled,
        "model": model,
        "cmdline_path": str(path or ""),
        "token": _pi5_headless_token(),
        "resolution": PI5_HEADLESS_RESOLUTION,
        "state_enabled": bool(state.get("enabled")),
        "message": str(state.get("message") or ""),
        "pending_reboot": bool(state.get("pending_reboot")),
    }


def apply_pi5_headless_mode(enable: bool) -> dict:
    model = get_pi_model_name()
    if not is_pi5_model(model):
        return {"ok": False, "message": t("pi5_headless.only_pi5", "Diese Funktion ist nur für Raspberry Pi 5 sichtbar/verwendbar.")}

    path = _pi5_headless_cmdline_path()
    if path is None:
        return {"ok": False, "message": t("pi5_headless.cmdline_missing", "cmdline.txt wurde nicht gefunden.")}

    try:
        old_cmdline = _read_boot_cmdline(path)
        parts = old_cmdline.split()
        state = _load_pi5_headless_state()
        token = _pi5_headless_token()
        known_tokens = _pi5_headless_known_tokens()

        changed = False
        if enable:
            new_parts = [part for part in parts if part not in known_tokens]
            if token not in new_parts:
                new_parts.append(token)
            new_cmdline = " ".join(new_parts)
            if new_cmdline != old_cmdline:
                if not state.get("backup_cmdline"):
                    state["backup_cmdline"] = old_cmdline
                _write_boot_cmdline(path, new_cmdline)
                changed = True

            raspi_msg = ""
            raspi_config = shutil.which("raspi-config") or "/usr/bin/raspi-config"
            if Path(raspi_config).exists() or shutil.which("raspi-config"):
                try:
                    result = _run_root_command([raspi_config, "nonint", "do_boot_behaviour", "B4"], timeout=20.0)
                    if result.returncode != 0:
                        raspi_msg = ((result.stderr or "") + " " + (result.stdout or "")).strip()
                except Exception as exc:
                    raspi_msg = str(exc)

            msg = t("pi5_headless.enabled_msg", "Headless für Raspberry Pi 5 wurde eingeschaltet. Bitte Raspberry neu starten und dann ohne Monitor testen.")
            if raspi_msg:
                msg += " " + t("pi5_headless.autologin_warning", "Hinweis: Desktop-Autologin konnte nicht sicher über raspi-config gesetzt werden: {error}", error=raspi_msg)
            state.update({
                "enabled": True,
                "pending_reboot": True,
                "token": token,
                "cmdline_path": str(path),
                "resolution": PI5_HEADLESS_RESOLUTION,
                "changed_ts": time.time(),
                "message": msg,
            })
            _save_pi5_headless_state(state)
            return {"ok": True, "changed": changed, "needs_reboot": True, "message": msg, "headless_info": get_pi5_headless_info()}

        # Ausschalten: nur die vom Webpanel gesetzten Headless-Tokens entfernen.
        new_parts = [part for part in parts if part not in known_tokens]
        new_cmdline = " ".join(new_parts)
        if new_cmdline != old_cmdline:
            _write_boot_cmdline(path, new_cmdline)
            changed = True

        msg = t("pi5_headless.disabled_msg", "Headless für Raspberry Pi 5 wurde ausgeschaltet. Bitte Raspberry neu starten. Desktop-Autologin bleibt unverändert, damit das Image sicher weiter startet.")
        state.update({
            "enabled": False,
            "pending_reboot": True,
            "cmdline_path": str(path),
            "changed_ts": time.time(),
            "message": msg,
        })
        _save_pi5_headless_state(state)
        return {"ok": True, "changed": changed, "needs_reboot": True, "message": msg, "headless_info": get_pi5_headless_info()}
    except Exception as exc:
        return {"ok": False, "message": t("pi5_headless.apply_failed", "Headless konnte nicht geändert werden: {error}", error=exc)}


def _display_user_exists(username: str | None) -> bool:
    username = (username or "").strip()
    if not username:
        return False
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False
    except Exception:
        return False


def _display_user_candidates() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add(username: str | None):
        username = (username or "").strip()
        if not username or username in seen:
            return
        if _display_user_exists(username):
            seen.add(username)
            out.append(username)

    for candidate in AUTODARTS_DISPLAY_PREFERRED_USERS:
        add(candidate)

    try:
        current_user = pwd.getpwuid(os.geteuid()).pw_name
    except Exception:
        current_user = ""
    add(current_user)

    try:
        home_root = Path("/home")
        if home_root.exists():
            for pth in sorted(home_root.iterdir()):
                if pth.is_dir():
                    add(pth.name)
    except Exception:
        pass

    try:
        proc = subprocess.run(
            ["ps", "-eo", "user=,args="],
            capture_output=True,
            text=True,
            timeout=1.8,
        )
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            user, args = parts[0].strip(), parts[1].strip()
            if not user or user == "root":
                continue
            if any(token in args for token in ("lxsession", "startlxde", "openbox", "xfce4-session", "xinit", "labwc", "wayfire", "wayland")):
                add(user)
    except Exception:
        pass

    return out


def _display_candidates() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add(value: str | None):
        value = (value or "").strip()
        if not value or value in seen:
            return
        seen.add(value)
        out.append(value)

    add(os.environ.get("DISPLAY", ""))
    add(":0")
    add(":1")
    return out


def _wayland_display_candidates(username: str | None = None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add(value: str | None):
        value = (value or "").strip()
        if not value or value in seen:
            return
        if not re.match(r"^wayland-[0-9]+$", value):
            return
        seen.add(value)
        out.append(value)

    add(os.environ.get("WAYLAND_DISPLAY", ""))

    uid = None
    try:
        if username:
            uid = pwd.getpwnam(username).pw_uid
    except Exception:
        uid = None

    if uid is not None:
        runtime_dir = Path(f"/run/user/{uid}")
        try:
            if runtime_dir.exists():
                for pth in sorted(runtime_dir.glob("wayland-*")):
                    if pth.name.endswith(".lock"):
                        continue
                    if pth.is_socket() or pth.exists():
                        add(pth.name)
        except Exception:
            pass

    add("wayland-1")
    add("wayland-0")
    return out


def _build_display_env(username: str, display_name: str = "", wayland_display: str = "") -> dict:
    env = os.environ.copy()
    if display_name:
        env["DISPLAY"] = display_name
    if wayland_display:
        env["WAYLAND_DISPLAY"] = wayland_display
    try:
        pwent = pwd.getpwnam(username)
        env["HOME"] = pwent.pw_dir
        env.setdefault("USER", username)
        env.setdefault("LOGNAME", username)

        runtime_dir = Path(f"/run/user/{pwent.pw_uid}")
        if runtime_dir.exists():
            env.setdefault("XDG_RUNTIME_DIR", str(runtime_dir))

        xauth_candidates = [
            Path(pwent.pw_dir) / ".Xauthority",
            Path(f"/run/user/{pwent.pw_uid}/gdm/Xauthority"),
            Path(f"/run/user/{pwent.pw_uid}/Xauthority"),
        ]
        for xauth in xauth_candidates:
            if xauth.exists():
                env["XAUTHORITY"] = str(xauth)
                break

        bus = Path(f"/run/user/{pwent.pw_uid}/bus")
        if bus.exists():
            env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={bus}")
    except Exception:
        pass
    return env


def _display_env_assignments(env: dict) -> list[str]:
    # sudo setzt Umgebungsvariablen gerne zurück. Für X11/Wayland müssen diese
    # Variablen aber sicher beim Ziel-Prozess ankommen. Darum werden sie direkt
    # über /usr/bin/env vor den eigentlichen Befehl gesetzt.
    keys = (
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_RUNTIME_DIR",
        "XAUTHORITY",
        "DBUS_SESSION_BUS_ADDRESS",
        "HOME",
        "USER",
        "LOGNAME",
    )
    out: list[str] = []
    for key in keys:
        value = str((env or {}).get(key) or "").strip()
        if value:
            out.append(f"{key}={value}")
    return out


def _run_subprocess_as_user(cmd: list[str], username: str, env: dict, timeout: float = 6.0) -> subprocess.CompletedProcess:
    current_user = ""
    try:
        current_user = pwd.getpwuid(os.geteuid()).pw_name
    except Exception:
        current_user = ""

    env_cmd = ["env", *_display_env_assignments(env), *cmd]

    if username and os.geteuid() == 0 and username != current_user:
        if shutil.which("sudo"):
            return subprocess.run(["sudo", "-u", username, "-H", *env_cmd], capture_output=True, text=True, timeout=timeout, env=os.environ.copy())
        if shutil.which("runuser"):
            shell_cmd = shlex.join(env_cmd)
            return subprocess.run(["runuser", "-l", username, "-c", "bash -lc " + shlex.quote(shell_cmd)], capture_output=True, text=True, timeout=timeout, env=os.environ.copy())
        if shutil.which("su"):
            shell_cmd = shlex.join(env_cmd)
            return subprocess.run(["su", "-", username, "-c", "bash -lc " + shlex.quote(shell_cmd)], capture_output=True, text=True, timeout=timeout, env=os.environ.copy())

    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


def _run_root_command(cmd: list[str], timeout: float = 12.0) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if os.geteuid() == 0:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if shutil.which("sudo"):
        return subprocess.run(["sudo", "-n", *cmd], capture_output=True, text=True, timeout=timeout, env=env)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


def _parse_xrandr_output(stdout: str) -> list[dict]:
    outputs: list[dict] = []
    current_output: dict | None = None
    mode_re = re.compile(r"^\s+([0-9]+x[0-9]+[A-Za-z]*)\s+(.*)$")
    header_re = re.compile(r"^(\S+)\s+connected(?:\s+primary)?(?:\s+([0-9]+x[0-9]+)\+[0-9]+\+[0-9]+)?")

    for raw_line in (stdout or "").splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        if not raw_line.startswith((" ", "	")):
            current_output = None
            header = header_re.search(line)
            if not header:
                continue
            current_output = {
                "name": header.group(1),
                "current_mode": header.group(2) or "",
                "current_rate": "",
                "modes": [],
            }
            outputs.append(current_output)
            continue

        if not current_output:
            continue

        match = mode_re.match(line)
        if not match:
            continue

        mode_name = match.group(1).strip()
        rates_blob = match.group(2).strip()
        seen_keys: set[str] = set()
        for token in rates_blob.split():
            token = token.strip()
            if not token or not re.match(r"^[0-9.]+[*+]*$", token):
                continue
            rate = re.sub(r"[^0-9.]", "", token)
            key = f"{mode_name}@{rate}" if rate else mode_name
            if key in seen_keys:
                continue
            seen_keys.add(key)
            is_current = "*" in token
            is_preferred = "+" in token
            label = mode_name + (f" @ {rate} Hz" if rate else "")
            entry = {
                "key": key,
                "mode": mode_name,
                "rate": rate,
                "label": label,
                "is_current": is_current,
                "is_preferred": is_preferred,
            }
            current_output["modes"].append(entry)
            if is_current:
                current_output["current_mode"] = mode_name
                current_output["current_rate"] = rate

    parsed: list[dict] = []
    for output in outputs:
        if output.get("modes"):
            parsed.append(output)
    return parsed


def _display_value_is_real(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.lower()
    return lowered not in {"(null)", "null", "none", "unknown", "n/a"}


def _display_mode_pixels(mode: dict) -> int:
    try:
        if mode.get("width") and mode.get("height"):
            return int(mode.get("width") or 0) * int(mode.get("height") or 0)
        text = str(mode.get("mode") or mode.get("key") or "")
        match = re.search(r"([0-9]+)x([0-9]+)", text)
        if match:
            return int(match.group(1)) * int(match.group(2))
    except Exception:
        pass
    return 0


def _display_output_score(output: dict) -> tuple:
    modes = output.get("modes") or []
    make = output.get("make")
    model = output.get("model")
    serial = output.get("serial")
    desc = output.get("description")
    real_identity = _display_value_is_real(make) or _display_value_is_real(model) or _display_value_is_real(serial)
    has_preferred = any(bool(m.get("is_preferred")) for m in modes if isinstance(m, dict))
    has_current = any(bool(m.get("is_current")) for m in modes if isinstance(m, dict))
    max_pixels = max((_display_mode_pixels(m) for m in modes if isinstance(m, dict)), default=0)
    name = str(output.get("name") or "")
    # Größerer Score = eher echter Hauptmonitor. Ein leerer/EDID-loser HDMI-Port auf Pi/Wayland
    # kann trotzdem als enabled auftauchen, hat dann aber meist Make/Model "(null)" und nur wenige Modi.
    score = 0
    if output.get("enabled") is not False:
        score += 1000
    if real_identity:
        score += 500
    if _display_value_is_real(desc) and "(null)" not in str(desc).lower():
        score += 100
    if has_preferred:
        score += 250
    if has_current:
        score += 80
    score += min(max_pixels // 10000, 250)
    # Bei Gleichstand HDMI-A-1/HDMI-1 leicht bevorzugen, weil das bei den meisten Pi-Setups der primäre Port ist.
    primary_hint = 1 if re.search(r"(?:^|[-_])(?:A-)?1$", name, re.I) else 0
    return (score, primary_hint, -len(modes), name)


def _decorate_display_outputs(outputs: list[dict]) -> list[dict]:
    decorated: list[dict] = []
    for output in outputs or []:
        modes = output.get("modes") or []
        max_pixels = max((_display_mode_pixels(m) for m in modes if isinstance(m, dict)), default=0)
        output["has_preferred"] = any(bool(m.get("is_preferred")) for m in modes if isinstance(m, dict))
        output["has_current"] = any(bool(m.get("is_current")) for m in modes if isinstance(m, dict))
        output["max_pixels"] = max_pixels
        real_identity = _display_value_is_real(output.get("make")) or _display_value_is_real(output.get("model")) or _display_value_is_real(output.get("serial"))
        output["is_placeholder"] = (not real_identity) and max_pixels <= (1024 * 768)

        label_parts = [str(output.get("name") or "").strip()]
        model = str(output.get("model") or "").strip()
        make = str(output.get("make") or "").strip()
        if _display_value_is_real(model):
            label_parts.append(model)
        elif _display_value_is_real(make):
            label_parts.append(make)
        elif output.get("is_placeholder"):
            label_parts.append(t("themes.display_output_no_edid", "kein Monitor/ohne EDID"))
        output["label"] = " – ".join([p for p in label_parts if p])
        decorated.append(output)

    decorated.sort(key=_display_output_score, reverse=True)
    return decorated


def _parse_wlr_randr_output(stdout: str) -> list[dict]:
    outputs: list[dict] = []
    current_output: dict | None = None
    in_modes = False
    mode_re = re.compile(r"^\s+([0-9]+)x([0-9]+)\s+px,\s*([0-9.]+)\s*Hz\s*(.*)$", re.I)

    for raw_line in (stdout or "").splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        if not raw_line.startswith((" ", "\t")):
            match_header = re.match(r'^(\S+)(?:\s+"(.*)")?$', line)
            name = (match_header.group(1) if match_header else line.split(None, 1)[0]).strip()
            description = ((match_header.group(2) if match_header and match_header.lastindex and match_header.lastindex >= 2 else "") or "").strip()
            if not name or ":" in name:
                current_output = None
                in_modes = False
                continue
            current_output = {
                "name": name,
                "description": description,
                "make": "",
                "model": "",
                "serial": "",
                "current_mode": "",
                "current_rate": "",
                "modes": [],
            }
            outputs.append(current_output)
            in_modes = False
            continue

        if not current_output:
            continue

        stripped = line.strip()
        lower = stripped.lower()
        if lower.startswith("make:"):
            current_output["make"] = stripped.split(":", 1)[1].strip()
            continue
        if lower.startswith("model:"):
            current_output["model"] = stripped.split(":", 1)[1].strip()
            continue
        if lower.startswith("serial:"):
            current_output["serial"] = stripped.split(":", 1)[1].strip()
            continue
        if lower.startswith("enabled:"):
            current_output["enabled"] = "yes" in lower or "true" in lower
            continue
        if lower.startswith("modes:"):
            in_modes = True
            continue
        if re.match(r"^[A-Za-z].*:\s*", stripped) and not lower.startswith("modes:"):
            in_modes = False

        if not in_modes:
            continue

        match = mode_re.match(line)
        if not match:
            continue

        width = int(match.group(1))
        height = int(match.group(2))
        mode_name = f"{width}x{height}"
        rate = match.group(3).strip()
        flags = (match.group(4) or "").lower()
        is_current = "current" in flags
        is_preferred = "preferred" in flags
        key = f"{mode_name}@{rate}" if rate else mode_name
        label = mode_name + (f" @ {rate} Hz" if rate else "")
        entry = {
            "key": key,
            "mode": mode_name,
            "width": width,
            "height": height,
            "rate": rate,
            "label": label,
            "is_current": is_current,
            "is_preferred": is_preferred,
        }
        current_output["modes"].append(entry)
        if is_current:
            current_output["current_mode"] = mode_name
            current_output["current_rate"] = rate

    parsed: list[dict] = []
    for output in outputs:
        enabled = output.get("enabled")
        if enabled is False:
            continue
        if output.get("modes"):
            parsed.append(output)
    return _decorate_display_outputs(parsed)

def _probe_xrandr_session() -> dict:
    xrandr_cmd = str(AUTODARTS_DISPLAY_XRANDR or "xrandr").strip() or "xrandr"
    if not shutil.which(xrandr_cmd) and not Path(xrandr_cmd).exists():
        return {
            "ok": False,
            "backend": "x11",
            "reason": t("themes.display_no_xrandr", "xrandr wurde auf diesem System nicht gefunden."),
        }

    any_query_success = False
    first_open_display_error = ""

    for username in _display_user_candidates():
        for display_name in _display_candidates():
            env = _build_display_env(username, display_name=display_name)
            try:
                result = _run_subprocess_as_user([xrandr_cmd, "--query"], username, env, timeout=6.0)
            except Exception as exc:
                first_open_display_error = first_open_display_error or str(exc)
                continue

            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()

            if result.returncode != 0:
                if stderr and not first_open_display_error:
                    first_open_display_error = stderr
                continue

            any_query_success = True
            outputs = _parse_xrandr_output(stdout)
            if outputs:
                return {
                    "ok": True,
                    "backend": "x11",
                    "user": username,
                    "display": display_name,
                    "outputs": outputs,
                }

    if any_query_success:
        return {
            "ok": False,
            "backend": "x11",
            "reason": t("themes.display_no_monitor", "Es wurde kein aktiver Monitor am Raspberry erkannt."),
        }

    message = first_open_display_error or ""
    lowered = message.lower()
    if (not message) or ("open display" in lowered) or ("can't open display" in lowered) or ("unable to open display" in lowered) or ("authorization required" in lowered):
        message = t("themes.display_no_x11_session", "Keine passende X11-Desktop-Sitzung gefunden.")
    return {
        "ok": False,
        "backend": "x11",
        "reason": message,
    }


def _probe_wlr_randr_session() -> dict:
    wlr_cmd = str(AUTODARTS_DISPLAY_WLR_RANDR or "wlr-randr").strip() or "wlr-randr"
    if not shutil.which(wlr_cmd) and not Path(wlr_cmd).exists():
        return {
            "ok": False,
            "backend": "wayland",
            "reason": t("themes.display_no_wlr_randr", "wlr-randr wurde auf diesem System nicht gefunden."),
        }

    any_query_success = False
    first_error = ""

    for username in _display_user_candidates():
        for wayland_display in _wayland_display_candidates(username):
            env = _build_display_env(username, wayland_display=wayland_display)
            try:
                result = _run_subprocess_as_user([wlr_cmd], username, env, timeout=6.0)
            except Exception as exc:
                first_error = first_error or str(exc)
                continue

            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            if result.returncode != 0:
                if stderr and not first_error:
                    first_error = stderr
                continue

            any_query_success = True
            outputs = _parse_wlr_randr_output(stdout)
            if outputs:
                return {
                    "ok": True,
                    "backend": "wayland",
                    "user": username,
                    "wayland_display": wayland_display,
                    "outputs": outputs,
                }

    if any_query_success:
        return {
            "ok": False,
            "backend": "wayland",
            "reason": t("themes.display_no_monitor", "Es wurde kein aktiver Monitor am Raspberry erkannt."),
        }

    message = first_error or t("themes.display_no_wayland_session", "Keine passende Wayland-Desktop-Sitzung gefunden.")
    return {
        "ok": False,
        "backend": "wayland",
        "reason": message,
    }


def _running_display_backend_hint() -> dict:
    # Wichtig: Unter Wayland kann Xwayland/xrandr trotzdem antworten. Das darf
    # nicht als echte X11-Sitzung gewertet werden, sonst endet das Setzen der
    # Auflösung mit RANDR/BadMatch. Darum zuerst echte Desktop-Prozesse prüfen.
    try:
        proc = subprocess.run(
            ["ps", "-eo", "user=,comm=,args="],
            capture_output=True,
            text=True,
            timeout=1.8,
        )
    except Exception:
        return {"backend": "unknown", "user": ""}

    x11_user = ""
    for raw_line in (proc.stdout or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        user = parts[0].strip()
        comm = parts[1].strip().lower()
        args = (parts[2] if len(parts) > 2 else "").strip().lower()
        blob = f"{comm} {args}"

        if comm in {"labwc", "wayfire"} or "/labwc" in blob or "/wayfire" in blob or "labwc-pi" in blob:
            return {"backend": "wayland", "user": user}

        if comm in {"xorg", "xwayland"} or "/usr/lib/xorg/xorg" in blob or " openbox" in blob or comm == "openbox":
            # Xwayland alleine ist kein alter TV-Modus. Merken, aber Wayland hat Vorrang.
            if comm != "xwayland" and not x11_user:
                x11_user = user

    if x11_user:
        return {"backend": "x11", "user": x11_user}
    return {"backend": "unknown", "user": ""}


def _probe_display_session() -> dict:
    # Wenn X11/Wayland umgeschaltet wurde, aber noch kein Neustart erfolgt ist,
    # zählt für die Auflösung immer die AKTUELL laufende Session.
    hint = _running_display_backend_hint()
    hinted_backend = str(hint.get("backend") or "unknown").lower()

    if hinted_backend == "wayland":
        wayland_probe = _probe_wlr_randr_session()
        if wayland_probe.get("ok"):
            return wayland_probe
        return {
            "ok": False,
            "backend": "wayland",
            "user": hint.get("user") or "",
            "reason": wayland_probe.get("reason") or t(
                "themes.display_no_wayland_session",
                "Wayland läuft, aber die Auflösung konnte nicht über wlr-randr gelesen werden.",
            ),
            "wayland_reason": wayland_probe.get("reason") or "",
            "x11_reason": "",
        }

    if hinted_backend == "x11":
        x11_probe = _probe_xrandr_session()
        if x11_probe.get("ok"):
            return x11_probe
        return {
            "ok": False,
            "backend": "x11",
            "user": hint.get("user") or "",
            "reason": x11_probe.get("reason") or t("themes.display_no_x11_session", "Keine passende X11-Desktop-Sitzung gefunden."),
            "wayland_reason": "",
            "x11_reason": x11_probe.get("reason") or "",
        }

    wayland_probe = _probe_wlr_randr_session()
    if wayland_probe.get("ok"):
        return wayland_probe

    x11_probe = _probe_xrandr_session()
    if x11_probe.get("ok"):
        return x11_probe

    return {
        "ok": False,
        "backend": "unknown",
        "reason": wayland_probe.get("reason") or x11_probe.get("reason") or t("themes.display_no_session", "Keine passende Desktop-Sitzung gefunden."),
        "wayland_reason": wayland_probe.get("reason") or "",
        "x11_reason": x11_probe.get("reason") or "",
    }


def _display_backend_label(value: str | None) -> str:
    value = (value or "").strip().lower()
    if value == "x11":
        return t("themes.display_backend_x11", "Alter TV Modus")
    if value == "wayland":
        return t("themes.display_backend_wayland", "Neuer TV Modus")
    return t("themes.display_backend_unknown", "unbekannt")


def get_configured_display_backend() -> str:
    raspi_config = shutil.which("raspi-config") or "/usr/bin/raspi-config"
    if Path(raspi_config).exists() or shutil.which("raspi-config"):
        try:
            result = _run_root_command([raspi_config, "nonint", "get_wayland"], timeout=6.0)
            raw = ((result.stdout or "") + chr(10) + (result.stderr or "")).strip().upper()
            if result.returncode == 0:
                if "W1" in raw or "RPD-X" in raw or "LXDE-PI-X" in raw or "OPENBOX" in raw or "X11" in raw:
                    return "x11"
                if "W2" in raw or "W3" in raw or "RPD-LABWC" in raw or "LXDE-PI-LABWC" in raw or "LABWC" in raw or "WAYFIRE" in raw or "WAYLAND" in raw:
                    return "wayland"
        except Exception:
            pass

    try:
        files = [Path("/etc/lightdm/lightdm.conf")]
        conf_dir = Path("/etc/lightdm/lightdm.conf.d")
        if conf_dir.exists():
            files.extend(sorted(conf_dir.glob("*.conf")))
        lines = []
        for pth in files:
            if not pth.exists():
                continue
            for line in pth.read_text(errors="ignore").splitlines():
                stripped = line.strip().lower()
                if stripped and not stripped.startswith("#"):
                    lines.append(stripped)
        blob = chr(10).join(lines)
        if any(token in blob for token in (
            "user-session=rpd-labwc",
            "user-session=lxde-pi-labwc",
            "user-session=labwc",
            "user-session=wayfire",
            "greeter-session=pi-greeter-labwc",
            "labwc",
            "wayfire",
        )):
            return "wayland"
        if any(token in blob for token in (
            "user-session=rpd-x",
            "user-session=lxde-pi-x",
            "user-session=openbox",
            "xserver-command",
            "x11",
        )):
            return "x11"
    except Exception:
        pass

    return "unknown"


def _wayland_option_candidates(raspi_config: str) -> list[str]:
    # Raspberry Pi OS hat die Wayland-Auswahl über die Zeit geändert:
    # ältere Bookworm-Stände: W2=Wayfire, W3=Labwc; neuere Stände: W2=Labwc.
    # Darum: wenn W3 im lokalen raspi-config eindeutig Labwc ist, bevorzugen wir W3,
    # sonst W2. Fallback probiert beide, damit Updates/Altstände safe bleiben.
    try:
        script = Path(raspi_config).read_text(errors="ignore").lower()
        if "w3" in script and "labwc" in script:
            return ["W3", "W2"]
    except Exception:
        pass
    return ["W2", "W3"]


def get_autodarts_display_info() -> dict:
    model = get_pi_model_name()
    is_pi5 = is_pi5_model(model)
    probe = _probe_display_session()
    runtime_backend = str(probe.get("backend") or "unknown")
    configured_backend = get_configured_display_backend()

    info = {
        "available": False,
        "show_in_theme_card": bool(is_pi5) if AUTODARTS_DISPLAY_SECTION_PI5_ONLY else True,
        "backend": runtime_backend,
        "runtime_backend": runtime_backend,
        "configured_backend": configured_backend,
        "pending_reboot": configured_backend in {"x11", "wayland"} and runtime_backend in {"x11", "wayland"} and configured_backend != runtime_backend,
        "backend_label": _display_backend_label(runtime_backend),
        "configured_backend_label": _display_backend_label(configured_backend),
        "model": model,
        "is_pi5": is_pi5,
        "message": probe.get("reason") or "",
        "outputs": [],
    }

    if probe.get("ok"):
        outputs = probe.get("outputs") or []
        info.update({
            "available": bool(outputs),
            "show_in_theme_card": True,
            "message": t("themes.display_ready", "Lokaler Monitor erkannt. Die Änderung gilt sofort auf dem Pi-Bildschirm."),
            "outputs": outputs,
        })

    if info["pending_reboot"]:
        info["message"] = t(
            "themes.display_backend_pending_reboot",
            "TV-Modus ist für den nächsten Start auf {configured} gesetzt. Aktuell läuft noch {runtime}.",
            configured=info["configured_backend_label"],
            runtime=info["backend_label"],
        )

    return info


def _find_display_mode(outputs: list[dict], output_name: str, mode_key: str) -> tuple[dict | None, dict | None]:
    for output in outputs or []:
        if str(output.get("name") or "") != output_name:
            continue
        for mode in output.get("modes", []) or []:
            if str(mode.get("key") or "") == mode_key:
                return output, mode
        return output, None
    return None, None


def apply_autodarts_display_mode(output_name: str, mode_key: str) -> dict:
    output_name = (output_name or "").strip()
    mode_key = (mode_key or "").strip()
    if not output_name:
        return {"ok": False, "message": t("themes.display_output_required", "Bitte zuerst einen Bildschirm-Ausgang auswählen.")}
    if not mode_key:
        return {"ok": False, "message": t("themes.display_mode_required", "Bitte zuerst eine Auflösung auswählen.")}

    probe = _probe_display_session()
    if not probe.get("ok"):
        return {"ok": False, "message": probe.get("reason") or t("themes.display_no_session", "Keine passende Desktop-Sitzung gefunden.")}

    outputs = probe.get("outputs") or []
    output, mode = _find_display_mode(outputs, output_name, mode_key)
    if not output:
        return {"ok": False, "message": t("themes.display_output_missing", "Der gewählte Bildschirm-Ausgang wurde nicht gefunden.")}
    if not mode:
        return {"ok": False, "message": t("themes.display_mode_missing", "Die gewählte Auflösung ist aktuell nicht verfügbar.")}

    backend = str(probe.get("backend") or "x11").lower()
    try:
        if backend == "wayland":
            env = _build_display_env(str(probe.get("user") or ""), wayland_display=str(probe.get("wayland_display") or "wayland-1"))
            mode_arg = str(mode.get("mode") or "")
            if mode.get("rate"):
                mode_arg = f"{mode_arg}@{mode.get('rate')}Hz"
            cmd = [str(AUTODARTS_DISPLAY_WLR_RANDR or "wlr-randr").strip() or "wlr-randr", "--output", output_name, "--mode", mode_arg]
            result = _run_subprocess_as_user(cmd, str(probe.get("user") or ""), env, timeout=8.0)
        else:
            env = _build_display_env(str(probe.get("user") or ""), display_name=str(probe.get("display") or ":0"))
            cmd = [str(AUTODARTS_DISPLAY_XRANDR or "xrandr").strip() or "xrandr", "--output", output_name, "--mode", str(mode.get("mode") or "")]
            if mode.get("rate"):
                cmd.extend(["--rate", str(mode.get("rate"))])
            result = _run_subprocess_as_user(cmd, str(probe.get("user") or ""), env, timeout=8.0)
    except Exception as exc:
        return {"ok": False, "message": str(exc)}

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        return {
            "ok": False,
            "message": stderr or t("themes.display_apply_failed", "Die Auflösung konnte nicht gesetzt werden."),
        }

    refreshed = get_autodarts_display_info()
    return {
        "ok": True,
        "message": t("themes.display_applied", "Auflösung {mode} übernommen.", mode=(mode.get("label") or mode.get("mode") or mode_key)),
        "display_info": refreshed,
    }


def apply_autodarts_display_backend(target_backend: str) -> dict:
    target_backend = (target_backend or "").strip().lower()
    if target_backend not in {"x11", "wayland"}:
        return {"ok": False, "message": t("themes.display_backend_required", "Bitte zuerst einen TV-Modus auswählen.")}

    raspi_config = shutil.which("raspi-config") or "/usr/bin/raspi-config"
    if not (Path(raspi_config).exists() or shutil.which("raspi-config")):
        return {"ok": False, "message": t("themes.display_no_raspi_config", "raspi-config wurde auf diesem System nicht gefunden.")}

    options = ["W1"] if target_backend == "x11" else _wayland_option_candidates(raspi_config)
    last_error = ""
    used_option = ""
    for option in options:
        try:
            result = _run_root_command([raspi_config, "nonint", "do_wayland", option], timeout=20.0)
        except Exception as exc:
            last_error = str(exc)
            continue

        if result.returncode == 0:
            used_option = option
            break

        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        last_error = stderr or stdout or last_error
    else:
        return {
            "ok": False,
            "message": last_error or t("themes.display_backend_apply_failed", "Der TV-Modus konnte nicht gesetzt werden."),
        }

    refreshed = get_autodarts_display_info()
    return {
        "ok": True,
        "message": t(
            "themes.display_backend_applied_reboot",
            "{mode} wurde gesetzt. Bitte Raspberry neu starten, damit der Modus aktiv wird.",
            mode=_display_backend_label(target_backend),
        ),
        "display_info": refreshed,
        "needs_reboot": True,
        "raspi_option": used_option,
    }


def _ensure_autodarts_theme_state_parent():
    try:
        AUTODARTS_THEME_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _autodarts_theme_default_state() -> dict:
    return {"selected": "default"}


def load_autodarts_theme_state() -> dict:
    try:
        data = json.loads(AUTODARTS_THEME_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            selected = str(data.get("selected") or "default").strip() or "default"
            return {"selected": selected}
    except Exception:
        pass
    return _autodarts_theme_default_state()


def save_autodarts_theme_state(selected: str) -> dict:
    payload = {"selected": (selected or "default").strip() or "default"}
    _ensure_autodarts_theme_state_parent()
    AUTODARTS_THEME_STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _parse_autodarts_theme_header_value(head: str, labels: tuple[str, ...]) -> str:
    if not head:
        return ""
    label_group = "|".join(re.escape(label) for label in labels)
    patterns = [
        rf"^\s*/\*\s*(?:{label_group})\s*:\s*(.+?)\s*\*/",
        rf"^\s*(?:{label_group})\s*:\s*(.+?)\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, head, flags=re.MULTILINE | re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""



def parse_autodarts_theme_metadata(css_text: str) -> dict[str, str]:
    if not css_text:
        return {"author": "", "modes": "", "resolution": ""}

    head = "\n".join(css_text.splitlines()[:30])
    author = _parse_autodarts_theme_header_value(head, ("Autor", "Author"))
    modes = _parse_autodarts_theme_header_value(head, ("Spielmodus", "Spielmodi", "Game mode", "Game modes", "Mode", "Modes"))
    resolution = _parse_autodarts_theme_header_value(head, ("Bildschirmauflösung", "Auflösung", "Resolution", "Screen resolution"))

    if not resolution:
        fallback_patterns = [
            r"für\s+Auflösung\s+([0-9]{3,5}\s*[x×]\s*[0-9]{3,5})",
            r"for\s+resolution\s+([0-9]{3,5}\s*[x×]\s*[0-9]{3,5})",
        ]
        for pattern in fallback_patterns:
            match = re.search(pattern, head, flags=re.IGNORECASE)
            if match:
                resolution = match.group(1).strip()
                break

    if resolution:
        resolution = re.sub(r"\s*[×x]\s*", " × ", resolution)

    if modes:
        modes = re.sub(r"\s*,\s*", ", ", modes.strip())

    return {
        "author": author,
        "modes": modes,
        "resolution": resolution,
    }



def parse_autodarts_theme_author(css_text: str) -> str:
    return parse_autodarts_theme_metadata(css_text).get("author", "")


def autodarts_theme_preview_path_for_css(path: Path) -> Path | None:
    stem = path.stem
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        candidate = path.with_suffix(ext)
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _theme_dir_fingerprint(dir_path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    try:
        css_files = sorted(dir_path.glob("*.css"), key=lambda p: p.name.lower())
        for f in css_files:
            h.update(f.name.encode())
            h.update(f.read_bytes())
    except Exception:
        pass
    return h.hexdigest()


def fetch_themes_from_release_zip() -> dict:
    zip_url = WEBPANEL_RAW_BASE + "/webpanel.zip"
    result_base = {"updated": False, "theme_count": 0, "backup_path": "", "offline": False, "error": ""}

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        zip_file = tmp / "webpanel.zip"

        try:
            req = urllib.request.Request(zip_url, headers={"User-Agent": "autodarts-webpanel/theme-fetch"})
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                zip_file.write_bytes(resp.read())
        except Exception as e:
            return {**result_base, "offline": True, "error": str(e)}

        extract_dir = tmp / "extracted"
        extract_dir.mkdir()
        try:
            with zipfile.ZipFile(zip_file) as zf:
                theme_entries = [n for n in zf.namelist() if re.match(r"theme/[^/]+$", n) and not n.endswith("/")]
                if not theme_entries:
                    return {**result_base, "error": "Kein theme/-Ordner in webpanel.zip"}
                for name in theme_entries:
                    zf.extract(name, extract_dir)
        except Exception as e:
            return {**result_base, "error": str(e)}

        zip_theme_dir = extract_dir / "theme"

        local_fp = _theme_dir_fingerprint(AUTODARTS_THEME_DIR)
        remote_fp = _theme_dir_fingerprint(zip_theme_dir)

        local_count = len(list(AUTODARTS_THEME_DIR.glob("*.css"))) if AUTODARTS_THEME_DIR.exists() else 0
        if local_fp == remote_fp:
            return {**result_base, "theme_count": local_count}

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = ""
        if AUTODARTS_THEME_DIR.exists():
            backup_dir = AUTODARTS_THEME_DIR.parent / f"theme.bak.{ts}"
            try:
                shutil.copytree(str(AUTODARTS_THEME_DIR), str(backup_dir))
                backup_path = str(backup_dir)
                shutil.rmtree(str(AUTODARTS_THEME_DIR))
            except Exception as e:
                return {**result_base, "error": f"Backup fehlgeschlagen: {e}"}

        try:
            shutil.copytree(str(zip_theme_dir), str(AUTODARTS_THEME_DIR))
            try:
                AUTODARTS_THEME_DIR.chmod(0o777)
                for f in AUTODARTS_THEME_DIR.iterdir():
                    f.chmod(0o777)
            except Exception:
                pass
        except Exception as e:
            return {**result_base, "backup_path": backup_path, "error": f"Kopieren fehlgeschlagen: {e}"}

        new_count = len(list(AUTODARTS_THEME_DIR.glob("*.css")))
        return {**result_base, "updated": True, "theme_count": new_count, "backup_path": backup_path}


def list_autodarts_themes() -> list[dict]:
    try:
        AUTODARTS_THEME_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    items: list[dict] = []
    try:
        candidates = sorted(AUTODARTS_THEME_DIR.glob("*.css"), key=lambda p: p.name.lower())
    except Exception:
        candidates = []

    for path in candidates:
        try:
            css_text = path.read_text(encoding="utf-8")
        except Exception:
            css_text = ""
        preview_path = autodarts_theme_preview_path_for_css(path)
        theme_meta = parse_autodarts_theme_metadata(css_text)
        items.append({
            "name": path.stem,
            "filename": path.name,
            "author": theme_meta.get("author", ""),
            "modes": theme_meta.get("modes", ""),
            "resolution": theme_meta.get("resolution", ""),
            "path": str(path),
            "preview_filename": preview_path.name if preview_path else "",
            "has_preview": bool(preview_path),
        })
    return items


def get_autodarts_theme_entry(filename: str | None):
    filename = (filename or "").strip()
    if not filename or filename == "default":
        return None
    for item in list_autodarts_themes():
        if item.get("filename") == filename:
            return item
    return None


def get_selected_autodarts_theme_name() -> str:
    state = load_autodarts_theme_state()
    selected = str(state.get("selected") or "default").strip() or "default"
    if selected == "default":
        return "default"
    return selected if get_autodarts_theme_entry(selected) else "default"


def read_selected_autodarts_theme_css() -> tuple[str, dict | None]:
    selected = get_selected_autodarts_theme_name()
    entry = get_autodarts_theme_entry(selected)
    if not entry:
        return "", None
    try:
        css = Path(entry["path"]).read_text(encoding="utf-8")
    except Exception:
        return "", entry
    return css, entry



def _caller_user_candidates() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add(username: str | None):
        username = (username or "").strip()
        if not username or username in seen or username == "root":
            return
        try:
            home = Path(pwd.getpwnam(username).pw_dir)
        except Exception:
            return
        if not home.exists():
            return
        seen.add(username)
        out.append(username)

    for username in _display_user_candidates():
        add(username)

    try:
        for entry in sorted(Path('/home').iterdir()):
            if entry.is_dir():
                add(entry.name)
    except Exception:
        pass

    return out


def _caller_desktop_dir_for_user(username: str) -> Path:
    pwent = pwd.getpwnam(username)
    home_dir = Path(pwent.pw_dir)
    desktop_dir = home_dir / 'Desktop'

    xdg_cmd = shutil.which('xdg-user-dir')
    if xdg_cmd:
        env = os.environ.copy()
        env['HOME'] = str(home_dir)
        env.setdefault('USER', username)
        env.setdefault('LOGNAME', username)
        try:
            result = _run_subprocess_as_user([xdg_cmd, 'DESKTOP'], username, env, timeout=3.0)
            candidate = (result.stdout or '').strip()
            if result.returncode == 0 and candidate.startswith('/'):
                desktop_dir = Path(candidate)
        except Exception:
            pass

    return desktop_dir


def _caller_install_target() -> tuple[str | None, Path | None, Path | None, str]:
    for username in _caller_user_candidates():
        try:
            desktop_dir = _caller_desktop_dir_for_user(username)
        except Exception:
            continue
        target_dir = desktop_dir / AUTODARTS_CALLER_DIR_NAME
        return username, desktop_dir, target_dir, ''
    return None, None, None, t('caller.install_user_missing', 'Es konnte kein passender lokaler Desktop-Benutzer gefunden werden.')


def _caller_strip_root_folder(names: list[str]) -> str:
    roots: set[str] = set()
    for raw_name in names:
        name = str(raw_name or '').replace('\\', '/').strip('/')
        if not name or name.startswith('__MACOSX/'):
            continue
        parts = [p for p in name.split('/') if p]
        if not parts:
            continue
        roots.add(parts[0])
    if len(roots) != 1:
        return ''
    root = next(iter(roots))
    for raw_name in names:
        name = str(raw_name or '').replace('\\', '/').strip('/')
        if not name or name.startswith('__MACOSX/'):
            continue
        parts = [p for p in name.split('/') if p]
        if len(parts) <= 1:
            return ''
        if parts[0] != root:
            return ''
    return root


def _caller_extract_zip(zip_path: Path, target_dir: Path):
    with zipfile.ZipFile(zip_path, 'r') as zf:
        strip_root = _caller_strip_root_folder(zf.namelist())
        for info in zf.infolist():
            raw_name = str(info.filename or '').replace('\\', '/').strip('/')
            if not raw_name or raw_name.startswith('__MACOSX/'):
                continue

            parts = list(PurePosixPath(raw_name).parts)
            if any(part in ('..', '') for part in parts):
                continue
            if strip_root and parts and parts[0] == strip_root:
                parts = parts[1:]
            if not parts:
                continue

            dest = target_dir.joinpath(*parts)
            try:
                dest.relative_to(target_dir)
            except Exception:
                continue

            if info.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue

            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, 'r') as src, open(dest, 'wb') as dst:
                shutil.copyfileobj(src, dst)

            mode = (info.external_attr >> 16) & 0o777
            if mode:
                try:
                    os.chmod(dest, mode)
                except Exception:
                    pass


def _caller_chown_recursive(path: Path, username: str):
    try:
        pwent = pwd.getpwnam(username)
    except Exception:
        return
    uid, gid = pwent.pw_uid, pwent.pw_gid

    for root, dirs, files in os.walk(path):
        try:
            os.chown(root, uid, gid)
        except Exception:
            pass
        for name in dirs:
            try:
                os.chown(os.path.join(root, name), uid, gid)
            except Exception:
                pass
        for name in files:
            try:
                os.chown(os.path.join(root, name), uid, gid)
            except Exception:
                pass

    try:
        os.chown(path, uid, gid)
    except Exception:
        pass


def install_caller_locally() -> dict:
    username, desktop_dir, target_dir, err = _caller_install_target()
    if not username or not desktop_dir or not target_dir:
        return {'ok': False, 'message': err or t('caller.install_failed', 'Caller konnte nicht installiert werden.')}

    if not AUTODARTS_CALLER_INSTALL_LOCK.acquire(blocking=False):
        return {'ok': False, 'message': t('caller.install_busy', 'Eine Caller-Installation läuft bereits. Bitte kurz warten und erneut versuchen.')}

    try:
        desktop_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='autodarts-caller-') as tmpdir:
            tmpdir_path = Path(tmpdir)
            zip_path = tmpdir_path / 'CALLER_ALL.zip'

            req = urllib.request.Request(AUTODARTS_CALLER_ZIP_URL, headers={'User-Agent': 'Autodarts-Webpanel/1.0'})
            with urllib.request.urlopen(req, timeout=60) as resp, open(zip_path, 'wb') as out_file:
                shutil.copyfileobj(resp, out_file)

            if target_dir.exists():
                shutil.rmtree(target_dir)
            target_dir.mkdir(parents=True, exist_ok=True)
            _caller_extract_zip(zip_path, target_dir)
            _caller_chown_recursive(target_dir, username)

        return {
            'ok': True,
            'message': t('caller.install_success', 'Caller wurde auf diesem Raspberry nach {path} installiert.', path=str(target_dir)),
            'target_path': str(target_dir),
            'desktop_path': str(desktop_dir),
            'username': username,
        }
    except urllib.error.URLError as exc:
        return {'ok': False, 'message': t('caller.install_download_failed', 'Download der Caller ZIP fehlgeschlagen: {error}', error=exc)}
    except zipfile.BadZipFile:
        return {'ok': False, 'message': t('caller.install_zip_invalid', 'Die geladene Caller ZIP ist ungültig oder beschädigt.')}
    except Exception as exc:
        return {'ok': False, 'message': t('caller.install_failed', 'Caller konnte nicht installiert werden: {error}', error=exc)}
    finally:
        try:
            AUTODARTS_CALLER_INSTALL_LOCK.release()
        except Exception:
            pass


def render_windows_caller_installer_bat() -> str:
    url = AUTODARTS_CALLER_ZIP_URL.replace("'", "''")
    folder = AUTODARTS_CALLER_DIR_NAME.replace('"', '')
    script = f"""@echo off
setlocal EnableExtensions

set "CALLER_URL={url}"
set "TARGET_NAME={folder}"

for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESKTOP_DIR=%%I"
if not defined DESKTOP_DIR set "DESKTOP_DIR=%USERPROFILE%\\Desktop"

set "TARGET_DIR=%DESKTOP_DIR%\\%TARGET_NAME%"
set "ZIP_FILE=%TEMP%\\CALLER_ALL.zip"

echo.
echo Caller wird nach "%TARGET_DIR%" installiert...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "try {{ Invoke-WebRequest -Uri '%CALLER_URL%' -UseBasicParsing -OutFile '%ZIP_FILE%' }} catch {{ exit 1 }}"
if errorlevel 1 goto :download_error

if exist "%TARGET_DIR%" rmdir /s /q "%TARGET_DIR%"
mkdir "%TARGET_DIR%" >nul 2>&1

powershell -NoProfile -ExecutionPolicy Bypass -Command "try {{ Expand-Archive -LiteralPath '%ZIP_FILE%' -DestinationPath '%TARGET_DIR%' -Force }} catch {{ exit 1 }}"
if errorlevel 1 goto :extract_error

powershell -NoProfile -ExecutionPolicy Bypass -Command "$target='%TARGET_DIR%'; $items=Get-ChildItem -LiteralPath $target -Force; if($items.Count -eq 1 -and $items[0].PSIsContainer) {{ $inner=$items[0].FullName; Get-ChildItem -LiteralPath $inner -Force | ForEach-Object {{ Move-Item -LiteralPath $_.FullName -Destination $target -Force }}; Remove-Item -LiteralPath $inner -Force -Recurse }}"

del "%ZIP_FILE%" >nul 2>&1

echo.
echo Fertig. Der Ordner "%TARGET_DIR%" ist jetzt auf dem Desktop.
pause
exit /b 0

:download_error
echo Fehler: Caller ZIP konnte nicht heruntergeladen werden.
pause
exit /b 1

:extract_error
echo Fehler: Caller ZIP konnte nicht entpackt werden.
pause
exit /b 1
"""
    return script.replace("\n", "\r\n")


@app.context_processor
def inject_i18n_helpers():
    return {
        "t": t,
        "current_lang": _get_current_lang_code(),
    }


def _forbidden_response():
    return (t("auth.forbidden", "Forbidden"), 403)



def _json_nocache(payload: dict, status: int = 200):
    resp = jsonify(payload)
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/api/langs", methods=["GET"])
def api_langs():
    languages, sources, err = _load_all_lang_jsons()
    status = 200
    default_code, default_err = _resolve_default_lang_code(languages)

    msg_parts = [x for x in [err, default_err] if x]
    return _json_nocache({
        "ok": bool(languages),
        "count": len(languages),
        "dir": str(LANG_JSON_DIR),
        "config_file": str(LANG_CONFIG_PATH),
        "default": default_code,
        "sources": sources,
        "data": languages,
        "msg": " | ".join(msg_parts) if msg_parts else None,
    }, status)


@app.route("/api/lang/default", methods=["GET", "POST", "PUT"])
def api_lang_default():
    languages, sources, err = _load_all_lang_jsons()
    default_code, default_err = _resolve_default_lang_code(languages)

    if request.method == "GET":
        msg_parts = [x for x in [err, default_err] if x]
        return _json_nocache({
            "ok": bool(default_code),
            "default": default_code,
            "config_file": str(LANG_CONFIG_PATH),
            "available": sorted(languages.keys()),
            "sources": sources,
            "msg": " | ".join(msg_parts) if msg_parts else None,
        }, 200)

    payload = request.get_json(silent=True) or {}
    requested_code = (
        payload.get("default")
        or payload.get("abk")
        or payload.get("code")
        or request.form.get("default")
        or request.form.get("abk")
        or request.form.get("code")
        or request.args.get("default")
        or request.args.get("abk")
        or request.args.get("code")
    )
    normalized = _normalize_lang_code(requested_code)
    if not normalized:
        return _json_nocache({
            "ok": False,
            "msg": "Bitte einen Sprachcode senden, z. B. {'default':'de'}.",
            "available": sorted(languages.keys()),
        }, 200)

    if languages and normalized not in languages:
        return _json_nocache({
            "ok": False,
            "default": default_code,
            "requested": normalized,
            "available": sorted(languages.keys()),
            "msg": f"Sprache '{normalized}' wurde in {LANG_JSON_DIR} nicht gefunden.",
        }, 200)

    ok, write_err = _write_lang_default_config(normalized)
    if not ok:
        return _json_nocache({
            "ok": False,
            "requested": normalized,
            "msg": write_err,
            "config_file": str(LANG_CONFIG_PATH),
        }, 200)

    return _json_nocache({
        "ok": True,
        "default": normalized,
        "requested": normalized,
        "config_file": str(LANG_CONFIG_PATH),
        "available": sorted(languages.keys()),
        "msg": f"Default-Sprache wurde auf '{normalized}' gesetzt.",
    })


@app.route("/lang/all.js", methods=["GET"])
@app.route("/langs.js", methods=["GET"])
def lang_all_js():
    languages, sources, err = _load_all_lang_jsons()
    payload = json.dumps(languages, ensure_ascii=False)
    default_code, default_err = _resolve_default_lang_code(languages)

    js_lines = [
        f"window.lang_code = {payload};",
        "window.lang = window.lang_code || {};",
        f"window.lang_sources = {json.dumps(sources, ensure_ascii=False)};",
        f"window.lang_dir = {json.dumps(str(LANG_JSON_DIR), ensure_ascii=False)};",
        f"window.lang_default = {json.dumps(default_code, ensure_ascii=False)};",
        f"window.lang_config_file = {json.dumps(str(LANG_CONFIG_PATH), ensure_ascii=False)};",
    ]

    if err:
        js_lines.append(f"window.lang_error = {json.dumps(err, ensure_ascii=False)};")
    if default_err:
        js_lines.append(f"window.lang_default_error = {json.dumps(default_err, ensure_ascii=False)};")

    js = "\n".join(js_lines) + "\n"
    resp = Response(js, status=200, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/lang/<code>", methods=["GET"])
def api_lang(code: str):
    data, path, err, normalized = _load_lang_json(code)
    if err:
        return _json_nocache({"ok": False, "code": normalized, "msg": err, "source": path}, 200)

    languages, _, _ = _load_all_lang_jsons()
    default_code, default_err = _resolve_default_lang_code(languages)

    return _json_nocache({
        "ok": True,
        "code": normalized,
        "source": path,
        "is_default": normalized == default_code,
        "default": default_code,
        "default_msg": default_err,
        "data": data,
    })


@app.route("/lang/<code>.js", methods=["GET"])
def lang_js(code: str):
    data, path, err, normalized = _load_lang_json(code)
    if err:
        js = "window.lang_code = {};\n"
        js += "window.lang = window.lang || {};\n"
        js += f"window.lang[{json.dumps(normalized or code)}] = window.lang_code\n"
        js += f"window.lang_error = {json.dumps(err, ensure_ascii=False)}\n"
        return Response(js, status=200, mimetype="application/javascript")

    languages, _, _ = _load_all_lang_jsons()
    default_code, default_err = _resolve_default_lang_code(languages)

    payload = json.dumps(data, ensure_ascii=False)
    lang_key = normalized.replace("-", "_")
    js = "\n".join([
        f"window.lang_code = {payload};",
        "window.lang = window.lang || {};",
        f"window.lang[{json.dumps(normalized)}] = window.lang_code;",
        f'window["lang_{lang_key}"] = window.lang_code;',
        f"window.lang_source = {json.dumps(path, ensure_ascii=False)};",
        f"window.lang_default = {json.dumps(default_code, ensure_ascii=False)};",
        f"window.lang_is_default = {json.dumps(normalized == default_code)};",
        f"window.lang_config_file = {json.dumps(str(LANG_CONFIG_PATH), ensure_ascii=False)};",
    ]) + "\n"

    if default_err:
        js += f"window.lang_default_error = {json.dumps(default_err, ensure_ascii=False)}\n"

    resp = Response(js, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/help", methods=["GET"])
def help_page():
    """Handbuch als PDF (inline) aus AUTODARTS_DATA_DIR ausliefern."""
    pdf_path = DATA_DIR / HELP_PDF_FILENAME
    if not pdf_path.exists():
        return _inline_notice_page(
            t("help.not_found_title", "Handbuch nicht gefunden"),
            f"<p>{t('help.not_found_text', 'Die Datei <code>{filename}</code> wurde nicht gefunden.', filename=HELP_PDF_FILENAME)}</p>",
            status=404,
        )

    resp = send_from_directory(str(DATA_DIR), HELP_PDF_FILENAME)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'inline; filename="{HELP_PDF_FILENAME}"'
    return resp




@app.route("/api/autodarts/display-info", methods=["GET"])
def api_autodarts_display_info():
    return _json_nocache({
        "ok": True,
        "display_info": get_autodarts_display_info(),
    })


@app.route("/api/autodarts/display-mode", methods=["POST"])
def api_autodarts_display_mode():
    payload = request.get_json(silent=True) or {}
    output_name = str(payload.get("output") or "").strip()
    mode_key = str(payload.get("mode") or payload.get("mode_key") or "").strip()
    result = apply_autodarts_display_mode(output_name, mode_key)
    status = 200 if result.get("ok") else 200
    return _json_nocache(result, status)


@app.route("/api/autodarts/display-backend", methods=["POST"])
def api_autodarts_display_backend():
    payload = request.get_json(silent=True) or {}
    target_backend = str(payload.get("backend") or payload.get("target_backend") or "").strip()
    result = apply_autodarts_display_backend(target_backend)
    status = 200 if result.get("ok") else 200
    return _json_nocache(result, status)


@app.route("/api/autodarts/open-display-settings", methods=["POST"])
def api_autodarts_open_display_settings():
    probe = _probe_display_session()
    if not probe.get("ok"):
        return _json_nocache({"ok": False, "message": t("themes.display_no_session", "Keine passende Desktop-Sitzung gefunden.")})

    backend = str(probe.get("backend") or "x11").lower()
    user = str(probe.get("user") or "").strip()

    if backend == "wayland":
        candidates = ["wdisplays", "lxrandr", "arandr"]
        env = _build_display_env(user, wayland_display=str(probe.get("wayland_display") or "wayland-1"))
    else:
        candidates = ["lxrandr", "arandr", "xfce4-display-settings"]
        env = _build_display_env(user, display_name=str(probe.get("display") or ":0"))

    for tool in candidates:
        tool_path = shutil.which(tool)
        if not tool_path:
            continue
        try:
            cmd = (["sudo", "-u", user, "--", tool_path] if user else [tool_path])
            subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return _json_nocache({"ok": True, "message": t("themes.display_fix_opened", "Systemeinstellungen wurden am Pi geöffnet.")})
        except Exception:
            continue

    return _json_nocache({"ok": False, "message": t("themes.display_fix_failed", "Systemeinstellungen konnten nicht geöffnet werden.")})


@app.route("/api/autodarts-theme/preview/<path:filename>", methods=["GET"])
def api_autodarts_theme_preview(filename):
    safe_name = os.path.basename(str(filename or "").strip())
    if not safe_name:
        abort(404)
    css_name = Path(safe_name).stem + ".css"
    entry = get_autodarts_theme_entry(css_name)
    if not entry:
        abort(404)
    css_path = Path(entry.get("path", ""))
    preview_path = autodarts_theme_preview_path_for_css(css_path)
    if not preview_path:
        abort(404)
    return send_from_directory(str(AUTODARTS_THEME_DIR), preview_path.name, as_attachment=False)


@app.route("/api/autodarts-theme/state", methods=["GET"])
def api_autodarts_theme_state():
    themes = list_autodarts_themes()
    selected = get_selected_autodarts_theme_name()
    entry = get_autodarts_theme_entry(selected)
    return jsonify({
        "ok": True,
        "selected": selected,
        "author": (entry or {}).get("author", ""),
        "modes": (entry or {}).get("modes", ""),
        "resolution": (entry or {}).get("resolution", ""),
        "panel_url": AUTODARTS_THEME_PANEL_URL,
        "theme_dir": str(AUTODARTS_THEME_DIR),
        "store_url": AUTODARTS_THEME_STORE_URL,
        "themes": themes,
    })


@app.route("/api/autodarts-theme/sync", methods=["GET"])
def api_autodarts_theme_sync():
    """Return all theme CSS + metadata for one-time browser synchronization.

    This is additive: the existing /api/autodarts-theme.css endpoint remains
    unchanged so older browser extensions continue to work.
    """
    bundle = [{
        "name": "Default",
        "filename": "default",
        "author": "",
        "modes": "",
        "resolution": "",
        "css": "",
    }]

    for entry in list_autodarts_themes():
        try:
            css_text = Path(entry.get("path", "")).read_text(encoding="utf-8")
        except Exception:
            css_text = ""
        bundle.append({
            "name": entry.get("name", ""),
            "filename": entry.get("filename", ""),
            "author": entry.get("author", ""),
            "modes": entry.get("modes", ""),
            "resolution": entry.get("resolution", ""),
            "css": css_text,
        })

    return _json_nocache({
        "ok": True,
        "schema": 1,
        "selected": get_selected_autodarts_theme_name(),
        "themes": bundle,
    })


@app.route("/api/autodarts-theme/fetch-from-zip", methods=["POST"])
def api_autodarts_theme_fetch_from_zip():
    if not bool(session.get("admin_unlocked", False)):
        return jsonify({"ok": False, "error": "Not authorized"}), 403
    result = fetch_themes_from_release_zip()
    return _json_nocache({
        "ok": True,
        "updated": result.get("updated", False),
        "theme_count": result.get("theme_count", 0),
        "backup_path": result.get("backup_path", ""),
        "offline": result.get("offline", False),
        "error": result.get("error", ""),
    })


@app.route("/api/autodarts-theme/select", methods=["POST"])
def api_autodarts_theme_select():
    payload = request.get_json(silent=True) if request.is_json else None
    selected = ""
    if isinstance(payload, dict):
        selected = str(payload.get("selected") or "")
    if not selected:
        selected = str(request.form.get("selected") or request.args.get("selected") or "")
    selected = selected.strip() or "default"

    if selected != "default" and not get_autodarts_theme_entry(selected):
        return jsonify({
            "ok": False,
            "message": t("themes.invalid", "Ungültiges Theme ausgewählt."),
        }), 400

    state = save_autodarts_theme_state(selected)
    entry = get_autodarts_theme_entry(state.get("selected"))
    author = (entry or {}).get("author", "")
    if state.get("selected") == "default":
        message = t("themes.saved_default", "Default-Theme aktiviert.")
    else:
        message = t("themes.saved_theme", "Theme '{theme}' aktiviert.", theme=(entry or {}).get("name", state.get("selected")))

    return jsonify({
        "ok": True,
        "selected": state.get("selected", "default"),
        "theme": (entry or {}).get("name", "default"),
        "filename": (entry or {}).get("filename", "default"),
        "author": author,
        "modes": (entry or {}).get("modes", ""),
        "resolution": (entry or {}).get("resolution", ""),
        "message": message,
    })


@app.route("/api/autodarts-theme.css", methods=["GET"])
def api_autodarts_theme_css():
    css, entry = read_selected_autodarts_theme_css()
    headers = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    if not css:
        return Response("", mimetype="text/css", headers=headers)
    return Response(css, mimetype="text/css", headers=headers)


@app.route("/api/caller/install-local", methods=["POST"])
def api_caller_install_local():
    result = install_caller_locally()
    status = 200 if result.get("ok") else 500
    return _json_nocache(result, status)


@app.route("/download/windows-caller-install.bat", methods=["GET"])
def download_windows_caller_install_bat():
    content = render_windows_caller_installer_bat()
    resp = Response(content, mimetype="application/octet-stream")
    resp.headers["Content-Disposition"] = f'attachment; filename="{AUTODARTS_CALLER_WINDOWS_INSTALLER_FILENAME}"'
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.route("/admin/pi5-headless/<mode>", methods=["POST"])
def admin_pi5_headless(mode):
    mode = (mode or "").strip().lower()
    if mode not in {"on", "off"}:
        return redirect(url_for("index", msg=t("pi5_headless.invalid_mode", "Ungültiger Headless-Modus.")))

    result = apply_pi5_headless_mode(mode == "on")
    msg = result.get("message") or (t("common.done", "Fertig.") if result.get("ok") else t("generic.failed", "Fehlgeschlagen."))
    return redirect(url_for("index", msg=msg))


# --- WHY these two popups exist (do not remove this comment) ---
#
# Old systems have Autodarts 1.0 + an old webpanel updater that does NOT know about
# the revision chain (update-revisions.conf). When such a system runs the first webpanel
# update, the old updater installs the new webpanel �?? but the revision chain never runs
# because the old updater doesn't know about it (it just copies "latest" without checkpoints).
#
# The fix is a two-step process, driven by these two popups:
#
#   Popup 1 �?? "Autodarts 2.0 installiert!" (this one, v2WelcomeOverlay):
#     Shows after the v2 migration completes. The button calls /api/v2-chain-fix, which
#     resets the local webpanel version to 1.800 and triggers a fresh update run.
#
#   Popup 2 �?? shown after the update finishes:
#     Now the NEW updater is running and recognises the revision chain. It walks through
#     all missing checkpoints (e.g. V1.809 → latest) so nothing is skipped.
#
# Without these popups, old systems would never re-run the chain and would silently
# miss required migration steps. The popups are not cosmetic �?? they are the mechanism.
#
# Flag rule: the flag is written when the user CLICKS the button (in /api/v2-chain-fix),
# NOT on render. This way a browser refresh before clicking does not eat the popup.

def check_v2_welcome():
    """Returns True if the v2 welcome popup should be shown. Does NOT set the flag."""
    if os.path.exists(V2_WELCOME_FLAG_FILE):
        return False
    try:
        with open(V2_MIGRATION_STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        return False
    if state.get("status") != "success":
        return False
    if state.get("result") not in ("migrated_from_v1", "fresh_install", "fresh_install_from_broken"):
        return False
    return True


@app.route("/", methods=["GET"])
def index():
    ensure_msg = None

    (
        ssid, ip, lan_ip,
        autodarts_active, autodarts_version,
        cpu_pct, mem_used, mem_total, temp_c,
        wifi_ok, lan_ok, dongle_ok,
        net_ok, ping_uplink_label,
        current_ap_ssid,
    ) = get_index_stats_cached()
    autodarts_v2_1_plus = _version_gte(autodarts_version or "", "2.1.0")
    wifi_signal = None  # Signalstärke wird nur auf Knopfdruck geladen
    ad_restarted = request.args.get("ad_restarted") == "1"
    wifi_conn_name, wifi_autoconnect_enabled = get_active_wifi_autoconnect_state()
    direct_connected, direct_ssid, direct_ip = get_direct_connect_state()

    autodarts_auto_update = bool(SETTINGS.get("autodarts_auto_update", False))
    autodarts_update_just_started = False
    if autodarts_auto_update:
        if autodarts_update_is_running():
            autodarts_update_just_started = True  # läuft noch → pollen
        else:
            try:
                stamp_age = time.time() - os.path.getmtime(AUTODARTS_AUTO_UPDATE_STAMP)
            except OSError:
                stamp_age = 9999
            if stamp_age >= 3600:
                try:
                    open(AUTODARTS_AUTO_UPDATE_STAMP, "w").close()
                except Exception:
                    pass
                start_autodarts_update_background()
                autodarts_update_just_started = True



    cam_config = load_cam_config()
    camera_mode = _camera_mode_runtime_active(cam_config, autodarts_active=autodarts_active)
    cam_info_message = ""

    # Nach Service-Neustart oder bei inkonsistentem Zustand (Autodarts läuft bereits)
    # soll der Kamera-Modus sicher AUS sein und nichts blockieren.
    if bool(cam_config.get("camera_mode", False)) and not camera_mode:
        subprocess.run(["pkill", "-f", "mjpg_streamer"], capture_output=True, text=True)
        _set_camera_mode_state(cam_config, False)
        save_cam_config(cam_config)

    cam_inventory = []
    cam_slots = []
    cam_devices = []
    cam_count_found = 0

    if camera_mode:
        cam_inventory = cam_config.get("camera_inventory", [])
        if not isinstance(cam_inventory, list):
            cam_inventory = []

        if not cam_inventory:
            legacy_devices = cam_config.get("devices", [])
            if isinstance(legacy_devices, list) and legacy_devices:
                cam_inventory = _legacy_inventory_from_devices(legacy_devices)

        cam_slots = _normalize_camera_slots(cam_inventory, cam_config.get("camera_slots"))
        cam_devices = _devices_for_slots(cam_inventory, cam_slots)
        cam_count_found = len(cam_inventory)

        dirty_cam_cfg = False
        if cam_config.get("camera_inventory") != cam_inventory:
            cam_config["camera_inventory"] = cam_inventory
            dirty_cam_cfg = True
        if cam_config.get("camera_slots") != cam_slots:
            cam_config["camera_slots"] = cam_slots
            dirty_cam_cfg = True
        if cam_config.get("devices") != cam_devices:
            cam_config["devices"] = cam_devices
            dirty_cam_cfg = True
        if cam_config.get("desired_cams") != cam_count_found:
            cam_config["desired_cams"] = cam_count_found
            dirty_cam_cfg = True
        if dirty_cam_cfg:
            save_cam_config(cam_config)

        if cam_count_found == 0:
            cam_info_message = t("camera.none_searched_yet", "Es wurden noch keine Kameras gesucht.")
    host = request.host.split(":", 1)[0]
    darts_url = f"http://{host}:3180"
    darts_ttyd_url = f"http://{host}:7681"
    autodarts_v2_running = is_autodarts_v2_running()
    autodarts_ttyd_running = is_ttyd_running()

    cam_indices = list(range(1, len(cam_slots) + 1))

    # LED panel info
    caller_email, caller_password, caller_board_id, caller_exists, caller_err = read_darts_caller_credentials()
    caller_installed = os.path.exists(DARTS_CALLER_DIR)
    wled_installed = os.path.exists(DARTS_WLED_DIR)
    # WLED / LED-Bänder (User-UI: nur Ein/Aus)
    wled_cfg = load_wled_config()

    wled_master_enabled = bool(wled_cfg.get("master_enabled", True))

    admin_unlocked = bool(session.get('admin_unlocked', False))
    adminerr = request.args.get('adminerr')
    adminmsg = (request.args.get('adminmsg') or '').strip()
    adminok = (request.args.get('adminok') == '1')

    msg = request.args.get('msg', '') or (ensure_msg or '')
    open_adver = (request.args.get('open_adver') == '1')

    webpanel_version = get_webpanel_version()
    webpanel_check = load_webpanel_update_check()
    _wc = SETTINGS.get("webpanel_channel", "stable")
    webpanel_update_available = (_wc == "beta") or bool(webpanel_version and webpanel_check.get('latest') and webpanel_version != webpanel_check.get('latest'))
    webpanel_state = load_webpanel_update_state() if admin_unlocked else {}
    webpanel_log_tail = tail_file(WEBPANEL_UPDATE_LOG, n=25, max_chars=3500) if admin_unlocked else ""
    uvc_backup_info = get_uvc_backup_info() if admin_unlocked else {}

    extensions_state = load_extensions_update_state() if admin_unlocked else {}
    extensions_last = load_extensions_update_last() if admin_unlocked else {}
    extensions_log_tail = tail_file(EXTENSIONS_UPDATE_LOG, n=25, max_chars=3500) if admin_unlocked else ""
    extensions_v2 = extensions_v2_status()

    os_update_state = load_os_update_state() if admin_unlocked else {}
    os_update_log_tail = tail_file(OS_UPDATE_LOG, n=25, max_chars=3500) if admin_unlocked else ""

    ufw_installed = ufw_is_installed()
    ufw_state = load_ufw_state() if admin_unlocked else {}
    if admin_unlocked and ufw_state.get("status") == "installing":
        ufw_state = ufw_refresh_state()
        ufw_installed = bool(ufw_state.get("installed"))

    wled_targets = wled_cfg.get("targets", []) or []
    while len(wled_targets) < 3:
        wled_targets.append({"label": f"Dart LED{len(wled_targets)+1}", "host": "", "enabled": False})
    wled_targets = wled_targets[:3]

    wled_bands = []
    wled_hosts = []
    for i, t in enumerate(wled_targets, start=1):
        wled_bands.append({"slot": i, "enabled": bool(t.get("enabled", False))})
        wled_hosts.append(str(t.get("host", "")).strip())
    wled_service_exists = service_exists(DARTS_WLED_SERVICE)
    wled_service_active = service_is_active(DARTS_WLED_SERVICE) if wled_service_exists else False
    caller_enabled = caller_extensions_enabled()
    caller_service_exists = service_exists(DARTS_CALLER_SERVICE)
    caller_service_active = service_is_active(DARTS_CALLER_SERVICE) if caller_service_exists else False

    creds_ok = bool(caller_email and caller_board_id)

    ledcheck = request.args.get("ledcheck", "")
    ledmsg = request.args.get("ledmsg", "")

    # Buttons
    can_save_creds = caller_exists
    can_check = caller_installed and caller_exists and bool(caller_email and caller_board_id)

    # Admin / Doku
    admin_gpio_exists = os.path.exists(ADMIN_GPIO_IMAGE)
    pi_csv_tail = tail_file(PI_MONITOR_CSV, n=20)
    pi_csv_exists = os.path.exists(PI_MONITOR_CSV)
    pi_mon_status = get_pi_monitor_status()
    pi_readme_exists = os.path.exists(PI_MONITOR_README)

    return render_template(
        "index.html",
        darts_url=darts_url,
        max_cams=MAX_CAMERAS,
        cam_inventory=cam_inventory,
        cam_slots=cam_slots,
        cam_devices=cam_devices,
        cam_count_found=cam_count_found,
        cam_info_message=cam_info_message,
        base_port=STREAM_BASE_PORT,
        cpu_pct=cpu_pct,
        mem_used=mem_used,
        mem_total=mem_total,
        temp_c=temp_c,
        cam_indices=cam_indices,
        caller_email=caller_email,
        caller_board_id=caller_board_id,
        caller_exists=caller_exists,
        caller_err=caller_err,
        ledcheck=ledcheck,
        ledmsg=ledmsg,
        can_save_creds=can_save_creds,
        can_check=can_check,
        wled_installed=wled_installed,
        wled_bands=wled_bands,
        wled_hosts=wled_hosts,
        wled_master_enabled=wled_master_enabled,
        caller_enabled=caller_enabled,
        caller_service_exists=caller_service_exists,
        caller_service_active=caller_service_active,
        admin_unlocked=admin_unlocked,
        os_update_state=os_update_state,
        os_update_log_tail=os_update_log_tail,
        ufw_installed=ufw_installed,
        ufw_state=ufw_state,
        adminerr=adminerr,
        adminmsg=adminmsg,
        adminok=adminok,
        wled_service_exists=wled_service_exists,
        wled_service_active=wled_service_active,
        admin_gpio_exists=admin_gpio_exists,
        pi_csv_tail=pi_csv_tail,
        pi_mon_status=pi_mon_status,
        pi_csv_exists=pi_csv_exists,
        pi_readme_exists=pi_readme_exists,
        pi_monitor_script=PI_MONITOR_SCRIPT,
        pi_monitor_csv=PI_MONITOR_CSV,
        pi_monitor_readme=PI_MONITOR_README,
        pi_monitor_outlog=PI_MONITOR_OUTLOG,
        usr_local_bin_dir=USR_LOCAL_BIN_DIR,
        autodarts_data_dir=AUTODARTS_DATA_DIR,
        extensions_dir=EXTENSIONS_DIR,
        autodarts_active=autodarts_active,
        autodarts_version=autodarts_version,
        autodarts_v2_1_plus=autodarts_v2_1_plus,
        autodarts_auto_update=autodarts_auto_update,
        autodarts_update_just_started=autodarts_update_just_started,
        autodarts_autostart_enabled=get_autodarts_autostart_enabled(),
        camera_mode=camera_mode,
        autodarts_notice=ad_restarted,
        wifi_ok=wifi_ok,
        dongle_ok=dongle_ok,
        net_ok=net_ok,
        direct_connected=direct_connected,
        direct_ssid=direct_ssid,
        direct_ip=direct_ip,
        ping_uplink_label=ping_uplink_label,
        current_ap_ssid=current_ap_ssid,
        ssid=ssid,
        ip=ip,
        wifi_signal=wifi_signal,
        wifi_conn_name=wifi_conn_name,
        wifi_autoconnect_enabled=wifi_autoconnect_enabled,
        msg=msg,
        wifi_interface=WIFI_INTERFACE,
        webpanel_version=webpanel_version,
        webpanel_check=webpanel_check,
        webpanel_update_available=webpanel_update_available,
        webpanel_state=webpanel_state,
        webpanel_log_tail=webpanel_log_tail,
        webpanel_channel=str(SETTINGS.get("webpanel_channel") or "stable"),
        uvc_backup_info=uvc_backup_info,
        settings_path=SETTINGS_PATH,
        extensions_state=extensions_state,
        extensions_last=extensions_last,
        extensions_log_tail=extensions_log_tail,
        extensions_v2=extensions_v2,
        lan_ok=lan_ok,
        lan_ip=lan_ip,
        autodarts_themes=list_autodarts_themes(),
        autodarts_theme_selected=get_selected_autodarts_theme_name(),
        autodarts_theme_dir=str(AUTODARTS_THEME_DIR),
        autodarts_theme_panel_url=AUTODARTS_THEME_PANEL_URL,
        autodarts_theme_store_url=AUTODARTS_THEME_STORE_URL,
        autodarts_display_info=get_autodarts_display_info(),
        pi5_headless_info=get_pi5_headless_info(),
        darts_ttyd_url=darts_ttyd_url,
        autodarts_v2_running=autodarts_v2_running,
        autodarts_ttyd_running=autodarts_ttyd_running,
        show_v2_welcome=check_v2_welcome(),
    )


@app.route("/led/save", methods=["POST"])
def led_save():
    ad_email = request.form.get("ad_email", "").strip()
    ad_password = request.form.get("ad_password", "").strip()
    ad_board = request.form.get("ad_board", "").strip()

    cur_email, cur_pw, cur_board, exists, err = read_darts_caller_credentials()
    if not exists:
        return redirect(url_for("index", ledcheck="bad", ledmsg=t("caller.start_custom_missing_short", "start-custom.sh nicht gefunden.")))

    # leer lassen => unverändert
    if not ad_email:
        ad_email = cur_email
    if not ad_password:
        ad_password = cur_pw
    if not ad_board:
        ad_board = cur_board

    try:
        write_darts_caller_credentials(ad_email, ad_password, ad_board)
        return redirect(url_for("index", ledcheck="ok", ledmsg=t("caller.saved", "Gespeichert (start-custom.sh aktualisiert).")))
    except Exception as e:
        return redirect(url_for("index", ledcheck="bad", ledmsg=t("generic.save_failed", "Speichern fehlgeschlagen: {error}", error=e)))


@app.route("/led/check", methods=["POST"])
def led_check():
    email, pw, bid, exists, err = read_darts_caller_credentials()
    if not exists:
        return redirect(url_for("index", ledcheck="bad", ledmsg=t("caller.start_custom_missing_short", "start-custom.sh nicht gefunden.")))
    if err:
        return redirect(url_for("index", ledcheck="bad", ledmsg=err))
    if not email or not pw or not bid:
        return redirect(url_for("index", ledcheck="bad", ledmsg=t("caller.set_credentials_2fa_off", "Bitte Account/Passwort/Board-ID setzen. (2FA muss AUS sein)")))
    return redirect(url_for("index", ledcheck="ok", ledmsg=t("caller.data_present_2fa_off", "Daten vorhanden. Hinweis: 2FA muss AUS sein.")))




@app.route("/api/extensions-v2/status", methods=["GET"])
def api_extensions_v2_status():
    status = extensions_v2_status()
    status["log_tail"] = tail_file(EXTENSIONS_V2_INSTALL_LOG, n=20, max_chars=6000)
    return jsonify(status)


@app.route("/api/extensions-v2/board-id", methods=["POST"])
def api_extensions_v2_board_id():
    payload = request.get_json(silent=True) or {}
    board_id = str(payload.get("board_id") or "").strip()
    if not board_id:
        return jsonify({"ok": False, "message": t("extensions_v2.board_id_required", "Bitte zuerst die Board-ID eingeben.")}), 400
    config_dir = "/var/lib/autodarts/config/darts-caller"
    config_path = os.path.join(config_dir, "start-custom.sh")
    try:
        os.makedirs(config_dir, exist_ok=True)
        media_path = "/var/lib/autodarts/media"
        call_every_dart = "1"
        if os.path.isfile(config_path):
            text = Path(config_path).read_text(encoding="utf-8", errors="replace")
            m = re.search(r'-M\s+["\']?([^"\'\s]+)', text)
            if m: media_path = m.group(1)
            m = re.search(r'-E\s+["\']?([^"\'\s]+)', text)
            if m: call_every_dart = m.group(1)
        content = (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "exec \"/var/lib/autodarts/extensions/darts-caller/darts-caller\" \\\n"
            f"  -B \"{board_id}\" \\\n"
            f"  -M \"{media_path}\" \\\n"
            f"  -E \"{call_every_dart}\"\n"
        )
        Path(config_path).write_text(content, encoding="utf-8")
        os.chmod(config_path, 0o755)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@app.route("/api/extensions-v2/install", methods=["POST"])
def api_extensions_v2_install():
    current = extensions_v2_status()
    if current.get("running"):
        return jsonify({"ok": False, "message": t("extensions_v2.already_running", "Die Installation läuft bereits.")}), 409
    ok, message = start_extensions_v2_install_background()
    return jsonify({"ok": ok, "message": message}), (200 if ok else 500)


@app.route("/api/extensions-v2/auth-reset", methods=["POST"])
def api_extensions_v2_auth_reset():
    if not caller_extensions_enabled():
        return jsonify({"ok": False, "message": t("caller.auth_reset_disabled", "Caller ist deaktiviert. Bitte zuerst den Caller aktivieren.")}), 400
    ok, message = start_caller_auth_reset_background()
    host = request.host.split(":", 1)[0]
    login_url = f"https://{host}:8079"
    return jsonify({"ok": ok, "message": message, "url": login_url}), (200 if ok else 500)


@app.route("/api/caller/toggle", methods=["POST"])
def api_caller_toggle():
    data = request.get_json(silent=True) or {}
    if "enabled" in data:
        enabled = bool(data.get("enabled"))
    else:
        enabled = not caller_extensions_enabled()
    ok, msg = set_caller_extensions_enabled(enabled)
    status = caller_wled_connection_status()
    status.update({
        "wled_service_active": service_is_active(DARTS_WLED_SERVICE) if service_exists(DARTS_WLED_SERVICE) else None,
        "wled_service_enabled": service_is_enabled(DARTS_WLED_SERVICE) if service_exists(DARTS_WLED_SERVICE) else None,
        "watchdog_timer_exists": wled_watchdog_timer_exists(),
        "watchdog_timer_active": service_is_active(DARTS_WLED_WATCHDOG_TIMER) if wled_watchdog_timer_exists() else None,
        "watchdog_timer_enabled": service_is_enabled(DARTS_WLED_WATCHDOG_TIMER) if wled_watchdog_timer_exists() else None,
    })
    return jsonify({"ok": ok, "msg": msg, "status": status}), (200 if ok else 500)


@app.route("/wled-presets", methods=["GET"])
def wled_presets():
    return render_template("wled_presets.html")


def _wled_json_get(ip_or_host: str, path: str = "/json/state", timeout_s: float = 1.2):
    url = f"http://{ip_or_host}{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "AutodartsPanel"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        raw = r.read().decode("utf-8", errors="ignore") or "{}"
        return json.loads(raw)


def _wled_json_post(ip_or_host: str, payload: dict, path: str = "/json/state", timeout_s: float = 1.2):
    url = f"http://{ip_or_host}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "AutodartsPanel",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        raw = r.read().decode("utf-8", errors="ignore") or "{}"
        return json.loads(raw) if raw else {}


_WLED_MUTATION_LOCKS = {}
_WLED_MUTATION_LOCKS_GUARD = threading.Lock()
_WLED_LAST_MUTATION_AT = {}


def _wled_get_mutation_lock(ip_or_host: str):
    key = str(ip_or_host or "").strip()
    with _WLED_MUTATION_LOCKS_GUARD:
        lock = _WLED_MUTATION_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _WLED_MUTATION_LOCKS[key] = lock
        return key, lock


def _wled_begin_mutation(ip_or_host: str, min_spacing_s: float = 1.1):
    """Serialize WLED preset writes/deletes per controller.

    WLED/ESP can become unstable when pdel/psave requests are fired too quickly.
    This guard spaces mutating preset operations and prevents parallel writes to the same controller.
    """
    key, lock = _wled_get_mutation_lock(ip_or_host)
    lock.acquire()
    try:
        last = float(_WLED_LAST_MUTATION_AT.get(key) or 0.0)
        wait_s = float(min_spacing_s) - (time.monotonic() - last)
        if wait_s > 0:
            time.sleep(wait_s)
        return key, lock
    except Exception:
        lock.release()
        raise


def _wled_end_mutation(key: str, lock, settle_s: float = 0.9):
    try:
        _WLED_LAST_MUTATION_AT[key] = time.monotonic()
        if settle_s > 0:
            time.sleep(float(settle_s))
    finally:
        lock.release()


def _resolve_wled_target_for_slot_or_host(slot: int | None, fallback_host: str | None = None):
    if not caller_extensions_enabled():
        return jsonify({"ok": False, "msg": t("caller_wled.restart_caller_disabled", "Caller ist deaktiviert. Bitte zuerst den Caller aktivieren.")}), 400

    cfg = load_wled_config()
    if not bool(cfg.get("master_enabled", True)):
        return None, None, t("wled.globally_disabled", "WLED ist global deaktiviert.")

    host = (fallback_host or "").strip()
    if slot and slot > 0:
        targets = cfg.get("targets", []) or []
        if 1 <= slot <= len(targets):
            t = targets[slot - 1] or {}
            if not bool(t.get("enabled", False)):
                return None, None, t("wled.slot_disabled", "WLED Slot {slot} ist deaktiviert.", slot=slot)
            cfg_host = str(t.get("host", "")).strip()
            if cfg_host:
                host = cfg_host

    if not host:
        return None, None, t("wled.no_host_found", "Kein WLED Host gefunden.")

    ip = resolve_host_to_ip_fast(host, timeout_s=0.8)
    if not ip:
        return None, host, t("wled.host_could_not_resolve", "{host} konnte nicht aufgelöst werden.", host=host)

    if not is_wled_reachable(ip, timeout_sec=1.0):
        return None, host, t("wled.host_unreachable", "{host} ({ip}) ist nicht erreichbar.", host=host, ip=ip)

    return ip, host, None


def _wled_dmu_preset_name(dmu_key: str) -> str:
    """Return an English-only WLED preset name for visual DMU entries.

    This affects only the name written to WLED via psave. It does not change the
    generated darts-wled argument in start-custom.sh.
    """
    key = str(dmu_key or "").strip().lower()

    if key == "1":
        return "All Singles"
    if key == "2":
        return "All Doubles"
    if key == "3":
        return "All Triples"
    if key == "s25":
        return "Single Bull"
    if key == "d25":
        return "Bullseye"

    m = re.match(r"^([sdt])(\d{1,2})$", key)
    if m:
        prefix, number = m.group(1), m.group(2)
        field_type = {"s": "Single", "d": "Double", "t": "Triple"}.get(prefix, "Field")
        return f"{field_type} {number}"

    return str(key) if key else "DMU"


def _wled_preset_name_from_editor(preset: int) -> str:
    ok, _msg, rows, _weps = load_wled_presets_state()
    if not ok or preset < 1:
        return f"Preset {preset}"

    row = next((r for r in rows if int(r.get("preset") or 0) == int(preset)), None)
    if not row:
        return f"Preset {preset}"

    kind = str(row.get("kind") or "")

    # Visual DMU entries get a clean English WLED preset name.
    # The start-custom.sh argument remains unchanged and continues to use the real DMU key.
    if kind == "dmu_field":
        return _wled_dmu_preset_name(str(row.get("dmuKey") or row.get("dmu_key") or ""))

    label = str(row.get("label") or "").strip()

    if " / " in label:
        label = label.split(" / ", 1)[0].strip()

    if kind == "score_exact":
        score = int(row.get("score") or 0)
        return f"Score {score}"

    if kind == "score_range":
        from_val = int(row.get("from") or 0)
        to_val = int(row.get("to") or 0)
        return f"Score {from_val}-{to_val}"

    return label or f"Preset {preset}"


@app.route("/api/wled-presets/send", methods=["POST"])
def api_wled_presets_send():
    data = request.get_json(silent=True) or {}
    try:
        preset = int(data.get("preset") or 0)
    except Exception:
        preset = 0
    try:
        slot = int(data.get("slot") or 0)
    except Exception:
        slot = 0
    host_fallback = str(data.get("host") or "").strip()

    if preset < 1:
        return jsonify({"ok": False, "msg": t("wled.invalid_preset_number", "Ungültige Preset-Nummer.")}), 400

    ip, host, err = _resolve_wled_target_for_slot_or_host(slot, host_fallback)
    if err:
        return jsonify({"ok": False, "msg": err}), 400

    mutation_key = None
    mutation_lock = None
    try:
        mutation_key, mutation_lock = _wled_begin_mutation(ip)
        preset_name = _wled_preset_name_from_editor(preset)
        state_before = _wled_json_get(ip, "/json/state", timeout_s=1.5) or {}
        is_on = bool(state_before.get("on", False))

        if is_on:
            loaded_existing = False
            try:
                _wled_json_post(ip, {"ps": preset}, "/json/state", timeout_s=1.5)
                time.sleep(0.25)
                state_after_load = _wled_json_get(ip, "/json/state", timeout_s=1.5) or {}
                loaded_existing = int(state_after_load.get("ps", -1)) == int(preset)
            except Exception:
                loaded_existing = False

            _wled_json_post(ip, {"psave": preset, "n": preset_name, "ib": False, "sb": True}, "/json/state", timeout_s=1.8)

            if loaded_existing:
                msg = t("wled.preset_applied_and_saved", "Preset {preset} auf {host} übernommen und neu gespeichert.", preset=preset, host=host)
            else:
                msg = t("wled.preset_created_from_current_state", "Preset {preset} auf {host} neu aus dem aktuellen Zustand angelegt.", preset=preset, host=host)

            return jsonify({"ok": True, "msg": msg, "host": host, "ip": ip, "preset": preset, "name": preset_name})

        default_payload = {
            "on": True,
            "seg": [{
                "id": 0,
                "col": [[255, 160, 0], [0, 0, 0], [0, 0, 0]],
                "fx": 0,
                "sx": 128,
                "ix": 128,
                "pal": 0
            }],
            "psave": preset,
            "n": preset_name,
            "ib": False,
            "sb": True
        }
        _wled_json_post(ip, default_payload, "/json/state", timeout_s=1.8)
        return jsonify({
            "ok": True,
            "msg": t("wled.preset_created_default_light", "Preset {preset} auf {host} neu mit Standard-Licht angelegt.", preset=preset, host=host),
            "host": host,
            "ip": ip,
            "preset": preset,
            "name": preset_name,
            "used_default": True
        })
    except Exception as e:
        return jsonify({"ok": False, "msg": t("wled.send_failed", "WLED Senden fehlgeschlagen: {error}", error=e)}), 500
    finally:
        if mutation_key is not None and mutation_lock is not None:
            _wled_end_mutation(mutation_key, mutation_lock)


@app.route("/api/wled-presets/delete", methods=["POST"])
def api_wled_presets_delete():
    data = request.get_json(silent=True) or {}
    try:
        preset = int(data.get("preset") or 0)
    except Exception:
        preset = 0
    try:
        slot = int(data.get("slot") or 0)
    except Exception:
        slot = 0
    host_fallback = str(data.get("host") or "").strip()

    if preset < 1:
        return jsonify({"ok": False, "msg": t("wled.invalid_preset_number", "Ungültige Preset-Nummer.")}), 400

    ip, host, err = _resolve_wled_target_for_slot_or_host(slot, host_fallback)
    if err:
        return jsonify({"ok": False, "msg": err}), 400

    mutation_key = None
    mutation_lock = None
    try:
        mutation_key, mutation_lock = _wled_begin_mutation(ip)
        _wled_json_post(ip, {"pdel": preset}, "/json/state", timeout_s=1.8)
        return jsonify({
            "ok": True,
            "msg": t("wled.preset_deleted_remote", "Preset {preset} wurde auf {host} gelöscht.", preset=preset, host=host),
            "host": host,
            "ip": ip,
            "preset": preset,
        })
    except Exception as e:
        return jsonify({"ok": False, "msg": t("wled.delete_failed", "WLED Löschen fehlgeschlagen: {error}", error=e)}), 500
    finally:
        if mutation_key is not None and mutation_lock is not None:
            _wled_end_mutation(mutation_key, mutation_lock)


@app.route("/api/wled-presets/load", methods=["GET"])
def api_wled_presets_load():
    ok, msg, rows, weps_text = load_wled_presets_state()
    status = 200 if ok else 400
    return jsonify({
        "ok": ok,
        "msg": msg,
        "rows": rows,
        "wepsText": weps_text,
        "path": DARTS_WLED_START_CUSTOM,
    }), status


@app.route("/api/wled-presets/save", methods=["POST"])
def api_wled_presets_save():
    data = request.get_json(silent=True) or {}
    rows = data.get("rows", [])
    ok, msg, saved_rows, weps_text = save_wled_presets_state(rows)
    status = 200 if ok else 400
    return jsonify({
        "ok": ok,
        "msg": msg,
        "rows": saved_rows,
        "wepsText": weps_text,
        "path": DARTS_WLED_START_CUSTOM,
    }), status


_WLED_DEFAULT_PRESET_ROWS = [
    {"kind": "fixed",       "arg": "-IDE",    "typeId": "player1",          "label": "Spieler 1 / Player 1",                 "duration": False, "preset": 1,  "seconds": ""},
    {"kind": "fixed",       "arg": "-IDE2",   "typeId": "player2",          "label": "Spieler 2 / Player 2",                 "duration": False, "preset": 2,  "seconds": ""},
    {"kind": "fixed",       "arg": "-IDE3",   "typeId": "player3",          "label": "Spieler 3 / Player 3",                 "duration": False, "preset": 3,  "seconds": ""},
    {"kind": "fixed",       "arg": "-IDE4",   "typeId": "player4",          "label": "Spieler 4 / Player 4",                 "duration": False, "preset": 4,  "seconds": ""},
    {"kind": "fixed",       "arg": "-IDE5",   "typeId": "player5",          "label": "Spieler 5 / Player 5",                 "duration": False, "preset": 5,  "seconds": ""},
    {"kind": "fixed",       "arg": "-IDE6",   "typeId": "player6",          "label": "Spieler 6 / Player 6",                 "duration": False, "preset": 6,  "seconds": ""},
    {"kind": "fixed",       "arg": "-G",      "typeId": "leg",              "label": "Leg gewonnen / Game won",              "duration": True,  "preset": 7,  "seconds": "4"},
    {"kind": "fixed",       "arg": "-M",      "typeId": "match",            "label": "Match gewonnen / Match won",           "duration": True,  "preset": 8,  "seconds": "5"},
    {"kind": "fixed",       "arg": "-B",      "typeId": "busted",           "label": "Busted / Überworfen",                  "duration": True,  "preset": 9,  "seconds": "3"},
    {"kind": "score_exact", "typeId": "score_exact", "label": "180! / Score 180", "score": 180,                              "duration": True,  "preset": 10, "seconds": "5"},
    {"kind": "fixed",       "arg": "-PJ",     "typeId": "player_joined",    "label": "Spieler beigetreten / Player joined",  "duration": True,  "preset": 11, "seconds": "3"},
    {"kind": "fixed",       "arg": "-PL",     "typeId": "player_left",      "label": "Spieler verlassen / Player left",      "duration": True,  "preset": 12, "seconds": "3"},
    {"kind": "fixed",       "arg": "-BSE",    "typeId": "board_stop_effect","label": "Board gestoppt / Board stopped",       "duration": True,  "preset": 13, "seconds": ""},
    {"kind": "fixed",       "arg": "-CE",     "typeId": "calibration",      "label": "Kalibrierung / Calibration",           "duration": True,  "preset": 14, "seconds": ""},
    {"kind": "fixed",       "arg": "-TOE",    "typeId": "checkout",         "label": "Checkout / Takeout",                   "duration": True,  "preset": 15, "seconds": "3"},
    {"kind": "fixed",       "arg": "-HF",     "typeId": "high_finish",      "label": "High Finish",                          "duration": True,  "preset": 16, "seconds": "4"},
    {"kind": "fixed",       "arg": "-DSBULL", "typeId": "bull",             "label": "Bull / Bullseye",                      "duration": True,  "preset": 17, "seconds": "3"},
    {"kind": "fixed",       "arg": "-SLE",    "typeId": "sleep_effect",     "label": "Sleep-Effekt / Sleep effect",          "duration": True,  "preset": 18, "seconds": ""},
    {"kind": "option_bool", "arg": "-OFF",    "typeId": "wled_off",         "label": "Nach Match-Ende ausschalten",          "value": "1"},
    {"kind": "option_bool", "arg": "-SOFF",   "typeId": "wled_off_at_start","label": "Beim Verbinden ausschalten",           "value": "1"},
    {"kind": "option_int",  "arg": "-BRI",    "typeId": "brightness",       "label": "Effekt-Helligkeit",                    "value": "200"},
    {"kind": "option_int",  "arg": "-HFO",    "typeId": "high_finish_on",   "label": "High-Finish ab Score",                 "value": "100"},
    {"kind": "option_int",  "arg": "-SLET",   "typeId": "sleep_timeout",    "label": "Sleep nach Sekunden",                  "value": "10"},
]

_WLED_DEFAULT_PRESETS_TEMPLATE = Path(__file__).parent / "configs" / "wled" / "presets_default.json"


def _wled_replace_led_count(obj, led_count: int):
    if isinstance(obj, dict):
        return {k: (led_count if k == "stop" and v == 9999 else _wled_replace_led_count(v, led_count)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_wled_replace_led_count(item, led_count) for item in obj]
    return obj


@app.route("/api/wled-presets/upload-defaults", methods=["POST"])
def api_wled_presets_upload_defaults():
    data = request.get_json(silent=True) or {}
    try:
        led_count = max(1, min(int(data.get("led_count") or 145), 9000))
    except Exception:
        led_count = 145
    try:
        slot = int(data.get("slot") or 0)
    except Exception:
        slot = 0
    host_fallback = str(data.get("host") or "").strip()

    ip, host, err = _resolve_wled_target_for_slot_or_host(slot, host_fallback)
    if err:
        return jsonify({"ok": False, "msg": err}), 400

    if not _WLED_DEFAULT_PRESETS_TEMPLATE.exists():
        return jsonify({"ok": False, "msg": "presets_default.json nicht gefunden."}), 500

    try:
        raw = _WLED_DEFAULT_PRESETS_TEMPLATE.read_text(encoding="utf-8")
        preset_data = json.loads(raw)
        preset_data = _wled_replace_led_count(preset_data, led_count)
        preset_json = json.dumps(preset_data, separators=(",", ":"))
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Template-Fehler: {e}"}), 500

    try:
        import requests as _req
        upload_url = f"http://{ip}/upload"
        files = {"file": ("/presets.json", preset_json.encode("utf-8"), "application/json")}
        resp = _req.post(upload_url, files=files, timeout=12)
        if not resp.ok:
            return jsonify({"ok": False, "msg": f"Upload fehlgeschlagen (HTTP {resp.status_code}): {resp.text[:200]}"}), 500
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Upload-Fehler: {e}"}), 500

    try:
        _wled_json_post(ip, {"rb": True}, "/json", timeout_s=3.0)
    except Exception:
        pass  # Reboot trennt Verbindung – das ist erwartet

    sh_ok, sh_msg, saved_rows, weps_text = save_wled_presets_state(_WLED_DEFAULT_PRESET_ROWS)

    return jsonify({
        "ok": True,
        "msg": t("wled.defaults_uploaded",
                 "18 Standard-Presets auf {host} geladen ({led} LEDs). WLED startet neu.",
                 host=host, led=led_count),
        "led_count": led_count,
        "host": host,
        "ip": ip,
        "sh_ok": sh_ok,
        "sh_msg": sh_msg,
        "rows": saved_rows,
        "wepsText": weps_text,
    })


def _wled_resolve_simple(slot: int, fallback_host: str):
    """Returns (host_str, ip_str_or_None) for brightness-only requests."""
    cfg = load_wled_config()
    host = (fallback_host or "").strip()
    if slot and slot > 0:
        targets = cfg.get("targets", []) or []
        if 1 <= slot <= len(targets):
            target_cfg = targets[slot - 1] or {}
            cfg_host = str(target_cfg.get("host", "")).strip()
            if cfg_host:
                host = cfg_host
    if not host:
        return t("wled.no_host_found", "Kein WLED Host gefunden."), None
    ip = resolve_host_to_ip_fast(host, timeout_s=0.8)
    if not ip:
        return t("wled.host_could_not_resolve", "{host} konnte nicht aufgelöst werden.", host=host), None
    return host, ip


@app.route("/api/wled/brightness", methods=["GET"])
def api_wled_brightness_get():
    slot = request.args.get("slot", 0, type=int)
    host_fallback = request.args.get("host", "").strip()
    host, ip = _wled_resolve_simple(slot, host_fallback)
    if not ip:
        return jsonify({"ok": False, "msg": host}), 503
    try:
        state_data = _wled_json_get(ip, "/json/state", timeout_s=2.5)
        on = bool(state_data.get("on", False))
        bri = int(state_data.get("bri", 0))
        percent = 0 if not on else round(bri * 100 / 255)
        return jsonify({"ok": True, "percent": percent, "bri": bri, "on": on, "host": host})
    except Exception as e:
        return jsonify({"ok": False, "msg": f"WLED nicht erreichbar: {e}"}), 503


@app.route("/api/wled/brightness", methods=["POST"])
def api_wled_brightness_set():
    data = request.get_json(silent=True) or {}
    try:
        percent = int(data.get("brightness", -1))
    except Exception:
        return jsonify({"ok": False, "msg": "Ungültiger Wert."}), 400
    if not 0 <= percent <= 100:
        return jsonify({"ok": False, "msg": "Helligkeit muss zwischen 0 und 100 liegen."}), 400
    slot = int(data.get("slot", 0) or 0)
    host_fallback = str(data.get("host", "") or "").strip()
    host, ip = _wled_resolve_simple(slot, host_fallback)
    if not ip:
        return jsonify({"ok": False, "msg": host}), 503
    try:
        payload = {"on": False} if percent == 0 else {"on": True, "bri": max(1, round(percent * 255 / 100))}
        _wled_json_post(ip, payload, "/json/state", timeout_s=2.5)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"WLED Fehler: {e}"}), 503

    # Sync -BRI in start-custom.sh so darts-wled uses the same value after restart
    bri_synced = False
    if percent > 0:
        try:
            ok_load, _, rows, _ = load_wled_presets_state()
            if ok_load and rows:
                bri_val = str(max(1, round(percent * 255 / 100)))
                for row in rows:
                    if row.get("typeId") == "brightness" and row.get("kind") == "option_int":
                        row["value"] = bri_val
                        bri_synced = True
                        break
                if bri_synced:
                    save_wled_presets_state(rows)
        except Exception:
            pass

    return jsonify({"ok": True, "percent": percent, "host": host, "bri_synced": bri_synced})


@app.route("/wled-open", methods=["GET"])
def wled_open():
    # Slot 1 öffnen
    return redirect(url_for("wled_open_slot", slot=1))


@app.route("/wled/open/<int:slot>", methods=["GET"])
def wled_open_slot(slot: int):
    cfg = load_wled_config()
    if not bool(cfg.get("master_enabled", True)):
        return _inline_notice_page(
            t("wled.disabled_title", "WLED deaktiviert"),
            (
                f"<p>{t('wled.disabled_text', 'WLED wurde in der Weboberfläche deaktiviert.')}</p>"
                f"<p><a style='color:#3b82f6' href='{url_for('index')}'>{t('generic.back', 'Zurück')}</a></p>"
            ),
            status=200,
        )

    targets = cfg.get("targets", [])
    if slot < 1 or slot > len(targets):
        return _inline_notice_page(
            t("wled.invalid_slot_title", "Ungültiger WLED Slot"),
            (
                f"<p>{t('wled.slot_does_not_exist', 'Slot {slot} existiert nicht.', slot=slot)}</p>"
                f"<p><a style='color:#3b82f6' href='{url_for('index')}'>{t('generic.back', 'Zurück')}</a></p>"
            ),
            status=404,
        )

    host = str(targets[slot - 1].get("host", "")).strip()
    slot_enabled = bool(targets[slot - 1].get("enabled", False))
    if not slot_enabled:
        return _inline_notice_page(
            t("wled.slot_disabled_title", "WLED Slot deaktiviert"),
            (
                f"<p>{t('wled.slot_currently_disabled', 'Slot {slot} ist aktuell nicht aktiviert.', slot=slot)}</p>"
                f"<p><a style='color:#3b82f6' href='{url_for('index')}'>{t('generic.back', 'Zurück')}</a></p>"
            ),
            status=200,
        )

    if not host:
        return _inline_notice_page(
            t("wled.no_host_title", "Kein WLED eingetragen"),
            (
                f"<p>{t('wled.no_host_for_slot', 'Für Slot {slot} wurde noch kein Hostname/IP eingetragen.', slot=slot)}</p>"
                f"<p><a style='color:#3b82f6' href='{url_for('index')}'>{t('generic.back', 'Zurück')}</a></p>"
            ),
            status=400,
        )

    ok, ip = is_http_reachable(host, timeout_s=0.8)
    if not ok:
        return _inline_notice_page(
            t("wled.unreachable_title", "WLED nicht erreichbar"),
            (
                f"<p>{t('wled.unreachable_text', 'Sie haben kein offizielles LED Band mit Controller im Einsatz, oder der Controller ist aktuell nicht verbunden.')}</p>"
                f"<p>{t('wled.host_label', 'Host')}: <code>{host}</code></p>"
                f"<p><a style='color:#3b82f6' href='{url_for('index')}'>{t('generic.back', 'Zurück')}</a></p>"
            ),
            status=503,
        )

    target = ip or host
    return redirect(f"http://{target}/")


@app.route("/wled/save-targets", methods=["POST"])
def wled_save_targets():
    cfg = load_wled_config()
    targets = cfg.get("targets", [])
    if not isinstance(targets, list):
        targets = []

    # Ensure 3 targets
    while len(targets) < 3:
        targets.append({"label": f"Dart LED{len(targets)+1}", "host": f"Dart-Led{len(targets)+1}.local", "enabled": False})
    targets = targets[:3]

    for i in range(1, 4):
        label = request.form.get(f"wled_label_{i}", f"Dart LED{i}").strip()[:40]
        host_raw = request.form.get(f"wled_host_{i}")
        if host_raw is None:
            host = str(targets[i - 1].get("host", "")).strip()
        else:
            host = host_raw.strip()
        enabled = request.form.get(f"wled_enabled_{i}") == "1"

        targets[i - 1]["label"] = label if label else f"Dart LED{i}"
        targets[i - 1]["host"] = host
        targets[i - 1]["enabled"] = bool(enabled)

    cfg["targets"] = targets
    save_wled_config(cfg)

    # Service handling + -WEPS Update
    msg_parts = [t("wled.targets_saved", "WLED Targets gespeichert.")]
    ok = True

    ok_apply, msg_apply = apply_wled_services_from_config(cfg, restart=True)
    msg_parts.append(msg_apply)
    ok = ok and ok_apply

    return redirect(url_for("index", ledcheck=("ok" if ok else "bad"), ledmsg="\n".join(msg_parts)))


@app.route("/wled/toggle", methods=["POST"])
def wled_toggle():
    cfg = load_wled_config()
    new_master = not bool(cfg.get("master_enabled", True))
    cfg["master_enabled"] = new_master
    save_wled_config(cfg)
    save_wled_flag(new_master)  # auch legacy flag

    ok = True
    msg_parts = []

    # Service handling
    msg_parts.append(t("wled.toggle_enabled", "WLED aktiviert (merkt sich das nach Neustart).") if new_master else t("wled.toggle_disabled_service_stopped", "WLED deaktiviert (merkt sich das nach Neustart). darts-wled wurde gestoppt."))
    ok_apply, msg_apply = apply_wled_services_from_config(cfg, restart=True)
    msg_parts.append(msg_apply)
    ok = ok and ok_apply

    return redirect(url_for("index", ledcheck=("ok" if ok else "bad"), ledmsg="\n".join(msg_parts)))




@app.route("/wled/set-enabled/<int:slot>", methods=["POST"])
def wled_set_enabled(slot: int):
    # User-UI: Slot 1..3
    if slot < 1 or slot > 3:
        return jsonify({"ok": False, "msg": t("generic.invalid_slot", "Ungültiger Slot.")}), 400

    enabled = request.form.get("enabled") == "1"
    cfg = load_wled_config()
    cfg["master_enabled"] = True  # User-UI hat keinen Master-Schalter

    targets = cfg.get("targets", []) or []
    while len(targets) < 3:
        targets.append({"label": f"Dart LED{len(targets)+1}", "host": "", "enabled": False})
    targets = targets[:3]

    targets[slot - 1]["enabled"] = bool(enabled)
    cfg["targets"] = targets
    save_wled_config(cfg)

    # Service handling
    ok_apply, msg_apply = apply_wled_services_from_config(cfg, restart=False)

    return jsonify({"ok": ok_apply, "msg": msg_apply})


@app.route("/wled/save-enabled", methods=["POST"])
def wled_save_enabled():
    cfg = load_wled_config()
    cfg["master_enabled"] = True  # User-UI hat keinen Master-Schalter

    targets = cfg.get("targets", []) or []
    while len(targets) < 3:
        targets.append({"label": f"Dart LED{len(targets)+1}", "host": "", "enabled": False})
    targets = targets[:3]

    for i in range(1, 4):
        enabled = request.form.get(f"wled_enabled_{i}") == "1"
        targets[i - 1]["enabled"] = bool(enabled)

    cfg["targets"] = targets
    save_wled_config(cfg)

    # Service handling: nur wenn installiert/exists
    apply_wled_services_from_config(cfg, restart=False)

    return redirect(url_for("index"))


@app.route("/wled/save-hosts", methods=["POST"])
def wled_save_hosts():
    if not bool(session.get("admin_unlocked", False)):
        return redirect(url_for("index", adminerr="1") + "#admin_details")

    cfg = load_wled_config()
    cfg["master_enabled"] = True  # User-UI hat keinen Master-Schalter

    targets = cfg.get("targets", []) or []
    while len(targets) < 3:
        targets.append({"label": f"Dart LED{len(targets)+1}", "host": "", "enabled": False})
    targets = targets[:3]

    for i in range(1, 4):
        host = (request.form.get(f"wled_host_{i}", "") or "").strip()
        if host:
            targets[i - 1]["host"] = host

    cfg["targets"] = targets
    save_wled_config(cfg)

    # Falls aktuell aktiv -> -WEPS updaten + service neu starten
    apply_wled_services_from_config(cfg, restart=False)

    return redirect(url_for("index"))


def _wled_check_one(host: str) -> tuple[bool, str | None]:
    """
    Schneller WLED-Check ohne DNS-Blocker:
    - löst Host via avahi/getent mit Timeout auf
    - prüft dann http://<ip>/json/info
    """
    host = (host or "").strip()
    if not host:
        return False, None

    # Cache über Host (für schnelle Reloads / mehrere Tabs)
    now = time.time()
    cached = WLED_STATUS_CACHE.get(host)
    if cached and (now - cached[0]) < WLED_STATUS_CACHE_TTL_SEC:
        d = cached[1]
        return bool(d.get("online", False)), d.get("ip")

    ip = resolve_host_to_ip_fast(host, timeout_s=0.6)
    if not ip:
        WLED_STATUS_CACHE[host] = (now, {"online": False, "ip": None})
        return False, None

    ok = False
    try:
        url = f"http://{ip}/json/info"
        req = urllib.request.Request(url, headers={"User-Agent": "AutodartsPanel"})
        with urllib.request.urlopen(req, timeout=0.6) as r:
            status = getattr(r, "status", 200)
            data = r.read(32)
            ok = (200 <= status < 300) and bool(data)
    except Exception:
        ok = False

    WLED_STATUS_CACHE[host] = (now, {"online": ok, "ip": ip})
    return ok, ip





@app.route("/api/caller-wled/led-restart", methods=["POST"])
def api_caller_wled_led_restart():
    """Startet nur den LED-Teil sauber neu: darts-wled stoppen, Caller/WLED-Anmeldung prüfen, darts-wled starten.

    Wichtig: Der Caller selbst wird absichtlich NICHT neu gestartet, damit ein laufendes Spiel/verbundene Freunde
    nicht getrennt werden.
    """
    cfg = load_wled_config()
    if not bool(cfg.get("master_enabled", True)):
        return jsonify({"ok": False, "msg": t("caller_wled.restart_master_off", "Caller/WLED ist global deaktiviert. Bitte zuerst Caller/WLED aktivieren.")}), 400

    hosts = get_enabled_wled_hosts(cfg)
    if not hosts:
        return jsonify({"ok": False, "msg": t("caller_wled.restart_no_target", "Kein aktives WLED/LED-Band gefunden. Bitte zuerst ein LED-Band aktivieren.")}), 400

    auth = caller_wled_connection_status()
    if not auth.get("caller_wled_auth_active"):
        return jsonify({
            "ok": False,
            "msg": t("caller_wled.restart_auth_missing", "Caller/WLED Anmeldung ist nicht aktiv. Bitte zuerst auf Port 8079 anmelden bzw. Start prüfen."),
            "status": auth,
        }), 400

    if not service_exists(DARTS_WLED_SERVICE):
        return jsonify({"ok": False, "msg": t("caller_wled.restart_wled_missing", "darts-wled.service wurde nicht gefunden.")}), 404

    ok_weps, msg_weps = update_darts_wled_start_custom_weps(hosts)
    if not ok_weps:
        return jsonify({"ok": False, "msg": msg_weps, "status": auth}), 400

    try:
        service_stop(DARTS_WLED_SERVICE)
        time.sleep(2)
        wled_watchdog_enable_now()
        service_enable(DARTS_WLED_SERVICE)
        service_restart(DARTS_WLED_SERVICE)
        time.sleep(2)
    except Exception as e:
        return jsonify({"ok": False, "msg": t("caller_wled.restart_failed", "LED-Neustart fehlgeschlagen: {error}", error=e), "status": auth}), 500

    status = caller_wled_connection_status()
    status.update({
        "wled_service_active": service_is_active(DARTS_WLED_SERVICE) if service_exists(DARTS_WLED_SERVICE) else None,
        "wled_service_enabled": service_is_enabled(DARTS_WLED_SERVICE) if service_exists(DARTS_WLED_SERVICE) else None,
        "watchdog_timer_exists": wled_watchdog_timer_exists(),
        "watchdog_timer_active": service_is_active(DARTS_WLED_WATCHDOG_TIMER) if wled_watchdog_timer_exists() else None,
        "watchdog_timer_enabled": service_is_enabled(DARTS_WLED_WATCHDOG_TIMER) if wled_watchdog_timer_exists() else None,
    })
    return jsonify({
        "ok": True,
        "msg": t("caller_wled.restart_ok", "LED wurde neu gestartet. Caller/WLED Anmeldung blieb aktiv."),
        "detail": msg_weps,
        "status": status,
    })


@app.route("/api/wled/status", methods=["GET"])
def api_wled_status():
    cfg = load_wled_config()
    targets = cfg.get("targets", []) or []
    while len(targets) < 3:
        targets.append({"label": f"Dart LED{len(targets)+1}", "host": "", "enabled": False})
    targets = targets[:3]

    bands = []
    work = []
    for i, t in enumerate(targets, start=1):
        enabled = bool(t.get("enabled", False))
        host = str(t.get("host", "")).strip()
        bands.append({"slot": i, "enabled": enabled, "online": None, "ip": None})
        if enabled and host:
            work.append((i, host))

    # Parallel (3 Stück max) -> schneller
    if work:
        with ThreadPoolExecutor(max_workers=min(3, len(work))) as ex:
            futures = {ex.submit(_wled_check_one, host): slot for slot, host in work}
            for fut, slot in futures.items():
                try:
                    ok, ip = fut.result(timeout=1.2)
                except Exception:
                    ok, ip = (False, None)
                bands[slot - 1]["online"] = bool(ok)
                bands[slot - 1]["ip"] = ip

        # enabled, aber kein host -> online bleibt None (wird als "Prüfe…" angezeigt)
    caller_wled_status = caller_wled_connection_status()
    return jsonify({
        "bands": bands,
        **caller_wled_status,
        "wled_service_active": service_is_active(DARTS_WLED_SERVICE) if service_exists(DARTS_WLED_SERVICE) else None,
        "wled_service_enabled": service_is_enabled(DARTS_WLED_SERVICE) if service_exists(DARTS_WLED_SERVICE) else None,
        "watchdog_timer_exists": wled_watchdog_timer_exists(),
        "watchdog_timer_active": service_is_active(DARTS_WLED_WATCHDOG_TIMER) if wled_watchdog_timer_exists() else None,
        "watchdog_timer_enabled": service_is_enabled(DARTS_WLED_WATCHDOG_TIMER) if wled_watchdog_timer_exists() else None,
    })


# === Pi Monitor Test API (Admin) ===
@app.route("/api/pi_monitor/status", methods=["GET"])
def api_pi_monitor_status():
    if not bool(session.get("admin_unlocked", False)):
        return jsonify({"ok": False, "msg": t("admin.locked", "Admin gesperrt.")}), 403
    st = get_pi_monitor_status()
    st["ok"] = True
    return jsonify(st)


@app.route("/api/pi_monitor/start", methods=["POST"])
def api_pi_monitor_start():
    if not bool(session.get("admin_unlocked", False)):
        return jsonify({"ok": False, "msg": t("admin.locked", "Admin gesperrt.")}), 403
    data = request.get_json(silent=True) or {}
    try:
        interval_s = int(data.get("interval_s") or 10)
        duration_min = int(data.get("duration_min") or 30)
    except Exception:
        interval_s, duration_min = 10, 30
    res = start_pi_monitor(interval_s=interval_s, duration_min=duration_min)
    if not res.get("ok"):
        return jsonify(res), 400
    return jsonify(res)


@app.route("/api/pi_monitor/stop", methods=["POST"])
def api_pi_monitor_stop():
    if not bool(session.get("admin_unlocked", False)):
        return jsonify({"ok": False, "msg": t("admin.locked", "Admin gesperrt.")}), 403
    res = stop_pi_monitor()
    if not res.get("ok"):
        return jsonify(res), 400
    return jsonify(res)

@app.route("/api/system/stats", methods=["GET"])
def api_system_stats():
    cpu_pct, mem_used, mem_total, temp_c = get_system_stats()
    cpu_pct_actual = get_cpu_actual_pct()   # blockiert ~300 ms
    return jsonify({
        "cpu_pct":        cpu_pct,
        "cpu_pct_actual": cpu_pct_actual,
        "mem_used":       mem_used,
        "mem_total":      mem_total,
        "temp_c":         temp_c,
    })


@app.route("/admin/unlock", methods=["POST"])
def admin_unlock():
    pw = (request.form.get("admin_password") or "").strip()
    if pw == ADMIN_PASSWORD:
        session["admin_unlocked"] = True
        return redirect(url_for("index", admin="1") + "#admin_details")
    session.pop("admin_unlocked", None)
    return redirect(url_for("index", adminerr="1") + "#admin_details")


@app.route("/admin/lock", methods=["POST"])
def admin_lock():
    session.pop("admin_unlocked", None)
    return redirect(url_for("index") + "#admin_details")



@app.route("/admin/reboot", methods=["POST"])
def admin_reboot():
    # Nur im Admin-Modus erlauben
    #if not bool(session.get("admin_unlocked", False)):
        #return _forbidden_response()

    unit_name = f"autodarts-reboot-{int(time.time())}"

    # Im Hintergrund rebooten (damit die HTTP-Response noch rausgeht)
    try:
        subprocess.Popen(
            ["sudo", "-n", "systemd-run", "--unit", unit_name, "--no-block", "--collect", "/sbin/reboot"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        # Fallback (falls systemd-run nicht geht)
        try:
            subprocess.Popen(
                ["sudo", "-n", "/sbin/reboot"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    return _inline_notice_page(
        t("admin.reboot_title", "Reboot"),
        (
            f"<h2>{t('admin.reboot_running', 'Neustart wird ausgeführt…')}</h2>"
            f"<p>{t('admin.reboot_running_text', 'Der Raspberry Pi startet jetzt neu. Diese Seite ist gleich nicht mehr erreichbar.')}</p>"
        ),
        html_lang=_get_current_lang_code(),
    )[0]






@app.route("/admin/shutdown", methods=["POST"])
def admin_shutdown():
    # Nur im Admin-Modus erlauben
    #if not bool(session.get("admin_unlocked", False)):
        #return _forbidden_response()

    unit_name = f"autodarts-shutdown-{int(time.time())}"

    try:
        subprocess.Popen(
            ["sudo", "-n", "systemd-run", "--unit", unit_name, "--no-block", "--collect", "/sbin/shutdown", "-h", "now"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        try:
            subprocess.Popen(
                ["sudo", "-n", "/sbin/shutdown", "-h", "now"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    return _inline_notice_page(
        t("admin.shutdown_title", "Herunterfahren"),
        (
            f"<h2>{t('admin.shutdown_running', 'Herunterfahren wird ausgeführt…')}</h2>"
            f"<p>{t('admin.shutdown_running_text', 'Der Raspberry Pi fährt jetzt sauber herunter. Diese Seite ist gleich nicht mehr erreichbar.')}</p>"
        ),
        html_lang=_get_current_lang_code(),
    )[0]


@app.route("/admin/pi-monitor/download/<kind>", methods=["GET"])
def admin_pi_monitor_download(kind: str):
    if not bool(session.get("admin_unlocked", False)):
        return _forbidden_response()

    kind = (kind or "").strip().lower()
    mapping = {
        "csv": (PI_MONITOR_CSV, "pi_monitor_test.csv", "text/csv; charset=utf-8"),
        "readme": (PI_MONITOR_README, "pi_monitor_test_README.txt", "text/plain; charset=utf-8"),
        "outlog": (PI_MONITOR_OUTLOG, "pi_monitor_test.out", "text/plain; charset=utf-8"),
    }
    entry = mapping.get(kind)
    if not entry:
        return (t("downloads.unknown_download", "Unbekannter Download."), 404)

    path, download_name, mimetype = entry
    if not os.path.exists(path):
        return (t("downloads.file_not_found", "Datei nicht gefunden: {path}", path=path), 404)

    return send_file(path, mimetype=mimetype, as_attachment=True, download_name=download_name)


@app.route("/admin/pi-monitor/tail", methods=["GET"])
def admin_pi_monitor_tail():
    if not bool(session.get("admin_unlocked", False)):
        return _forbidden_response()

    source = (request.args.get("source") or "csv").strip().lower()
    if source == "outlog":
        path = PI_MONITOR_OUTLOG
        filename = "pi_monitor_test_last_2000_outlog_lines.txt"
    else:
        path = PI_MONITOR_CSV
        filename = "pi_monitor_test_last_2000_csv_lines.txt"

    try:
        n = int(request.args.get("n") or 2000)
    except Exception:
        n = 2000
    n = max(1, min(5000, n))

    if not os.path.exists(path):
        return Response(t("downloads.file_not_found", "Datei nicht gefunden: {path}", path=path) + "\n", status=404, mimetype="text/plain")

    text = tail_file(path, n=n, max_chars=400000)
    if not text:
        text = ""

    resp = Response(text, mimetype="text/plain")
    if request.args.get("download") == "1":
        resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@app.route("/admin/os/update", methods=["POST"])
def admin_os_update():
    if not bool(session.get("admin_unlocked", False)):
        return _forbidden_response()

    ok, msg = start_os_update_background()
    return redirect(url_for("index", admin="1", adminok=("1" if ok else "0"), adminmsg=msg) + "#admin_details")




# ---------------- Admin: Firewall (UFW) ----------------

@app.route("/admin/ufw/refresh", methods=["POST"])
def admin_ufw_refresh():
    if not bool(session.get("admin_unlocked", False)):
        return _forbidden_response()
    ufw_refresh_state()
    return redirect(url_for("index", admin="1", adminmsg=t("ufw.status_refreshed", "Firewall-Status aktualisiert."), adminok="1") + "#admin_details")


@app.route("/admin/ufw/install", methods=["POST"])
def admin_ufw_install():
    if not bool(session.get("admin_unlocked", False)):
        return _forbidden_response()
    if ufw_is_installed():
        return redirect(url_for("index", admin="1", adminmsg=t("ufw.already_installed", "UFW ist bereits installiert."), adminok="1") + "#admin_details")
    ok, msg = start_ufw_install_background()
    return redirect(url_for("index", admin="1", adminmsg=msg, adminok=("1" if ok else "0")) + "#admin_details")


@app.route("/admin/ufw/apply_ports", methods=["POST"])
def admin_ufw_apply_ports():
    if not bool(session.get("admin_unlocked", False)):
        return _forbidden_response()
    ok, msg = ufw_apply_port_rules()
    # Status NICHT automatisch prüfen – wir aktualisieren nur den Cache (Button ist explizit)
    ufw_refresh_state()
    short = (msg.splitlines()[0] if msg else (t("generic.ok", "OK") if ok else t("generic.error", "Fehler")))
    return redirect(url_for("index", admin="1", adminmsg=short, adminok=("1" if ok else "0")) + "#admin_details")


@app.route("/admin/ufw/enable", methods=["POST"])
def admin_ufw_enable():
    if not bool(session.get("admin_unlocked", False)):
        return _forbidden_response()
    ok, msg = ufw_set_enabled(True)
    ufw_refresh_state()
    short = (msg.splitlines()[0] if msg else (t("generic.ok", "OK") if ok else t("generic.error", "Fehler")))
    return redirect(url_for("index", admin="1", adminmsg=short, adminok=("1" if ok else "0")) + "#admin_details")


@app.route("/admin/ufw/disable", methods=["POST"])
def admin_ufw_disable():
    if not bool(session.get("admin_unlocked", False)):
        return _forbidden_response()
    ok, msg = ufw_set_enabled(False)
    ufw_refresh_state()
    short = (msg.splitlines()[0] if msg else (t("generic.ok", "OK") if ok else t("generic.error", "Fehler")))
    return redirect(url_for("index", admin="1", adminmsg=short, adminok=("1" if ok else "0")) + "#admin_details")
@app.route("/admin/webpanel/check", methods=["POST"])
def admin_webpanel_check():
    # Admin muss entsperrt sein
    #if not bool(session.get("admin_unlocked", False)):
        #return _forbidden_response()

    installed = get_webpanel_version()
    latest = fetch_latest_webpanel_version()

    save_webpanel_update_check({
        "ts": int(time.time()),
        "installed": installed,
        "latest": latest,
    })

    if latest and installed and installed == latest:
        msg = t("webpanel.no_update", "Webpanel: Kein Update verfügbar.")
        ok = "1"
    elif latest:
        msg = t("webpanel.update_available", "Webpanel: Update verfügbar ({installed} → {latest}).", installed=(installed or "unknown"), latest=latest)
        ok = "1"
    else:
        msg = t("webpanel.update_check_failed", "Webpanel: Update-Check fehlgeschlagen.")
        ok = "0"

    return redirect(url_for("index", admin="1", adminmsg=msg, adminok=ok) + "#admin_details")


@app.route("/api/webpanel/update-status", methods=["GET"])
def api_webpanel_update_status():
    installed = get_webpanel_version()
    check = load_webpanel_update_check()
    latest = str(check.get("latest") or "").strip() or None
    running = webpanel_update_is_running()
    result_code = webpanel_update_result_code()
    update_available = bool(installed and latest and installed != latest)

    # Once the new version is running, refresh the cached "installed" value too.
    # This keeps all later page renders consistent without requiring another manual check.
    if installed and latest and installed == latest and check.get("installed") != installed:
        check["installed"] = installed
        check["ts"] = int(time.time())
        save_webpanel_update_check(check)

    return jsonify({
        "ok": True,
        "installed": installed,
        "latest": latest,
        "running": running,
        "result_code": result_code,
        "update_available": update_available,
    })


# V2-CHAIN-FIX: Einmalig nach Erstinstallation von autodarts 2.0 �?? setzt Version zurück
# damit der Runner die Revisionskette (1.808→latest) nachholt. ENTFERNBAR sobald
# alle Pis mindestens einmal durch den Runner gelaufen sind.
@app.route("/api/v2-chain-fix", methods=["POST"])
def api_v2_chain_fix():
    # Reset version so the updater runner walks the revision chain from 1.800 onward.
    try:
        os.makedirs(os.path.dirname(WEBPANEL_VERSION_FILE), exist_ok=True)
        with open(WEBPANEL_VERSION_FILE, "w", encoding="utf-8") as f:
            f.write("1.800\n")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    # User clicked the button → popup was seen → set the flag so it never shows again.
    try:
        os.makedirs(os.path.dirname(V2_WELCOME_FLAG_FILE), exist_ok=True)
        open(V2_WELCOME_FLAG_FILE, "w").close()
    except Exception:
        pass
    ok, msg = start_webpanel_update_background(mode="update", allow_self_update=True)
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/webpanel/update", methods=["POST"])
def api_webpanel_update_start():
    installed = get_webpanel_version()

    # Preserve the old update route behavior: determine the target version fresh
    # at the moment the user clicks Update, rather than trusting an older cache.
    latest = fetch_latest_webpanel_version()
    if latest:
        save_webpanel_update_check({
            "ts": int(time.time()),
            "installed": installed,
            "latest": latest,
        })

    if not latest:
        return jsonify({
            "ok": False,
            "message": t("webpanel.latest_not_determined", "Webpanel: Konnte 'latest' nicht ermitteln – bitte zuerst 'Update prüfen'."),
        }), 503

    _channel = str(load_settings().get("webpanel_channel") or "stable").strip().lower()
    if _channel != "beta" and installed and installed == latest:
        return jsonify({
            "ok": True,
            "already_current": True,
            "installed": installed,
            "latest": latest,
            "message": t("webpanel.no_update", "Webpanel: Kein Update verfügbar."),
        })

    ok, err = start_webpanel_update_background()
    if not ok:
        status = 409 if webpanel_update_is_running() else 500
        return jsonify({
            "ok": False,
            "running": webpanel_update_is_running(),
            "message": t("webpanel.update_start_failed", "Webpanel-Update konnte nicht gestartet werden: {error}.", error=(err or t("generic.unknown", "unbekannt"))),
        }), status

    return jsonify({
        "ok": True,
        "running": True,
        "installed": installed,
        "latest": latest,
        "message": t("webpanel.update_started", "Webpanel-Update gestartet. Die Weboberfläche kann kurz neu starten."),
    })


@app.route("/api/webpanel/channel", methods=["POST"])
def api_webpanel_channel():
    data = request.get_json(silent=True) or {}
    channel = str(data.get("channel") or "").strip().lower()
    if channel not in ("stable", "beta"):
        return jsonify({"ok": False, "message": t("webpanel.channel_invalid", "Ungültiger Kanal. Erlaubt: stable, beta.")}), 400
    try:
        save_setting("webpanel_channel", channel)
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500
    label = t("webpanel.channel_beta", "Beta") if channel == "beta" else t("webpanel.channel_stable", "Stable")
    return jsonify({"ok": True, "channel": channel, "message": t("webpanel.channel_saved", "Update-Kanal gesetzt: {label}.", label=label)})








@app.route("/admin/webpanel/update", methods=["POST"])
def admin_webpanel_update():
    # Admin muss entsperrt sein
    #if not bool(session.get("admin_unlocked", False)):
        #return _forbidden_response()

    installed = get_webpanel_version()
    latest = fetch_latest_webpanel_version()

    # Wenn latest nicht ermittelbar: erst prüfen lassen
    if not latest:
        msg = t("webpanel.latest_not_determined", "Webpanel: Konnte 'latest' nicht ermitteln – bitte zuerst 'Update prüfen'.")
        return redirect(url_for("index", admin="1", adminmsg=msg, adminok="0") + "#admin_details")

    _channel = str(load_settings().get("webpanel_channel") or "stable").strip().lower()
    if _channel != "beta" and installed and installed == latest:
        msg = t("webpanel.no_update", "Webpanel: Kein Update verfügbar.")
        return redirect(url_for("index", admin="1", adminmsg=msg, adminok="1") + "#admin_details")

    ok, err = start_webpanel_update_background()
    if ok:
        msg = t("webpanel.update_started", "Webpanel-Update gestartet. Die Weboberfläche kann kurz neu starten.")
    else:
        msg = t("webpanel.update_start_failed", "Webpanel-Update konnte nicht gestartet werden: {error}.", error=(err or t("generic.unknown", "unbekannt")))

    return redirect(url_for("index", admin="1", adminmsg=msg, adminok=("1" if ok else "0")) + "#admin_details")



@app.route("/admin/webpanel/uvc-install", methods=["POST"])
def admin_webpanel_uvc_install():
    if not bool(session.get("admin_unlocked", False)):
        return _forbidden_response()

    info = get_uvc_backup_info()
    if (not info.get("backup_exists")) and info.get("marker_exists"):
        msg = t("webpanel.uvc_hack_not_started_backup_missing", "UVC Hack nicht gestartet: Kein Original-Backup vorhanden, aber alter UVC-Marker gefunden. Bitte Backup nach {path} kopieren.", path=info.get("backup_dir"))
        return redirect(url_for("index", admin="1", adminok="0", adminmsg=msg) + "#admin_details")

    ok, err = start_webpanel_update_background(mode="uvc-hack", allow_self_update=False)
    msg = t("webpanel.uvc_hack_started", "UVC Hack gestartet. Bitte danach kurz das Webpanel-Log prüfen.") if ok else t("webpanel.uvc_hack_start_failed", "UVC Hack konnte nicht gestartet werden: {error}.", error=(err or t("generic.unknown", "unbekannt")))
    return redirect(url_for("index", admin="1", adminok=("1" if ok else "0"), adminmsg=msg) + "#admin_details")


@app.route("/admin/webpanel/uvc-uninstall", methods=["POST"])
def admin_webpanel_uvc_uninstall():
    if not bool(session.get("admin_unlocked", False)):
        return _forbidden_response()

    info = get_uvc_backup_info()
    if not info.get("backup_exists"):
        msg = t("webpanel.uvc_uninstall_not_started_backup_missing", "UVC Hack wurde NICHT deinstalliert: Kein lokales Original-Backup vorhanden. Bitte Backup nach {path} kopieren.", path=info.get("backup_dir"))
        return redirect(url_for("index", admin="1", adminok="0", adminmsg=msg) + "#admin_details")

    ok, err = start_webpanel_update_background(mode="uvc-uninstall", allow_self_update=False)
    msg = t("webpanel.uvc_uninstall_started", "UVC Deinstallation gestartet. Der Mini PC startet danach automatisch neu.") if ok else t("webpanel.uvc_uninstall_start_failed", "UVC Deinstallation konnte nicht gestartet werden: {error}.", error=(err or t("generic.unknown", "unbekannt")))
    return redirect(url_for("index", admin="1", adminok=("1" if ok else "0"), adminmsg=msg) + "#admin_details")


@app.route("/admin/wled-update", methods=["POST"])
def admin_wled_update():
    # Admin muss entsperrt sein
    if not bool(session.get("admin_unlocked", False)):
        return _forbidden_response()

    ok, msg = start_extensions_update_background("all")
    return redirect(url_for("index", admin="1", adminok=("1" if ok else "0"), adminmsg=(msg or "")) + "#admin_details")




@app.route("/admin/gpio-image", methods=["GET"])
def admin_gpio_image():
    if not bool(session.get("admin_unlocked", False)):
        return _forbidden_response()

    if os.path.exists(ADMIN_GPIO_IMAGE):
        return send_file(ADMIN_GPIO_IMAGE, mimetype="image/jpeg")
    return _inline_notice_page(
        t("admin.gpio_image_missing_title", "GPIO Bild nicht gefunden"),
        (
            f"<p>{t('downloads.file_missing', 'Datei fehlt')}: <code>{ADMIN_GPIO_IMAGE}</code></p>"
            f"<p><a style='color:#3b82f6' href='{url_for('index')}'>{t('generic.back', 'Zurück')}</a></p>"
        ),
        status=404,
    )





















@app.route("/tools/siemens-star.pdf", methods=["GET"])
def siemens_star_pdf():
    pdf_path = SIEMENS_STAR_DIR / SIEMENS_STAR_FILENAME
    if not pdf_path.exists():
        return _inline_notice_page(
            t("camera.focus_target_missing_title", "Fokusziel-PDF nicht gefunden"),
            (
                f"<p>{t('camera.focus_target_missing_text', 'Die Datei wurde nicht gefunden unter: {path}', path=str(pdf_path))}</p>"
                f"<p><a href='{url_for('index')}'>{t('generic.back', 'Zurück')}</a></p>"
            ),
            status=404,
        )

    download = request.args.get("download", "").strip().lower() in {"1", "true", "yes", "download"}
    resp = send_from_directory(
        str(SIEMENS_STAR_DIR),
        SIEMENS_STAR_FILENAME,
        as_attachment=download,
        download_name=SIEMENS_STAR_FILENAME,
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ------------------------------
# Admin: Live Journal Viewer
# ------------------------------
@app.route("/admin/journal")
def admin_journal():
    # same admin gate as other admin endpoints
    if not session.get("admin_unlocked", False):
        return redirect(url_for("index", adminerr="1") + "#admin_details")

    return render_template("admin_journal.html")


@app.route("/admin/journal/stream")
def admin_journal_stream():
    if not session.get("admin_unlocked", False):
        return ("forbidden", 403)

    unit = request.args.get("unit", "darts-caller.service").strip()
    if unit not in ALLOWED_JOURNAL_UNITS:
        return ("invalid unit", 400)

    env = os.environ.copy()
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("LC_ALL", "C.UTF-8")

    def generate():
        cmd = ["journalctl", "-u", unit, "-f", "-o", "cat", "-n", "200"]
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        try:
            for raw in iter(p.stdout.readline, ""):
                line = redact_journal_line(raw.rstrip("\n"))
                yield f"data: {line}\n\n"
        except GeneratorExit:
            pass
        finally:
            try:
                p.terminate()
            except Exception:
                pass

    resp = Response(stream_with_context(generate()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


# ------------------------------
# Einmalige Post-Update-Migration 1.762
# ------------------------------
def _post_update_1762_once():
    """Installiert nach dem ersten Start der neuen Webpanel-Version schlanke Fallback-/Watcher-Hilfen."""
    marker = Path("/var/lib/autodarts/once-webpanel-1.762-callerwatch-fallback.done")
    log_path = Path("/var/log/autodarts_webpanel_post_update.log")
    try:
        if marker.exists():
            return
        time.sleep(8)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as logf:
            def plog(msg: str):
                logf.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
                logf.flush()

            plog("===== Webpanel 1.762 Post-Update START =====")
            if os.geteuid() != 0:
                plog("SKIP: Webpanel läuft nicht als root; Systemdateien werden nicht verändert.")
                return

            safe_updater = Path("/usr/local/bin/autodarts-safe-updater.sh")
            safe_updater.write_text('#!/usr/bin/env bash\n# BUILD: AUTODARTS-SAFE-UPDATER-20260902-01\nset -Eeuo pipefail\n\nLOG="/var/log/autodarts_safe_updater.log"\nCHANNEL="${1:-latest}"\nPLATFORM="linux"\nSERVICE="autodarts.service"\n\nmkdir -p "$(dirname "$LOG")"\nexec >>"$LOG" 2>&1\n\nlog() { echo "[$(date +\'%F %T\')] $*"; }\n\narch_norm() {\n  case "$(uname -m)" in\n    x86_64|amd64) echo "amd64" ;;\n    aarch64|arm64) echo "arm64" ;;\n    armv7l) echo "armv7l" ;;\n    *) uname -m ;;\n  esac\n}\n\ninstall_dir() {\n  local exec_start path\n  exec_start="$(systemctl show "$SERVICE" -p ExecStart --value 2>/dev/null || true)"\n  path="$(printf \'%s\\n\' "$exec_start" | sed -nE \'s/.*path=([^ ;]+).*/\\1/p\' | head -n1)"\n  if [[ -z "$path" ]]; then\n    path="$(systemctl cat "$SERVICE" 2>/dev/null | sed -nE \'s/^ExecStart=([^[:space:]]+).*/\\1/p\' | head -n1)"\n  fi\n  if [[ -n "$path" ]]; then\n    dirname "$path"\n  else\n    echo "$HOME/.local/opt/autodarts"\n  fi\n}\n\nlocal_version() {\n  local dir bin out\n  dir="$(install_dir)"\n  bin="${dir}/autodarts"\n  [[ -x "$bin" ]] || return 0\n  out="$($bin --version 2>&1 || $bin -V 2>&1 || true)"\n  printf \'%s\\n\' "$out" | grep -oE \'[0-9]+\\.[0-9]+\\.[0-9]+(-beta\\.[0-9]+)?\' | head -n1 || true\n}\n\nversion_gt() {\n  local a="${1#v}" b="${2#v}"\n  [[ -n "$a" ]] || return 1\n  [[ -n "$b" ]] || return 0\n  [[ "$a" == "$b" ]] && return 1\n  [[ "$(printf \'%s\\n%s\\n\' "$a" "$b" | sort -V | tail -n1)" == "$a" ]]\n}\n\nfetch_meta() {\n  local base="$1" arch="$2" out="$3" url\n  url="${base}/detection/${CHANNEL}/${PLATFORM}/${arch}/RELEASES.json"\n  curl -fsSL --connect-timeout 5 --max-time 15 "$url" -o "$out" || return 1\n  python3 - "$out" >/dev/null <<\'PY\'\nimport json, sys\nwith open(sys.argv[1], encoding="utf-8") as f:\n    d=json.load(f)\nassert isinstance(d.get("currentVersion"), str) and d["currentVersion"].startswith("v")\nassert isinstance(d.get("releases"), list) and d["releases"]\nPY\n}\n\npick_source() {\n  local arch tmp base\n  arch="$(arch_norm)"\n  for base in "https://get.autodarts.io" "https://get.autodarts.com"; do\n    tmp="$(mktemp)"\n    if fetch_meta "$base" "$arch" "$tmp"; then\n      echo "${base}|${tmp}|${arch}"\n      return 0\n    fi\n    rm -f "$tmp"\n  done\n  return 1\n}\n\nrun_installer() {\n  local base="$1" tmp_inst\n  tmp_inst="$(mktemp)"\n  if ! curl -fsSL --connect-timeout 5 --max-time 20 "$base" -o "$tmp_inst"; then\n    log "FEHLER: Installer nicht erreichbar: $base"\n    rm -f "$tmp_inst"\n    return 1\n  fi\n  if ! head -n 1 "$tmp_inst" | grep -q \'^#!\' || ! grep -q \'INSTALL_DIR\' "$tmp_inst"; then\n    log "FEHLER: Installer bei $base sieht ungueltig aus."\n    rm -f "$tmp_inst"\n    return 1\n  fi\n  chmod +x "$tmp_inst"\n  log "Nutze Installer: $base"\n  bash "$tmp_inst"\n  rm -f "$tmp_inst"\n}\n\nlog "===== Autodarts Safe Updater START ====="\nSRC="$(pick_source)" || { log "Keine gueltige Updatequelle erreichbar (.io/.com)."; exit 0; }\nBASE="${SRC%%|*}"\nREST="${SRC#*|}"\nJSON_FILE="${REST%%|*}"\nARCH="${REST##*|}"\nLATEST="$(python3 - "$JSON_FILE" <<\'PY\'\nimport json, sys\nwith open(sys.argv[1], encoding="utf-8") as f:\n    d=json.load(f)\nprint(d["currentVersion"].lstrip("v"))\nPY\n)"\nLOCAL="$(local_version || true)"\nrm -f "$JSON_FILE"\nlog "Quelle: ${BASE}"\nlog "Arch:   ${ARCH}"\nlog "Lokal:  ${LOCAL:-unknown}"\nlog "Online: ${LATEST:-unknown}"\n\nif [[ -n "$LOCAL" ]] && ! version_gt "$LATEST" "$LOCAL"; then\n  log "Kein Boardmanager-Update noetig."\n  exit 0\nfi\n\nrun_installer "$BASE" || { log "Updateversuch fehlgeschlagen; bestehende Installation bleibt unveraendert."; exit 0; }\nlog "Autodarts Safe Updater fertig."\n', encoding="utf-8")
            os.chmod(str(safe_updater), 0o755)
            plog(f"OK: Safe-Updater installiert: {safe_updater}")

            _autoupdate_svc = "autodartsupdater.service"
            if service_exists(_autoupdate_svc) or Path(f"/etc/systemd/system/{_autoupdate_svc}").exists():
                override_dir = Path(f"/etc/systemd/system/{_autoupdate_svc}.d")
                override_dir.mkdir(parents=True, exist_ok=True)
                override_file = override_dir / "override.conf"
                override_file.write_text(f"[Service]\nExecStart=\nExecStart={safe_updater}\n", encoding="utf-8")
                os.chmod(str(override_file), 0o644)
                subprocess.run(["systemctl", "daemon-reload"], stdout=logf, stderr=logf, timeout=20)
                plog("OK: autodartsupdater.service per Drop-in auf Safe-Updater umgestellt.")
            else:
                plog("INFO: autodartsupdater.service nicht vorhanden -> kein Drop-in gesetzt.")

            flag = Path("/var/lib/autodarts/config/extensions-v2-installed.json")
            ext_script = Path("/usr/local/bin/autodarts-extensions-update.sh")
            installed = False
            try:
                installed = bool(json.loads(flag.read_text(encoding="utf-8", errors="ignore") or "{}").get("installed"))
            except Exception:
                installed = False
            if installed and ext_script.exists():
                plog("Starte Extensions-Service-Reparatur für Caller/WLED-Watchdog.")
                subprocess.run(["bash", str(ext_script), "service-repair"], stdout=logf, stderr=logf, timeout=180)
                plog("OK: Extensions-Service-Reparatur abgeschlossen.")
            else:
                plog("INFO: Caller/WLED V2 nicht installiert oder Script fehlt -> Service-Reparatur skip.")

            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({"done": True, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False), encoding="utf-8")
            plog("===== Webpanel 1.762 Post-Update OK =====")
    except Exception as e:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as logf:
                logf.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] FEHLER: {e}\n")
        except Exception:
            pass

def _start_post_update_1762_worker():
    try:
        th = threading.Thread(target=_post_update_1762_once, daemon=True)
        th.start()
    except Exception:
        pass

_start_post_update_1762_worker()


@app.route("/api/ping", methods=["GET", "OPTIONS"])
def api_ping():
    resp = jsonify({"app": "autodarts-webpanel", "ok": True})
    resp.headers["Access-Control-Allow-Origin"]          = "*"
    resp.headers["Access-Control-Allow-Private-Network"] = "true"
    resp.headers["Access-Control-Allow-Methods"]         = "GET, OPTIONS"
    if request.method == "OPTIONS":
        resp.data = b""
        resp.status_code = 204
    return resp


@app.route("/api/autodarts/autoupdate", methods=["POST"])
def api_autodarts_autoupdate_toggle():
    settings = load_settings()
    current = bool(settings.get("autodarts_auto_update", False))
    new_val = not current
    settings["autodarts_auto_update"] = new_val
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        refresh_settings_if_needed.__wrapped__() if hasattr(refresh_settings_if_needed, "__wrapped__") else None
        global SETTINGS
        SETTINGS = load_settings()
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500
    return jsonify({"ok": True, "auto_update": new_val})


@app.route("/api/autodarts/autostart", methods=["POST"])
def api_autodarts_autostart_toggle():
    user, uid = _get_ad_user_ctx()
    currently = get_autodarts_autostart_enabled()
    try:
        if not currently:
            subprocess.run(["loginctl", "enable-linger", user], capture_output=True, timeout=5)
            _run_as_ad_user(["systemctl", "--user", "enable", "autodarts.service"], timeout=10)
        else:
            _run_as_ad_user(["systemctl", "--user", "disable", "autodarts.service"], timeout=10)
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500
    enabled = get_autodarts_autostart_enabled()
    return jsonify({"ok": True, "enabled": enabled})


@app.route("/api/autodarts/restart", methods=["POST"])
def api_autodarts_restart():
    try:
        result = subprocess.run(
            ["/usr/local/bin/autodarts-userctl", "restart"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return jsonify({"ok": False, "message": result.stderr.strip()}), 500
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500
    return jsonify({"ok": True})


@app.route("/api/autodarts/update", methods=["POST"])
def api_autodarts_update_start():
    ok, msg = start_autodarts_update_background()
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)


@app.route("/api/autodarts/update-status", methods=["GET"])
def api_autodarts_update_status():
    running = autodarts_update_is_running()
    rc = autodarts_update_result_code()
    state = load_autodarts_update_state()
    version_before = state.get("version_before")

    if not running:
        try:
            AUTODARTS_VERSION_CACHE["ts"] = 0.0
            AUTODARTS_VERSION_CACHE["v"] = None
        except Exception:
            pass

    version_after = get_autodarts_version() if not running else None
    log_tail = tail_file(AUTODARTS_UPDATE_LOG, n=25, max_chars=2500) if not running else None
    success = (rc == 0) if rc is not None else None

    msg = ""
    if not running and rc is not None:
        if success:
            if version_before and version_after and version_before != version_after:
                msg = f"{version_before} → {version_after}"
            else:
                msg = f"Installierte Version: {version_after or 'unbekannt'}"
        else:
            msg = "Autodarts-Update fehlgeschlagen. Bitte Log prüfen."

    return jsonify({
        "ok": True,
        "running": running,
        "result_code": rc,
        "success": success,
        "version_before": version_before,
        "version_after": version_after,
        "message": msg,
        "log_tail": log_tail,
    })


@app.route("/api/identify", methods=["GET", "OPTIONS"])
def api_identify():
    try:
        with open("/etc/machine-id") as f:
            mid = f.read().strip()
        panel_id = "adp-" + mid[:6]
    except Exception:
        panel_id = "adp-000000"

    try:
        import socket
        name = socket.gethostname()
    except Exception:
        name = "autodarts-pi"

    resp = jsonify({
        "app": "autodarts-webpanel",
        "id": panel_id,
        "name": name,
    })
    resp.headers["Cache-Control"]                         = "no-store"
    resp.headers["Access-Control-Allow-Origin"]          = "*"
    resp.headers["Access-Control-Allow-Private-Network"] = "true"
    resp.headers["Access-Control-Allow-Methods"]         = "GET, OPTIONS"
    if request.method == "OPTIONS":
        resp.data = b""
        resp.status_code = 204
    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
