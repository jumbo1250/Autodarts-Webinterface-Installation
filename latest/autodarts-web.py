#!/usr/bin/env python3
import os
import json
import re
import socket
import subprocess
import shutil
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
import urllib.request
import urllib.error
from pathlib import Path
import time  # für weichen Dongle-Reset
import uuid
import threading

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    render_template_string,
    send_file,
    send_from_directory,
    jsonify,    session,

)

app = Flask(__name__)
app.secret_key = os.environ.get('AUTODARTS_WEB_SECRET', 'autodarts-web-admin')

# === KONFIGURATION ===

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

AUTODARTS_DATA_DIR = os.environ.get("AUTODARTS_DATA_DIR", "/home/peter/autodarts-data")
DATA_DIR = Path(AUTODARTS_DATA_DIR).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
HELP_PDF_FILENAME = "Autodarts_install_manual.pdf"
CAM_CONFIG_PATH = "/var/lib/autodarts/cam-config.json"

MAX_CAMERAS = 4
MAX_VIDEO_INDEX = 50
STREAM_BASE_PORT = 8081

WIFI_CONNECTION_NAME = "Autodarts-Net"
AUTODARTS_SERVICE = "autodarts.service"

# WLAN-Client (USB-Dongle)
WIFI_INTERFACE = "wlan0"

# Access-Point (Onboard-WLAN)
AP_CONNECTION_NAME = "Autodarts-AP"
AP_INTERFACE = "wlan_ap"

# --- LED / darts-caller / darts-wled ---
DARTS_CALLER_START_CUSTOM = "/var/lib/autodarts/extensions/darts-caller/start-custom.sh"
DARTS_CALLER_DIR = "/var/lib/autodarts/extensions/darts-caller"

DARTS_WLED_DIR = "/var/lib/autodarts/extensions/darts-wled"
DARTS_WLED_SERVICE = "darts-wled.service"

# darts-wled Config (Startscript mit Mapping/Presets) – wir ändern hier NUR die -WEPS Zeile
DARTS_WLED_START_CUSTOM = "/var/lib/autodarts/config/darts-wled/start-custom.sh"

# Multi-WLED (bis zu 3 Targets)
WLED_CONFIG_PATH = "/var/lib/autodarts/wled-targets.json"

# Legacy (alte Single-Variante)
WLED_MDNS_NAME = "Dart-Led.local"
WLED_FLAG_PATH = "/var/lib/autodarts/wled-enabled.json"

# WLED Reachability Cache (damit die Seite schnell lädt)
WLED_STATUS_CACHE: dict[str, tuple[float, dict]] = {}
WLED_STATUS_CACHE_TTL_SEC = 3.0

# --- ADMIN / DOKU ---
ADMIN_GPIO_IMAGE = "/home/peter/autodarts-data/GPIO_Setup.jpeg"

# --- Webpanel Settings (damit man Kleinigkeiten ändern kann, ohne am Script zu schrauben) ---
SETTINGS_PATH = "/var/lib/autodarts/webpanel-settings.json"
AUTODARTS_UPDATE_LOG = "/var/log/autodarts_update.log"
AUTODARTS_UPDATE_STATE = "/var/lib/autodarts/autodarts-update-state.json"
AUTODARTS_UPDATE_CHECK = "/var/lib/autodarts/autodarts-update-check.json"
AUTOUPDATE_SERVICE = "autodartsupdater.service"
PINGTEST_STATE_DIR = "/var/lib/autodarts/pingtests"

# --- Webpanel (diese Weboberfläche) Update ---
WEBPANEL_SERVICE = "autodarts-web.service"
WEBPANEL_UPDATE_SCRIPT = "/usr/local/bin/autodarts-webpanel-update.sh"
WEBPANEL_UPDATE_LOG = "/var/log/autodarts_webpanel_update.log"
WEBPANEL_UPDATE_STATE = "/var/lib/autodarts/webpanel-update-state.json"
WEBPANEL_UPDATE_CHECK = "/var/lib/autodarts/webpanel-update-check.json"

# lokale Version (bei Installation idealerweise zusammen mit autodarts-web.py nach /usr/local/bin/version.txt kopieren)
WEBPANEL_VERSION_FILE = os.environ.get("WEBPANEL_VERSION_FILE", "/usr/local/bin/version.txt")

# Remote (GitHub Raw) – kann per ENV überschrieben werden
WEBPANEL_RAW_BASE = os.environ.get(
    "WEBPANEL_RAW_BASE",
    "https://raw.githubusercontent.com/jumbo1250/Autodarts-Webinterface-Installation/main/latest",
)
WEBPANEL_VERSION_URL = os.environ.get("WEBPANEL_VERSION_URL", WEBPANEL_RAW_BASE + "/version.txt")

# Fallback falls keine lokale Version-Datei existiert
WEBPANEL_UI_FALLBACK_VERSION = "1.20"


DEFAULT_SETTINGS = {
    "admin_password": "1234",
    "ap_ssid_choices": [f"Autodartsinstall{i}" for i in range(1, 11)],
    # Wenn leer/fehlt, versuchen wir es automatisch über systemd/Dateipfade zu finden
    "autodarts_update_cmd": "",
}


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

    # Falls Datei fehlt: defaults einmalig hinschreiben (damit man sie einfach editieren kann)
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        if not os.path.exists(SETTINGS_PATH):
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2)
    except Exception:
        pass

    return merged


SETTINGS = load_settings()
ADMIN_PASSWORD = SETTINGS["admin_password"]
AP_SSID_CHOICES = SETTINGS["ap_ssid_choices"]

AUTODARTS_VERSION_CACHE = {"ts": 0.0, "v": None}
AUTODARTS_VERSION_CACHE_TTL_SEC = 10.0

# === leichte Caches für Statusdaten (reduziert subprocess-Last) ===
INDEX_STATS_CACHE = {'ts': 0.0, 'data': None}
INDEX_STATS_TTL_SEC = 2.0  # Startseite: Statuswerte max. alle 2s neu holen

WIFI_SIGNAL_CACHE = {'ts': 0.0, 'v': None}
WIFI_SIGNAL_CACHE_TTL_SEC = 5.0  # Signalstärke nur auf Knopfdruck, kurz cachen


PI_MONITOR_SCRIPT = "/usr/local/bin/pi_monitor_test.sh"
PI_MONITOR_CSV = "/var/log/pi_monitor_test.csv"
PI_MONITOR_README = "/var/log/pi_monitor_test_README.txt"

PI_MONITOR_STATE = "/tmp/autodarts_pi_monitor_state.json"
PI_MONITOR_PIDFILE = "/tmp/autodarts_pi_monitor.pid"
PI_MONITOR_OUTLOG = "/var/log/pi_monitor_test.out"

EXTENSIONS_DIR = "/var/lib/autodarts/extensions"
USR_LOCAL_BIN_DIR = "/usr/local/bin"

# =====================


# ---------------- WLAN / AP ----------------

def get_wifi_status():
    """
    Liefert (ssid, ip) für den WLAN-Dongle (WIFI_INTERFACE) oder (None, None),
    wenn er nicht mit einem WLAN verbunden ist.

    ssid = echte WLAN-SSID (Name des Routers), nicht nur der Verbindungsname.
    """
    ssid = None
    ip = None
    dev = WIFI_INTERFACE
    conn_name = None

    # 1) herausfinden, ob das Interface verbunden ist und wie die Connection heißt
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,STATE,CONNECTION", "device"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.strip().split(":", 2)
                if len(parts) >= 3 and parts[0] == dev:
                    state = parts[1]
                    conn_name = parts[2] or None
                    if state != "connected":
                        conn_name = None
                    break
    except Exception:
        conn_name = None

    if not conn_name:
        return None, None

    # 2) SSID aus der Connection auslesen
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "802-11-wireless.ssid", "connection", "show", conn_name],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("802-11-wireless.ssid:"):
                    val = line.split(":", 1)[1].strip()
                    if val:
                        ssid = val
                    break
    except Exception:
        pass

    # 3) IPv4-Adresse auslesen
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", dev],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("IP4.ADDRESS"):
                    ip_addr = line.split(":", 1)[1].strip()
                    if ip_addr:
                        ip = ip_addr.split("/", 1)[0]
                    break
    except Exception:
        pass

    return ssid, ip


def _get_default_route_interface() -> str | None:
    """Return interface used for the default route (best proxy for "home network" interface)."""
    try:
        r = subprocess.run(
            ["ip", "route", "get", "1.1.1.1"],
            capture_output=True,
            text=True,
            timeout=1.2,
        )
        if r.returncode != 0:
            return None
        # example: "1.1.1.1 via 192.168.1.1 dev wlan0 src 192.168.1.50 uid 1000"
        m = re.search(r"\bdev\s+(\S+)", r.stdout or "")
        if not m:
            return None
        dev = m.group(1).strip()
        if dev == AP_INTERFACE:
            return None
        return dev
    except Exception:
        return None


def _get_connected_wifi_interface(prefer: str | None = None) -> str | None:
    """
    Best-effort: return a connected WiFi interface (via nmcli), excluding the AP interface/profile.
    Preference order:
      1) 'prefer' if connected
      2) interface connected to WIFI_CONNECTION_NAME (e.g. Autodarts-Net)
      3) first connected WiFi excluding AP
    """
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if r.returncode != 0:
            return None

        connected: list[tuple[str, str]] = []
        for line in (r.stdout or "").splitlines():
            parts = line.split(":", 3)
            if len(parts) != 4:
                continue
            dev, typ, state, conn = [p.strip() for p in parts]
            if typ != "wifi" or state != "connected":
                continue
            # exclude AP interface / AP connection profile
            if dev == AP_INTERFACE or conn == AP_CONNECTION_NAME:
                continue
            connected.append((dev, conn))

        if not connected:
            return None

        if prefer:
            for dev, _conn in connected:
                if dev == prefer:
                    return dev

        for dev, conn in connected:
            if WIFI_CONNECTION_NAME and conn == WIFI_CONNECTION_NAME:
                return dev

        return connected[0][0]
    except Exception:
        return None

def _wifi_signal_from_proc(iface: str) -> int | None:
    """
    Very lightweight signal read via /proc/net/wireless.
    Returns percent (0..100) or None if iface not present / not wireless.
    """
    try:
        with open("/proc/net/wireless", "r", encoding="utf-8", errors="ignore") as f:
            lines = f.read().splitlines()
        for line in lines:
            line = line.strip()
            if not line.startswith(iface + ":"):
                continue
            # Example columns: iface: status link level noise ...
            # link quality is usually 0..70 in column 2
            parts = line.split()
            if len(parts) < 3:
                return None
            # parts[1] is status, parts[2] is link quality (e.g. "70.")
            q_str = parts[2].strip().rstrip(".")
            q = float(q_str)
            # Convert 0..70 to 0..100
            pct = int(round((q / 70.0) * 100.0))
            if pct < 0:
                pct = 0
            if pct > 100:
                pct = 100
            return pct
    except Exception:
        return None


def _wifi_signal_from_iw(iface: str) -> int | None:
    """Get signal strength as percent using 'iw dev <iface> link' (no scan)."""
    try:
        r = subprocess.run(
            ["iw", "dev", iface, "link"],
            capture_output=True,
            text=True,
            timeout=1.2,
        )
        if r.returncode != 0:
            return None
        out = (r.stdout or "").strip()
        if not out or "Not connected" in out:
            return None
        m = re.search(r"signal:\s*(-?\d+)\s*dBm", out)
        if not m:
            return None
        dbm = int(m.group(1))
        # Map -90..-30 dBm roughly to 0..100%
        pct = int(round((dbm + 90) * 100 / 60))
        if pct < 0:
            pct = 0
        if pct > 100:
            pct = 100
        return pct
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _wifi_signal_from_nmcli(iface: str) -> int | None:
    """Fallback: nmcli scan-less signal strength (0..100)."""
    try:
        r = subprocess.run(
            ["nmcli", "-t", "--rescan", "no", "-f", "IN-USE,SIGNAL,SSID", "device", "wifi", "list", "ifname", iface],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if r.returncode != 0:
            return None
        for line in (r.stdout or "").splitlines():
            # Format usually: *:70:MyWifi
            parts = line.split(":", 2)
            if len(parts) >= 2 and parts[0].strip() in ("*", "yes", "Yes", "YES"):
                try:
                    return int(parts[1].strip())
                except Exception:
                    return None
    except Exception:
        return None
    return None


def get_wifi_signal_percent() -> int | None:
    """
    Liefert die Signalstärke (0-100) **für die Heimnetz-Verbindung** (nicht den AP).

    Ressourcenschonend:
      1) /proc/net/wireless (kein Scan)
      2) iw dev <iface> link (kein Scan)
      3) nmcli --rescan no (Fallback)
    """
    # Ziel: Heimnetz-Interface finden (wlan0), AP (wlan_ap) ignorieren
    home_iface = None

    # 1) Wenn WIFI_INTERFACE gesetzt ist (z.B. wlan0), das bevorzugen
    if WIFI_INTERFACE and WIFI_INTERFACE != AP_INTERFACE:
        home_iface = WIFI_INTERFACE

    # 2) Falls das nicht klappt: Interface mit Default-Route (Internet)
    if not home_iface:
        home_iface = _get_default_route_interface()

    # 3) Fallback: nmcli connected wifi (AP ausgeschlossen)
    if not home_iface:
        home_iface = _get_connected_wifi_interface(prefer=WIFI_INTERFACE if WIFI_INTERFACE else None)

    candidates: list[str] = []
    if home_iface:
        candidates.append(home_iface)

    # Safety fallback: wenn oben nix gefunden, aber WIFI_INTERFACE existiert
    if WIFI_INTERFACE and WIFI_INTERFACE not in candidates and WIFI_INTERFACE != AP_INTERFACE:
        candidates.append(WIFI_INTERFACE)

    for dev in candidates:
        if not dev or dev == AP_INTERFACE:
            continue

        # 1) /proc/net/wireless
        sig = _wifi_signal_from_proc(dev)
        if sig is not None:
            return sig

        # 2) iw link
        sig = _wifi_signal_from_iw(dev)
        if sig is not None:
            return sig

        # 3) nmcli fallback
        sig = _wifi_signal_from_nmcli(dev)
        if sig is not None:
            return sig

    return None


def wifi_dongle_present() -> bool:
    """Prüft, ob der WLAN-USB-Dongle (WIFI_INTERFACE) als WiFi-Device beim NetworkManager sichtbar ist."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE", "device"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if result.returncode != 0:
            return False

        for line in result.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 2:
                dev, dev_type = parts[0], parts[1]
                if dev == WIFI_INTERFACE and dev_type == "wifi":
                    return True
        return False
    except Exception:
        return False


def interpret_nmcli_error(stdout: str, stderr: str):
    """
    Macht aus der nmcli-Ausgabe eine verständliche Meldung + kurzen Debug-Hinweis.
    """
    text = (stderr or stdout or "").strip()
    short = text.splitlines()[0] if text else ""
    lower = text.lower()

    user_msg = "Verbindung konnte nicht hergestellt werden."

    if "no device" in lower or "unknown device" in lower:
        user_msg = "WLAN-USB-Stick wird nicht richtig erkannt."
    elif "no wifi device" in lower:
        user_msg = "Kein gültiges WLAN-Gerät gefunden (WLAN-Stick fehlt oder wird nicht richtig erkannt)."
    elif "no network with ssid" in lower or "wifi network could not be found" in lower or "ssid not found" in lower:
        user_msg = "Der eingegebene WLAN-Name (SSID) wurde nicht gefunden. Bitte Schreibweise und Abstand zum Router prüfen."
    elif "wrong password" in lower or "secrets were required, but not provided" in lower or "invalid passphrase" in lower:
        user_msg = "Das WLAN-Passwort scheint nicht zu stimmen. Bitte erneut eingeben."
    elif "no suitable device found" in lower or "profile is not compatible with device" in lower:
        user_msg = "Das WLAN-Profil passt nicht zum Gerät (z.B. falsches Interface wie eth0 statt WLAN-Stick)."
    elif "activation failed" in lower:
        user_msg = "Der Router hat die Verbindung abgelehnt oder es gibt ein Problem mit den WLAN-Einstellungen."

    debug_msg = f" (Details für Profis: {short})" if short else ""
    return user_msg + debug_msg


def soft_reset_wifi_dongle():
    """
    Resetet nur den WLAN-Client (USB-Dongle, WIFI_INTERFACE),
    ohne den Access-Point (AP_INTERFACE) zu beeinflussen.
    """
    try:
        subprocess.run(
            ["nmcli", "device", "disconnect", WIFI_INTERFACE],
            capture_output=True,
            text=True,
        )
    except Exception:
        pass

    time.sleep(3)


def get_ap_ssid():
    """Liefert die aktuelle SSID des Access-Points (AP_CONNECTION_NAME) oder None."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "802-11-wireless.ssid", "connection", "show", AP_CONNECTION_NAME],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if result.returncode != 0:
            return None

        for line in result.stdout.splitlines():
            if line.startswith("802-11-wireless.ssid:"):
                val = line.split(":", 1)[1].strip()
                if val:
                    return val
    except Exception:
        pass
    return None


# ---------------- System / Stats ----------------

def is_autodarts_active() -> bool:
    try:
        r = subprocess.run(
            ["systemctl", "is-active", AUTODARTS_SERVICE],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        return r.stdout.strip() == "active"
    except Exception:
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
    autodarts_active = is_autodarts_active()
    autodarts_version = get_autodarts_version()
    cpu_pct, mem_used, mem_total, temp_c = get_system_stats()
    current_ap_ssid = get_ap_ssid()
    wifi_ok = bool(ssid and ip)
    dongle_ok = wifi_ok or wifi_dongle_present()

    data = (
        ssid, ip,
        autodarts_active, autodarts_version,
        cpu_pct, mem_used, mem_total, temp_c,
        wifi_ok, dongle_ok,
        current_ap_ssid,
    )
    try:
        INDEX_STATS_CACHE['ts'] = now
        INDEX_STATS_CACHE['data'] = data
    except Exception:
        pass
    return data



# ---------------- Notes / Cam config ----------------


def load_cam_config():
    try:
        with open(CAM_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cam_config(config: dict):
    os.makedirs(os.path.dirname(CAM_CONFIG_PATH), exist_ok=True)
    with open(CAM_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)



# --- V4L2 Probe Helpers (robustere Kamera-Auswahl & bessere Fehlermeldungen) ---
V4L2CTL_TIMEOUT = 1.5

def _v4l2ctl(args: list[str], timeout: float = V4L2CTL_TIMEOUT):
    """Run v4l2-ctl with timeout; returns CompletedProcess or None."""
    try:
        return subprocess.run(
            ["v4l2-ctl", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

def probe_v4l2_device(dev: str) -> dict:
    """Probe device for pixel formats and discrete resolutions.

    Returns:
      {
        ok: bool,
        formats: set[str],
        resolutions: dict[str, list[tuple[int,int]]],  # fmt -> [(w,h), ...]
        error: str|None,
        raw: str
      }
    """
    r = _v4l2ctl(["-d", dev, "--list-formats-ext"])
    if not r:
        return {"ok": False, "formats": set(), "resolutions": {}, "error": "v4l2-ctl nicht verfügbar oder Timeout.", "raw": ""}

    raw = (r.stdout or "") + ("\n" + (r.stderr or "") if r.stderr else "")
    if r.returncode != 0:
        # Häufig: Permission denied / Not a capture device / busy
        err = (r.stderr or r.stdout or "").strip() or f"v4l2-ctl returncode {r.returncode}"
        return {"ok": False, "formats": set(), "resolutions": {}, "error": err, "raw": raw}

    fmt = None
    formats: set[str] = set()
    resolutions: dict[str, list[tuple[int, int]]] = {}

    re_fmt = re.compile(r"Pixel\s+Format:\s+'([A-Z0-9]+)'")
    re_size = re.compile(r"Size:\s+Discrete\s+(\d+)x(\d+)")
    for line in (r.stdout or "").splitlines():
        m = re_fmt.search(line)
        if m:
            fmt = m.group(1)
            formats.add(fmt)
            resolutions.setdefault(fmt, [])
            continue
        m = re_size.search(line)
        if m and fmt:
            w, h = int(m.group(1)), int(m.group(2))
            if (w, h) not in resolutions[fmt]:
                resolutions[fmt].append((w, h))

    return {"ok": True, "formats": formats, "resolutions": resolutions, "error": None, "raw": raw}

def _best_resolution_for_formats(resolutions: dict[str, list[tuple[int,int]]], preferred_formats: list[str]) -> tuple[str|None, str|None]:
    """Pick best (format, WxH) based on preferences and available discrete sizes."""
    # Prefer common sizes if present, otherwise max area.
    preferred_sizes = [(1920,1080),(1600,1200),(1280,720),(1024,768),(800,600),(640,480)]
    for fmt in preferred_formats:
        sizes = resolutions.get(fmt) or []
        if not sizes:
            continue
        for wh in preferred_sizes:
            if wh in sizes:
                return fmt, f"{wh[0]}x{wh[1]}"
        # fallback: largest area
        w,h = max(sizes, key=lambda x: x[0]*x[1])
        return fmt, f"{w}x{h}"
    return None, None

def _pick_best_video_device(video_devs: list[str]) -> str | None:
    """Pick best /dev/videoX from a physical camera group.

    Preference:
      - device that supports MJPG (best for mjpg_streamer)
      - else device that supports YUYV
      - else first device that at least responds to v4l2-ctl
    """
    best = None
    best_score = -1
    for dev in video_devs:
        p = probe_v4l2_device(dev)
        if not p.get("ok"):
            # still might be usable; but score low
            score = 0
        else:
            fmts = p.get("formats", set())
            if "MJPG" in fmts:
                score = 3
            elif "YUYV" in fmts:
                score = 2
            elif fmts:
                score = 1
            else:
                score = 0
        if score > best_score:
            best_score = score
            best = dev
    return best

def _v4l2_device_info(dev: str) -> dict:
    """Return basic v4l2 device info using 'v4l2-ctl -D'."""
    r = _v4l2ctl(["-d", dev, "-D"], timeout=0.9)
    if not r or r.returncode != 0:
        return {}
    info = {}
    for line in (r.stdout or "").splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().lower()
        v = v.strip()
        if k in ("driver name", "card type", "bus info"):
            info[k] = v
    return info


def _is_probably_camera_device(dev: str) -> bool:
    """Heuristic to avoid false positives like codec/ISP/decoder nodes."""
    info = _v4l2_device_info(dev)
    card = (info.get("card type") or "").lower()
    bus = (info.get("bus info") or "").lower()

    bad = ["codec", "isp", "rpivid", "v4l2loopback", "loopback", "virtual", "m2m", "mem2mem", "decoder", "encoder"]
    if any(b in card for b in bad) or any(b in bus for b in bad):
        return False

    # Must have at least one format we can stream
    p = probe_v4l2_device(dev)
    if not p.get("ok"):
        return False
    fmts = p.get("formats", set()) or set()
    if not fmts:
        return False

    # Prefer USB cameras
    if "usb" in bus:
        return True

    # CSI / sensor-style names
    good = ["camera", "webcam", "uvc", "unicam", "csi", "ov", "imx", "ar", "gc", "s5k"]
    if any(g in card for g in good) or any(g in bus for g in good):
        return True

    # Unknown: be conservative to prevent ghost-devices when no camera is attached
    return False



def detect_cameras(desired_count: int):
    """
    Erkennt Kameras möglichst zuverlässig.

    1) Wenn verfügbar, nutzt es 'v4l2-ctl --list-devices', um physische USB-Kameras zu gruppieren
       und pro Kamera genau EIN /dev/videoX zu wählen (verhindert Doppel-/Meta-Devices).
    2) Fallback: einfache /dev/video0..N Suche.
    """
    # 1) Versuch: v4l2-ctl verwenden
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--list-devices"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            lines = result.stdout.splitlines()

            groups = []  # Liste von (name, [video-devices])
            current_name = None
            current_videos = []

            for line in lines:
                if line.strip() == "":
                    continue

                if not line.startswith("	") and not line.startswith(" "):
                    # Neue Gerätegruppe beginnt
                    if current_name and current_videos:
                        groups.append((current_name, current_videos))
                    current_name = line.strip().rstrip(":")
                    current_videos = []
                else:
                    # Eingrückte Zeile -> Device
                    path = line.strip()
                    if path.startswith("/dev/video"):
                        current_videos.append(path)

            # letzte Gruppe übernehmen
            if current_name and current_videos:
                groups.append((current_name, current_videos))

            # Gruppen filtern: echte Kameras bevorzugen, System/Meta-Geräte eher raus
            def _looks_like_camera(name: str) -> bool:
                n = (name or "").lower()

                # harte Ausschlüsse: typische System/Codec/ISP/Decoder-Geräte
                bad = ["codec", "isp", "rpivid", "v4l2loopback", "loopback", "virtual", "m2m", "mem2mem", "decoder", "encoder"]
                if any(b in n for b in bad):
                    return False

                # USB-Kameras erkennt man meist direkt
                if "usb" in n or "uvc" in n or "webcam" in n or "camera" in n:
                    return True

                # CSI/Sensoren (ov9732, imx219, ...)
                sensor = ["ov", "imx", "ar", "gc", "s5k", "unicam", "csi"]
                if any(s in n for s in sensor):
                    return True

                # Default: konservativ, sonst findet man ohne Kamera gerne 'Geistergeräte'
                return False

            cam_groups = [g for g in groups if _looks_like_camera(g[0])]

            devices = []
            for name, videos in cam_groups:
                videos_sorted = sorted(videos)
                # Einige Kameras liefern mehrere /dev/videoX Nodes (Meta/H264/etc.).
                # Wir wählen nach Möglichkeit den Node, der MJPG (oder YUYV) anbietet.
                dev = _pick_best_video_device(videos_sorted) or videos_sorted[0]
                if _is_probably_camera_device(dev):
                    devices.append(dev)
                if len(devices) >= desired_count:
                    break

            if devices:
                return devices
    except FileNotFoundError:
        # v4l2-ctl nicht vorhanden -> Fallback
        pass
    except Exception as e:
        print(f"[autodarts-web] Warnung detect_cameras mit v4l2-ctl: {e}")

    # 2) Fallback: einfache /dev/video0..N-Suche
    found = []
    for idx in range(MAX_VIDEO_INDEX):
        dev = f"/dev/video{idx}"
        if os.path.exists(dev) and _is_probably_camera_device(dev):
            found.append(dev)
            if len(found) >= desired_count:
                break
    return found

# ---------------- systemd helpers ----------------

def service_exists(service_name: str) -> bool:
    candidates = [
        f"/etc/systemd/system/{service_name}",
        f"/lib/systemd/system/{service_name}",
        f"/usr/lib/systemd/system/{service_name}",
    ]
    return any(os.path.exists(p) for p in candidates)


# systemctl helper (verhindert Hänger durch blockierende systemctl-Aufrufe)
SYSTEMCTL_CHECK_TIMEOUT = 2.0
SYSTEMCTL_ACTION_TIMEOUT = 20.0

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
        # Beispiele:
        # ExecStart=/home/peter/.local/bin/autodarts
        # ExecStart={ path=/home/peter/.local/bin/autodarts ; argv[]=/home/peter/.local/bin/autodarts ; ... }
        m = re.search(r"/[^\s;]+", line)
        if m:
            p = m.group(0).strip()
            return p if os.path.exists(p) else p  # exist check optional
    except Exception:
        pass
    return None


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


def get_autodarts_version() -> str | None:
    """Liest die installierte Autodarts-Version (über autodarts --version)."""
    # Cache (damit die Startseite nicht träge wird)
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
            # fallback: manche Tools nutzen -V
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


def _get_autodarts_updater_path() -> str | None:
    """Versucht updater.sh zu finden (wird vom offiziellen Installer angelegt)."""
    # Neben dem autodarts binary
    bin_path = get_autodarts_binary_path()
    if bin_path:
        d = os.path.dirname(bin_path)
        cand = os.path.join(d, "updater.sh")
        if os.path.exists(cand):
            return cand

    # typische Orte
    candidates = [
        os.path.expanduser("~/.local/bin/updater.sh"),
        "/home/peter/.local/bin/updater.sh",
        "/home/pi/.local/bin/updater.sh",
        "/root/.local/bin/updater.sh",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def load_update_state() -> dict:
    try:
        with open(AUTODARTS_UPDATE_STATE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_update_state(state: dict):
    try:
        os.makedirs(os.path.dirname(AUTODARTS_UPDATE_STATE), exist_ok=True)
        with open(AUTODARTS_UPDATE_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


def start_autodarts_update_background() -> tuple[bool, str]:
    """Startet ein Autodarts-Update im Hintergrund und loggt nach AUTODARTS_UPDATE_LOG."""
    # Läuft schon?
    state = load_update_state()
    pid = state.get("pid")
    if pid:
        try:
            os.kill(int(pid), 0)
            return False, "Update läuft bereits."
        except Exception:
            pass  # PID tot -> weiter

    # Command bestimmen
    cmd = SETTINGS.get("autodarts_update_cmd") or ""
    cmd = cmd.strip()

    updater = _get_autodarts_updater_path()
    if not cmd:
        if updater:
            cmd = updater
        else:
            # Fallback auf offiziellen Installer (holt auch updater.sh neu)
            cmd = "bash <(curl -sL get.autodarts.io)"

    # Log-File öffnen
    try:
        os.makedirs(os.path.dirname(AUTODARTS_UPDATE_LOG), exist_ok=True)
    except Exception:
        pass

    try:
        logf = open(AUTODARTS_UPDATE_LOG, "a", encoding="utf-8")
        logf.write("\n\n===== Autodarts Update gestartet: %s =====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        logf.flush()

        # in bash ausführen (für process substitution)
        p = subprocess.Popen(
            ["bash", "-lc", cmd],
            stdout=logf,
            stderr=logf,
            close_fds=True,
        )

        save_update_state({"pid": p.pid, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "cmd": cmd})
        return True, "Update gestartet."
    except Exception as e:
        return False, f"Update konnte nicht gestartet werden: {e}"



# ---------------- Webpanel Update (dieser Flask-Service) ----------------

def get_webpanel_version() -> str | None:
    """Liest die installierte Webpanel-Version (lokale version.txt)."""
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


def fetch_latest_webpanel_version(timeout_s: float = 2.0) -> str | None:
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


def start_webpanel_update_background() -> tuple[bool, str]:
    """Startet das Webpanel-Update Script im Hintergrund. Das Script darf den Service neu starten."""
    if not os.path.exists(WEBPANEL_UPDATE_SCRIPT):
        return False, f"Update-Script nicht gefunden: {WEBPANEL_UPDATE_SCRIPT}"

    # Läuft schon?
    state = load_webpanel_update_state()
    pid = state.get("pid")
    if pid:
        try:
            os.kill(int(pid), 0)
            return False, "Webpanel-Update läuft bereits."
        except Exception:
            pass

    try:
        os.makedirs(os.path.dirname(WEBPANEL_UPDATE_LOG), exist_ok=True)
    except Exception:
        pass

    # Command bauen (wenn nicht root → sudo -n, damit es nicht hängen kann)
    cmd = [WEBPANEL_UPDATE_SCRIPT]
    if os.geteuid() != 0:
        cmd = ["sudo", "-n"] + cmd

    try:
        logf = open(WEBPANEL_UPDATE_LOG, "a", encoding="utf-8")
        logf.write("\n\n===== Webpanel Update gestartet: %s =====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        logf.write("CMD: %s\n" % " ".join(cmd))
        logf.flush()

        p = subprocess.Popen(
            cmd,
            stdout=logf,
            stderr=logf,
            close_fds=True,
        )

        save_webpanel_update_state({"pid": p.pid, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "cmd": " ".join(cmd)})
        return True, "Webpanel-Update gestartet. Die Seite lädt in wenigen Sekunden neu (Service-Restart)."
    except Exception as e:
        return False, f"Webpanel-Update konnte nicht gestartet werden: {e}"



def service_enable_now(service_name: str):
    _run_systemctl(["enable", "--now", service_name], timeout=SYSTEMCTL_ACTION_TIMEOUT)

def service_disable_now(service_name: str):
    _run_systemctl(["disable", "--now", service_name], timeout=SYSTEMCTL_ACTION_TIMEOUT)

def service_restart(service_name: str):
    _run_systemctl(["restart", service_name], timeout=SYSTEMCTL_ACTION_TIMEOUT)

def service_is_enabled(service_name: str) -> bool:
    r = _run_systemctl(["is-enabled", service_name], timeout=SYSTEMCTL_CHECK_TIMEOUT)
    return bool(r and r.stdout.strip() == "enabled")

def autodarts_autoupdate_is_enabled() -> bool | None:
    if not service_exists(AUTOUPDATE_SERVICE):
        return None
    return service_is_enabled(AUTOUPDATE_SERVICE)

def autodarts_set_autoupdate(enabled: bool) -> tuple[bool, str]:
    if not service_exists(AUTOUPDATE_SERVICE):
        return False, f"{AUTOUPDATE_SERVICE} nicht gefunden."
    try:
        if enabled:
            subprocess.run(["systemctl", "enable", "--now", AUTOUPDATE_SERVICE], capture_output=True, text=True)
            return True, "Auto-Update aktiviert."
        subprocess.run(["systemctl", "disable", "--now", AUTOUPDATE_SERVICE], capture_output=True, text=True)
        return True, "Auto-Update deaktiviert."
    except Exception as e:
        return False, f"Auto-Update konnte nicht geändert werden: {e}"


# ---------------- Update-Check (nur bei Klick) ----------------

def load_update_check() -> dict:
    try:
        with open(AUTODARTS_UPDATE_CHECK, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}

def save_update_check(d: dict):
    os.makedirs(os.path.dirname(AUTODARTS_UPDATE_CHECK), exist_ok=True)
    with open(AUTODARTS_UPDATE_CHECK, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)

def _get_platform_arch_for_autodarts() -> tuple[str, str]:
    # Plattform ist im Installer 'linux'
    platform = "linux"
    arch = subprocess.run(["uname", "-m"], capture_output=True, text=True).stdout.strip()
    if arch in ("x86_64", "amd64"):
        arch = "amd64"
    elif arch in ("aarch64", "arm64"):
        arch = "arm64"
    elif arch == "armv7l":
        arch = "armv7l"
    return platform, arch

def _get_updater_channel() -> str:
    # Versuche CHANNEL aus updater.sh zu lesen (latest/beta)
    updater = _get_autodarts_updater_path()
    if updater:
        try:
            for line in Path(updater).read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("CHANNEL="):
                    v = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if v:
                        return v
        except Exception:
            pass
    return "latest"

def fetch_latest_autodarts_version(channel: str | None = None, timeout_s: float = 2.5) -> str | None:
    try:
        platform, arch = _get_platform_arch_for_autodarts()
        ch = (channel or _get_updater_channel()).strip() or "latest"
        # Installer nutzt:
        # latest: detection/latest/<platform>/<arch>/RELEASES.json
        # beta:   detection/beta/<platform>/<arch>/RELEASES.json
        url = f"https://get.autodarts.io/detection/{ch}/{platform}/{arch}/RELEASES.json"
        req = urllib.request.Request(url, headers={"User-Agent": "AutodartsPanel"})
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            data = json.loads(r.read().decode("utf-8", errors="ignore") or "{}")
        cv = str(data.get("currentVersion", "")).strip()
        if cv.startswith("v"):
            cv = cv[1:]
        return cv or None
    except Exception:
        return None


# ---------------- Verbindungstest (Ping) ----------------

PING_JOBS: dict[str, dict] = {}

def get_default_gateway() -> str | None:
    # Default-Route -> Gateway IP
    try:
        r = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True)
        if r.returncode != 0:
            return None
        # Beispiel: 'default via 192.168.178.1 dev wlan0 ...'
        m = re.search(r"default\s+via\s+(\d+\.\d+\.\d+\.\d+)", r.stdout)
        return m.group(1) if m else None
    except Exception:
        return None

def _ping_worker(job_id: str, target: str, count: int):
    job = PING_JOBS.get(job_id)
    if not job:
        return
    times = []
    received = 0
    try:
        p = subprocess.Popen(
            ["ping", "-n", "-c", str(count), target],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )
        job["pid"] = p.pid
        for line in p.stdout or []:
            # icmp_seq=1 time=12.3 ms
            m = re.search(r"icmp_seq=(\d+).*time=([0-9\.]+)\s*ms", line)
            if m:
                seq = int(m.group(1))
                t = float(m.group(2))
                times.append(t)
                received = len(times)
                job["progress"] = max(job.get("progress", 0), seq)
                job["received"] = received
        p.wait()
        # Summary parse (optional)
        # '30 packets transmitted, 30 received, 0% packet loss, time ...'
        # We prefer our measured times for min/avg/max.
    except Exception as e:
        job["error"] = str(e)

    if times:
        job["min_ms"] = round(min(times), 2)
        job["max_ms"] = round(max(times), 2)
        job["avg_ms"] = round(sum(times) / len(times), 2)
    job["done"] = True

def start_ping_test(count: int = 30) -> tuple[bool, str, str | None]:
    gw = get_default_gateway()
    if not gw:
        return False, "Kein Gateway gefunden (nicht verbunden?).", None
    job_id = uuid.uuid4().hex[:10]
    PING_JOBS[job_id] = {
        "target": gw,
        "count": int(count),
        "started": time.time(),
        "progress": 0,
        "received": 0,
        "done": False,
        "min_ms": None,
        "max_ms": None,
        "avg_ms": None,
        "error": None,
        "pid": None,
    }
    th = threading.Thread(target=_ping_worker, args=(job_id, gw, int(count)), daemon=True)
    th.start()
    return True, "Ping gestartet.", job_id



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
        return False, f"start-custom.sh nicht gefunden: {DARTS_WLED_START_CUSTOM}"

    try:
        with open(DARTS_WLED_START_CUSTOM, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        return False, f"start-custom.sh konnte nicht gelesen werden: {e}"

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
        return False, "Keine -WEPS Zeile in start-custom.sh gefunden (unerwartetes Format)."

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
        return False, f"start-custom.sh konnte nicht geschrieben werden: {e}"

    return True, "start-custom.sh (-WEPS) aktualisiert."


# ---------------- WLED legacy flag compatibility ----------------

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

def tail_file(path: str, n: int = 20, max_chars: int = 6000) -> str:
    """Liest die letzten N Zeilen einer Datei, ohne die komplette Datei einzulesen."""
    try:
        if not os.path.exists(path):
            return ""

        # Wir lesen von hinten in Blöcken, bis wir genug Zeilen haben.
        block_size = 4096
        data = b""
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()

            while pos > 0 and data.count(b"\n") <= (n + 2) and len(data) < (max_chars * 6):
                step = min(block_size, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data

        text = data.decode("utf-8", errors="replace")
        out = "\n".join(text.splitlines()[-n:])
        if len(out) > max_chars:
            out = out[-max_chars:]
        return out.strip()
    except Exception:
        return ""

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
        return {"running": False, "msg": "Nicht aktiv."}

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
        "msg": f"Läuft (PID {st.get('pid')}). Start: {started} · Ende: {ends}",
        **st,
    }


def start_pi_monitor(interval_s: int, duration_min: int) -> dict:
    # Guard: nicht mehrfach starten
    st = _read_pi_monitor_state()
    if st.get("running"):
        return {"ok": False, "running": True, "msg": "Pi Monitor läuft bereits – bitte warten bis er fertig ist."}

    if interval_s < 1 or interval_s > 3600:
        return {"ok": False, "running": False, "msg": "Intervall ungültig."}
    if duration_min < 1 or duration_min > 24 * 60:
        return {"ok": False, "running": False, "msg": "Dauer ungültig."}

    if not os.path.exists(PI_MONITOR_SCRIPT):
        return {"ok": False, "running": False, "msg": f"Script nicht gefunden: {PI_MONITOR_SCRIPT}"}

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
        return {"ok": False, "running": False, "msg": f"Start fehlgeschlagen: {e}"}

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

    return {"ok": True, "running": True, "msg": "Pi Monitor gestartet.", **get_pi_monitor_status()}


def stop_pi_monitor() -> dict:
    st = _read_pi_monitor_state()
    if not st.get("running"):
        return {"ok": True, "running": False, "msg": "Nicht aktiv."}
    pid = st.get("pid")
    try:
        os.kill(int(pid), 15)  # SIGTERM
    except Exception as e:
        return {"ok": False, "running": True, "msg": f"Konnte nicht stoppen: {e}"}
    return {"ok": True, "running": False, "msg": "Stop gesendet."}

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
        return email, password, board_id, True, f"Fehler beim Lesen von start-custom.sh: {e}"

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
        raise RuntimeError("start-custom.sh: benötigte Variablenzeilen nicht gefunden – es wurde NICHT geschrieben.")

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

@app.route("/help", methods=["GET"])
def help_page():
    """Handbuch als PDF (inline) aus AUTODARTS_DATA_DIR ausliefern."""
    pdf_path = DATA_DIR / HELP_PDF_FILENAME
    if not pdf_path.exists():
        return (
            "<html><body><h1>Handbuch nicht gefunden</h1>"
            "<p>Die Datei <code>Autodarts_install_manual.pdf</code> wurde nicht gefunden.</p>"
            "</body></html>",
            404,
        )

    resp = send_from_directory(str(DATA_DIR), HELP_PDF_FILENAME)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'inline; filename="{HELP_PDF_FILENAME}"'
    return resp


@app.route("/", methods=["GET"])
def index():
    (
        ssid, ip,
        autodarts_active, autodarts_version,
        cpu_pct, mem_used, mem_total, temp_c,
        wifi_ok, dongle_ok,
        current_ap_ssid,
    ) = get_index_stats_cached()
    wifi_signal = None  # Signalstärke wird nur auf Knopfdruck geladen
    ad_restarted = request.args.get("ad_restarted") == "1"


    cam_config = load_cam_config()
    camera_mode = bool(cam_config.get("camera_mode", False))

    desired_cams = int(cam_config.get("desired_cams", 3))
    desired_cams = max(1, min(MAX_CAMERAS, desired_cams))

    cam_devices = cam_config.get("devices", [])
    cam_info_message = ""

    if not cam_devices:
        cam_devices = detect_cameras(desired_cams)
        if cam_devices:
            cam_config["devices"] = cam_devices
            cam_config["desired_cams"] = desired_cams
            save_cam_config(cam_config)

    cam_count_found = len(cam_devices)

    if cam_count_found < desired_cams:
        if cam_count_found == 0:
            cam_info_message = (
                f"Es wurden keine Kameras gefunden, obwohl {desired_cams} erwartet "
                f"wurden. Bitte Verkabelung und USB-Anschlüsse prüfen."
            )
        else:
            cam_info_message = (
                f"Es wurden nur {cam_count_found} Kamera(s) gefunden, "
                f"obwohl {desired_cams} eingestellt sind."
            )
    host = request.host.split(":", 1)[0]
    darts_url = f"http://{host}:3180"

    cam_count_options = list(range(1, MAX_CAMERAS + 1))
    cam_devices_enum = list(enumerate(cam_devices, start=1))
    cam_indices = list(range(1, cam_count_found + 1))

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

    msg = request.args.get('msg', '')
    autoupdate_enabled = autodarts_autoupdate_is_enabled()
    update_check = load_update_check()
    webpanel_version = get_webpanel_version()
    webpanel_check = load_webpanel_update_check() if admin_unlocked else {}
    webpanel_update_available = bool(webpanel_check.get('installed') and webpanel_check.get('latest') and webpanel_check.get('installed') != webpanel_check.get('latest'))
    webpanel_state = load_webpanel_update_state() if admin_unlocked else {}
    webpanel_log_tail = tail_file(WEBPANEL_UPDATE_LOG, n=25, max_chars=3500) if admin_unlocked else ""
    update_available = bool(update_check.get('installed') and update_check.get('latest') and update_check.get('installed') != update_check.get('latest'))

    update_state = load_update_state() if admin_unlocked else {}
    update_log_tail = tail_file(AUTODARTS_UPDATE_LOG, n=25, max_chars=3500) if admin_unlocked else ""

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

    html = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Autodarts Installation</title>
  <style>
    body { font-family: system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:#111; color:#eee; margin:0; padding:0; }
    .container { max-width: 900px; margin: 0 auto; padding: 24px 16px 60px; }
    h1 { font-size: 2rem; margin-bottom: 8px; }
    .subtitle { opacity: .8; margin-bottom: 24px; }
    .card { background:#1c1c1c; border-radius:12px; padding:16px 18px 18px; margin-bottom:18px; box-shadow:0 0 20px rgba(0,0,0,0.4); }
    .card h2 { margin-top:0; font-size:1.2rem; margin-bottom:8px; }
    .btn-row { display:flex; flex-wrap:wrap; gap:10px; margin-top:10px; align-items:center; }
    .btn { display:inline-block; padding:10px 16px; border-radius:999px; border:1px solid #444; background:#2a2a2a; color:#eee; text-decoration:none; font-size:0.95rem; cursor:pointer; }
    .btn-primary { background:#3b82f6; border-color:#3b82f6; color:white; }
    .btn-small { padding:6px 12px; font-size:0.85rem; }
    .btn-disabled { opacity: 0.4; pointer-events: none; filter: grayscale(1); }
    textarea { width:100%; min-height:80px; border-radius:8px; border:1px solid #444; background:#111; color:#eee; padding:8px; resize:vertical; font-family:inherit; }
    label { font-size:0.9rem; display:block; margin-bottom:4px; }
    input[type=text], input[type=password] { width:100%; padding:8px; border-radius:8px; border:1px solid #444; background:#111; color:#eee; margin-bottom:10px; font-family:inherit; }
    select { padding:6px 10px; border-radius:8px; border:1px solid #444; background:#111; color:#eee; font-family:inherit; }
    .cam-buttons { display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap; }
    .info-msg { font-size:0.85rem; margin:6px 0 0; color:#ffb347; }
    .hint { font-size:0.8rem; opacity:0.7; margin-top:4px; }
    .msg-ok { color:#6be26b; margin-top:10px; white-space:pre-line; }
    .msg-bad { color:#ff6b6b; margin-top:10px; white-space:pre-line; }
    footer { margin-top:24px; font-size:0.75rem; opacity:0.6; text-align:center; }
    .sysinfo { position:fixed; top:10px; right:10px; background:#1c1c1c; border-radius:8px; padding:8px 10px; font-size:0.8rem; box-shadow:0 0 10px rgba(0,0,0,0.6); z-index:1000; border:1px solid #333; }
    .sysinfo h3 { margin:0 0 4px; font-size:0.9rem; }
    .sysinfo-row { white-space:nowrap; }
    code { background:#0b0b0b; padding:2px 6px; border-radius:6px; border:1px solid #333; }
    .dot { display:inline-block; width:10px; height:10px; border-radius:999px; margin-right:6px; vertical-align:middle; }
    .dot-green { background:#6be26b; }
    .dot-red { background:#ff6b6b; }
    .dot-gray { background:#9ca3af; }
    details { border:1px solid #333; border-radius:12px; padding:12px 14px; background:#171717; }
    details summary { cursor:pointer; list-style:none; user-select:none; font-weight:600; }
    details summary::-webkit-details-marker { display:none; }
    .admin-hint { font-size:0.8rem; opacity:0.75; margin-top:6px; }

    .ad-status { display:inline-block; padding:4px 10px; border-radius:999px; font-size:0.9rem; margin-bottom:10px; font-weight:bold; }
    .ad-on { background:#16a34a33; border:1px solid #16a34a; color:#bbf7d0; }
    .ad-off { background:#b91c1c33; border:1px solid #b91c1c; color:#fecaca; }
    .slot-card { border:1px solid #333; border-radius:10px; padding:10px 12px; margin:10px 0; }
    .slot-row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; justify-content:space-between; }
  </style>
</head>
<body>

  {% if cpu_pct is not none or mem_used is not none or temp_c is not none or autoupdate_enabled is not none %}
  <div class="sysinfo">
    <h3>Mini PC</h3>
    {% if cpu_pct is not none %}
      <div class="sysinfo-row">CPU: {{ cpu_pct }}%</div>
    {% endif %}
    {% if mem_used is not none and mem_total is not none %}
      <div class="sysinfo-row">RAM: {{ mem_used }} / {{ mem_total }} GB</div>
    {% endif %}
    {% if temp_c is not none %}
      <div class="sysinfo-row">Temp: {{ "%.1f"|format(temp_c) }} °C</div>
    {% endif %}
    {% if autoupdate_enabled is not none %}
      <div class="sysinfo-row" style="margin-top:6px;">
        Auto-Update:
        {% if autoupdate_enabled %}
          <span style="color:#6be26b; font-weight:700;">AN</span>
        {% else %}
          <span style="color:#ff6b6b; font-weight:700;">AUS</span>
        {% endif %}
      </div>
      <form method="post" action="{{ url_for('autoupdate_toggle') }}" style="margin-top:6px;">
        <button type="submit" class="btn btn-small" style="width:100%;">
          {% if autoupdate_enabled %}Auto-Update ausschalten{% else %}Auto-Update einschalten{% endif %}
        </button>
      </form>
    {% endif %}

  </div>
  {% endif %}

  <div class="container">
    {% if autodarts_active %}
      <div class="ad-status ad-on">Autodarts: AKTIV</div>
    {% else %}
      <div class="ad-status ad-off">Autodarts: DEAKTIVIERT</div>
    {% endif %}
    <div class="hint" style="margin:-6px 0 14px 0;">
      Autodarts Version: <strong>{{ autodarts_version or 'unbekannt' }}</strong>
    </div>

    <h1>Willkommen bei der Autodarts Installation</h1>
    <div class="subtitle">by Peter Rottmann v.{{ webpanel_version or '1.20' }} (Multi-WLED)</div>

    {% if not wifi_ok %}
      <div style="background:#7f1d1d; border:2px solid #f87171; color:#fee2e2;
                  padding:12px 16px; border-radius:10px; margin-bottom:18px;">
        <strong style="font-size:1.1rem;">
          ACHTUNG: Sie sind NICHT mit dem Internet verbunden, nicht in Ihrem Wlan!
        </strong>
        <p style="margin:6px 0 0; font-size:0.9rem;">
          Der <strong>Dartscheiben-Manager</strong> (Schritt 2) funktioniert nur,
          wenn der Mini PC mit Ihrem <strong>Heim-WLAN</strong> verbunden ist.
        </p>
      </div>
    {% endif %}

    <div class="btn-row" style="margin-bottom: 16px;">
      <a href="{{ url_for('help_page') }}" target="_blank" class="btn btn-small">
        Hilfe / Handbuch öffnen (PDF)
      </a>
      <a href="{{ url_for('ap_config') }}" class="btn btn-small">
        Access-Point (Mini PC-WLAN) auswählen
      </a>
    </div>

    <div class="card">
      <h2>1. Mit WLAN zuhause verbinden</h2>
      <p>
        {% if ssid and ip %}
          Erfolgreich mit Ihrem WLAN <strong>{{ ssid }}</strong> verbunden
          (IP-Adresse: <strong>{{ ip }}</strong>) · Signal: <strong id="wifiSignalOut">—</strong> <button type="button" class="btn btn-small" id="wifiSignalBtn" onclick="fetchWifiSignal()">Signal anzeigen</button>.
        {% elif ssid and not ip %}
          Mit WLAN <strong>{{ ssid }}</strong> verbunden,
          aber es wurde keine IPv4-Adresse vergeben. Signal: <strong id="wifiSignalOut">—</strong> <button type="button" class="btn btn-small" id="wifiSignalBtn" onclick="fetchWifiSignal()">Signal anzeigen</button>
        {% else %}
          Aktuell ist der Mini PC nicht über den USB-WLAN-Dongle mit Ihrem
          Heimnetzwerk verbunden.
        {% endif %}
      </p>

      {% if not dongle_ok %}
        <p class="msg-bad">
          Der WLAN-USB-Stick wird aktuell nicht erkannt.
          Bitte ziehen Sie den USB-Stick kurz ab und stecken Sie ihn wieder ein,
          warten Sie ein paar Sekunden und versuchen Sie es erneut.
          <span class="hint"><br>(Für Profis: Kein WiFi-Device <code>{{ wifi_interface }}</code> im NetworkManager sichtbar.)</span>
        </p>
      {% endif %}

      <p>
        Verbinden Sie den Mini PC zuerst mit dem WLAN in Ihrem Zuhause.
        Falls bei Ihnen kein WLAN vorhanden ist, können Sie alternativ ein LAN-Kabel verwenden.
      </p>
      <div class="btn-row">
        <a href="{{ url_for('wifi') }}" class="btn btn-primary">WLAN-Verbindung einrichten / ändern</a>
        <button type="button" id="pingBtn" class="btn {% if not wifi_ok %}btn-disabled{% endif %}">Verbindungstest (30 Pakete)</button>
      </div>
      <div id="pingResult" class="hint" style="margin-top:10px;"></div>

    </div>

    <div class="card">
      <h2>2. Dartscheibe mit Autodarts-Account verknüpfen</h2>
      <p>
        Sie benötigen ein bestehendes Autodarts-Konto. Falls noch keines vorhanden ist,
        können Sie es auf <strong>autodarts.io</strong> anlegen.
      </p>
      <div class="btn-row">
        <a href="{{ darts_url }}" target="_blank" class="btn btn-primary">Dartscheiben-Manager öffnen</a>
      </div>
    </div>

<div class="card">
  <h2>3. Kamera einstellen</h2>

  {% if autodarts_notice %}
    <p class="info-msg">
      Autodarts wurde wieder gestartet.
      Wenn Sie fertig sind, prüfen Sie bitte kurz im Dartscheiben-Manager (Schritt&nbsp;2),
      ob die Kamera-Streams dort wieder korrekt laufen.
    </p>
  {% endif %}

  <p>
    Hier können Sie die Kameras einstellen (Bild ausrichten, scharf stellen).
    Dafür wird Autodarts kurz pausiert und ein Live-Bild angezeigt.
  </p>

  <div class="btn-row">
    {% if not camera_mode %}
      <form method="post" action="{{ url_for('camera_mode_start') }}"
            onsubmit="return confirm('Während der Kamera-Einstellung wird Autodarts kurz pausiert. Fortfahren?');">
        <button type="submit" class="btn btn-primary">Kamera einstellen starten</button>
      </form>
    {% else %}
      <form method="post" action="{{ url_for('camera_mode_end') }}">
        <button type="submit" class="btn btn-danger">Autodarts wieder starten</button>
      </form>
    {% endif %}
  </div>

  <hr style="border:none; border-top:1px solid #333; margin:14px 0;">

  <form method="post" action="{{ url_for('set_cams') }}">
    <label for="cam_count">Wie viele Kameras sind angeschlossen? (max. {{ max_cams }})</label>
    <select id="cam_count" name="cam_count">
      {% for n in cam_count_options %}
        <option value="{{ n }}" {% if n == desired_cams %}selected{% endif %}>{{ n }}</option>
      {% endfor %}
    </select>
    <div class="btn-row">
      <button type="submit" class="btn btn-small btn-primary">Kameras suchen</button>
    </div>
  </form>

  {% if cam_count_found > 0 %}
    <p style="margin-top:12px;">
      Gefundene Kameras: {{ cam_count_found }}<br>
      {% for idx, dev in cam_devices_enum %}
        Kamera {{ idx }} → <code>{{ dev }}</code><br>
      {% endfor %}
    </p>
  {% else %}
    <p style="margin-top:12px;">Es wurden keine Kameras gefunden.</p>
  {% endif %}

  {% if cam_info_message %}
    <p class="info-msg">{{ cam_info_message }}</p>
  {% endif %}

  {% if camera_mode and cam_count_found > 0 %}
    <p style="margin-top:12px;">Klicken Sie auf eine Kamera, um das Live-Bild zu öffnen:</p>
    <div class="cam-buttons">
      {% for idx in cam_indices %}
        <a href="{{ url_for('cam_view', cam_id=idx) }}" target="_blank" class="btn btn-small">
          Kamera {{ idx }}
        </a>
      {% endfor %}
    </div>
  {% elif not camera_mode %}
    <p class="hint" style="margin-top:10px;">
      Tipp: Erst <strong>Kamera einstellen starten</strong> klicken – dann können Sie die Live-Bilder öffnen.
    </p>
  {% endif %}
</div>

<!-- LED Konfiguration -->
    <div class="card">
      <h2>LED Konfiguration</h2>

      <p class="hint" style="margin-top:0;">
        Achtung: Autodarts-Account darf <strong>keine 2-Faktor-Authentifizierung (2FA)</strong> aktiviert haben.
      </p>

      {% if ledmsg %}
        <p class="{{ 'msg-ok' if ledcheck=='ok' else 'msg-bad' }}">{{ ledmsg }}</p>
      {% endif %}

      {% if caller_err %}
        <p class="msg-bad">{{ caller_err }}</p>
      {% endif %}

      <p class="hint">
        Datei: <code>/var/lib/autodarts/extensions/darts-caller/start-custom.sh</code>
      </p>

      <!-- Darts-Caller Credentials -->
      <form method="post" action="{{ url_for('led_save') }}">
        <label for="ad_email">Autodarts-Account</label>
        <input type="text" id="ad_email" name="ad_email" value="{{ caller_email }}">

        <label for="ad_password">Autodarts-Passwort</label>
        <input type="password" id="ad_password" name="ad_password" value="" placeholder="(leer lassen = unverändert)">

        <label for="ad_board">Autodarts-Boardnummer / Board-ID</label>
        <input type="text" id="ad_board" name="ad_board" value="{{ caller_board_id }}">

        <div class="btn-row">
          <button type="submit" class="btn btn-primary {% if not can_save_creds %}btn-disabled{% endif %}">
            Speichern
          </button>
        </div>
      </form>

      {% if not caller_exists %}
        <p class="info-msg">
          darts-caller start-custom.sh nicht gefunden. LED Konfiguration ist deaktiviert.
        </p>
      {% endif %}

      
      <!-- LED-Bänder (WLED) -->
<h3 style="margin:0 0 6px; font-size:1.05rem;">LED Bänder (bis zu 3)</h3>
<p class="hint" style="margin-top:0;">Hier können Sie die LEDs verwalten, bzw. ein und ausschalten.</p>

{% if not wled_installed %}
  <p class="info-msg">Die LED-Steuerung ist auf diesem Mini PC nicht installiert.</p>
{% endif %}

{% if not wled_master_enabled %}
  <p class="msg-bad">WLED ist global deaktiviert (Master-Schalter AUS). Bitte im Admin-Bereich wieder aktivieren.</p>
{% endif %}

<form id="wledForm">
<fieldset {% if not wled_master_enabled %}disabled style="opacity:0.55"{% endif %}>
  {% for b in wled_bands %}
    <div style="border:1px solid #333; border-radius:12px; padding:12px 14px; margin:10px 0;">
      <div style="display:flex; gap:12px; flex-wrap:wrap; align-items:center; justify-content:space-between;">
        <div style="font-weight:700;">LED Band {{ b.slot }}</div>

        <div style="display:flex; align-items:center; gap:12px;">
          <label style="display:flex; align-items:center; gap:8px; margin:0;">
            <input type="checkbox" name="wled_enabled_{{ b.slot }}" value="1"
                   {% if b.enabled %}checked{% endif %}
                   {% if not wled_installed %}disabled{% endif %}>
            <span>Ein / Aus</span>
          </label>

          <span id="wled_status_{{ b.slot }}" style="font-size:0.9rem;">
            {% if not b.enabled %}
              <span class="dot dot-gray"></span>Aus
            {% else %}
              <span class="dot dot-gray"></span>Prüfe…
            {% endif %}
          </span>

          <a href="{{ url_for('wled_open_slot', slot=b.slot) }}" target="_blank"
             id="wled_cfgbtn_{{ b.slot }}" class="btn btn-small {% if not wled_installed or not b.enabled %}btn-disabled{% endif %}">
            LED konfigurieren
          </a>
        </div>
      </div>
    </div>
  {% endfor %}

  </fieldset>
</form>

<script>
  // Wenn #admin oder ?adminerr / ?admin gesetzt ist: Admin-Details automatisch aufklappen
  (function () {
    try {
      var p = new URLSearchParams(window.location.search);
      if (window.location.hash === "#admin" || p.get("admin") === "1" || p.get("adminerr") === "1") {
        var d = document.getElementById("admin_details");
        if (d) d.open = true;
      }
    } catch (e) {}
  })();
</script>

<script>
  
  // WLAN-Signalstärke nur auf Knopfdruck abfragen (spart nmcli-Rescan beim Laden)
  async function fetchWifiSignal(){
    const out = document.getElementById("wifiSignalOut");
    const btn = document.getElementById("wifiSignalBtn");
    if(btn){ btn.classList.add("btn-disabled"); btn.disabled = true; }
    try{
      const r = await fetch("{{ url_for('api_wifi_signal') }}", { cache: "no-store" });
      const j = await r.json();
      if(out){
        out.textContent = (j && j.signal !== null && j.signal !== undefined) ? (String(j.signal) + "%") : "n/a";
      }
    }catch(e){
      if(out) out.textContent = "n/a";
    }finally{
      if(btn){ btn.classList.remove("btn-disabled"); btn.disabled = false; }
    }
  }

(function () {
    const statusUrl = "{{ url_for('api_wled_status') }}";

    function setStatus(slot, state) {
      const el = document.getElementById("wled_status_" + slot);
      if (!el) return;

      if (state === "off") {
        el.innerHTML = '<span class="dot dot-gray"></span>Aus';
        return;
      }
      if (state === "checking") {
        el.innerHTML = '<span class="dot dot-gray"></span>Prüfe…';
        return;
      }
      if (state === "ok") {
        el.innerHTML = '<span class="dot dot-green"></span>Erreichbar';
        return;
      }
      if (state === "bad") {
        el.innerHTML = '<span class="dot dot-red"></span>Nicht erreichbar';
        return;
      }
      el.innerHTML = '<span class="dot dot-gray"></span>—';
    }

    function refresh() {
      fetch(statusUrl, { cache: "no-store" })
        .then(r => r.json())
        .then(data => {
          (data.bands || []).forEach(b => {
            if (!b.enabled) return setStatus(b.slot, "off");
            if (b.online === true) return setStatus(b.slot, "ok");
            if (b.online === false) return setStatus(b.slot, "bad");
            setStatus(b.slot, "checking");
          });
        })
        .catch(() => {});
    }

    window.addEventListener("load", refresh);

    document.querySelectorAll("input[type=checkbox][name^=wled_enabled_]").forEach(cb => {
      cb.addEventListener("change", async () => {
        const slot = (cb.name || "").split("_").pop();
        if (!slot) return;

        // UI sofort reagieren
        setStatus(slot, cb.checked ? "checking" : "off");
        const cfgBtn = document.getElementById("wled_cfgbtn_" + slot);
        if(cfgBtn){
          if(cb.checked){ cfgBtn.classList.remove("btn-disabled"); }
          else { cfgBtn.classList.add("btn-disabled"); }
        }

        // sofort speichern (ohne extra "Speichern"-Knopf)
        cb.disabled = true;
        try{
          const body = new URLSearchParams();
          body.set("enabled", cb.checked ? "1" : "0");
          const r = await fetch(`/wled/set-enabled/${slot}`, { method:"POST", body });
          const j = await r.json().catch(()=>({ok:false,msg:""}));
          if(!j.ok){
            // rollback UI falls Fehler
            cb.checked = !cb.checked;
            setStatus(slot, cb.checked ? "checking" : "off");
          }
        }catch(e){
          cb.checked = !cb.checked;
          setStatus(slot, cb.checked ? "checking" : "off");
        }finally{
          cb.disabled = false;
          setTimeout(refresh, 250);
        }
      });
    });
  })();
</script>
    </div>

    <!-- NUR FÜR ADMINISTRATOR -->
    <div id="admin"></div>
    <div class="card">
      <details id="admin_details">
        <summary>Admin-Bereich anzeigen</summary>

        {% if adminmsg %}
          <p class="{{ 'msg-ok' if adminok else 'msg-bad' }}" style="margin-top:10px;">{{ adminmsg }}</p>
        {% endif %}

        {% if not admin_unlocked %}
          {% if adminerr %}
            <p class="msg-bad" style="margin-top:10px;">Falsches Admin-Passwort.</p>
          {% endif %}
          <form method="post" action="{{ url_for('admin_unlock') }}" style="margin-top:12px;">
            <label for="admin_password">Admin-Passwort</label>
            <input type="password" id="admin_password" name="admin_password" placeholder="Passwort eingeben" autocomplete="off">
            <div class="btn-row">
              <button type="submit" class="btn btn-primary">Freischalten</button>
            </div>
            <p class="hint">Nur für Einstellungen und Diagnose.</p>
          </form>
        {% else %}

          <form method="post" action="{{ url_for('admin_lock') }}" style="margin-top:12px;">
            <button type="submit" class="btn btn-small">Admin sperren</button>
          </form>

          <hr style="border:none; border-top:1px solid #333; margin:14px 0;">

          
          <h3 style="margin:0 0 8px;">Webpanel Software</h3>
          <p class="hint" style="margin-top:0;">
            Installierte Version: <strong>{{ webpanel_version or 'unbekannt' }}</strong>
          </p>

          <div class="btn-row">
              <form method="post" action="{{ url_for('admin_webpanel_check') }}" style="margin:0;">
                <button type="submit" class="btn">Update prüfen</button>
              </form>

              <form method="post" action="{{ url_for('admin_webpanel_update') }}"
                    onsubmit="return confirm('Webpanel jetzt aktualisieren?\nHinweis: Der Webpanel-Service startet danach neu.');" style="margin:0;">
                <button type="submit" class="btn btn-primary {% if not webpanel_update_available %}btn-disabled{% endif %}">
                  {% if webpanel_update_available %}Update installieren{% else %}Webpanel aktualisieren{% endif %}
                </button>
              </form>
            </div>

            <p class="hint" style="margin-top:8px;">
              {% if webpanel_check.latest %}
                Letzter Check: <strong>{{ webpanel_check.installed or 'unbekannt' }}</strong> → <strong>{{ webpanel_check.latest }}</strong>
                {% if webpanel_update_available %}
                  <span style="color:#ffb347;">(Update verfügbar)</span>
                {% else %}
                  <span style="color:#6be26b;">(aktuell)</span>
                {% endif %}
              {% else %}
                Tipp: Erst „Update prüfen“, dann nur bei Bedarf aktualisieren.
              {% endif %}
            </p>

          {% if webpanel_state.started %}
            <p class="hint" style="margin-top:8px;">
              Letztes Webpanel-Update gestartet: <code>{{ webpanel_state.started }}</code>
            </p>
          {% endif %}

          {% if webpanel_log_tail %}
            <details style="margin-top:10px;">
              <summary>Webpanel Update-Log anzeigen</summary>
              <pre style="background:#0b0b0b; padding:12px; border-radius:10px; border:1px solid #333; overflow:auto; white-space:pre-wrap; margin:10px 0 0;">{{ webpanel_log_tail }}</pre>
            </details>
          {% endif %}

          <hr style="border:none; border-top:1px solid #333; margin:14px 0;">

<h3 style="margin:0 0 8px;">Autodarts Software</h3>
          <p class="hint" style="margin-top:0;">
            Installierte Version: <strong>{{ autodarts_version or 'unbekannt' }}</strong>
          </p>

          <div class="btn-row">
              <form method="post" action="{{ url_for('admin_autodarts_check') }}" style="margin:0;">
                <button type="submit" class="btn">Update prüfen</button>
              </form>

              <form method="post" action="{{ url_for('admin_autodarts_update') }}"
                    onsubmit="return confirm('Autodarts jetzt aktualisieren?');" style="margin:0;">
                <button type="submit" class="btn btn-primary {% if not update_available %}btn-disabled{% endif %}">
                  {% if update_available %}Update installieren{% else %}Autodarts aktualisieren{% endif %}
                </button>
              </form>
            </div>

            <p class="hint" style="margin-top:8px;">
              {% if update_check.latest %}
                Letzter Check: <strong>{{ update_check.installed or 'unbekannt' }}</strong> → <strong>{{ update_check.latest }}</strong>
                {% if update_available %}
                  <span style="color:#ffb347;">(Update verfügbar)</span>
                {% else %}
                  <span style="color:#6be26b;">(aktuell)</span>
                {% endif %}
              {% else %}
                Tipp: Erst „Update prüfen“, dann nur bei Bedarf aktualisieren.
              {% endif %}
            </p>

            <p class="hint" style="margin-top:6px;">
              Das Update nutzt das offizielle Autodarts-Update-Script (<code>updater.sh</code>) und schreibt ein Log.
            </p>

          {% if update_state.started %}
            <p class="hint" style="margin-top:8px;">
              Letztes Update gestartet: <code>{{ update_state.started }}</code>
            </p>
          {% endif %}

          {% if update_log_tail %}
            <details style="margin-top:10px;">
              <summary>Update-Log anzeigen</summary>
              <pre style="background:#0b0b0b; padding:12px; border-radius:10px; border:1px solid #333; overflow:auto; white-space:pre-wrap; margin:10px 0 0;">{{ update_log_tail }}</pre>
            </details>
          {% endif %}

          <hr style="border:none; border-top:1px solid #333; margin:14px 0;">

          <h3 style="margin:0 0 8px;">LED-Bänder – Adressen</h3>
          <p class="hint" style="margin-top:0;">
            Hier stellen Sie die Adresse/Hostname für LED Band 1–3 ein.
          </p>

          <form method="post" action="{{ url_for('wled_save_hosts') }}">
            <label for="wled_host_1">LED Band 1 Adresse/Hostname</label>
            <input type="text" id="wled_host_1" name="wled_host_1" value="{{ wled_hosts[0] }}"
                   {% if not wled_installed %}disabled{% endif %}>

            <label for="wled_host_2">LED Band 2 Adresse/Hostname</label>
            <input type="text" id="wled_host_2" name="wled_host_2" value="{{ wled_hosts[1] }}"
                   {% if not wled_installed %}disabled{% endif %}>

            <label for="wled_host_3">LED Band 3 Adresse/Hostname</label>
            <input type="text" id="wled_host_3" name="wled_host_3" value="{{ wled_hosts[2] }}"
                   {% if not wled_installed %}disabled{% endif %}>

            <div class="btn-row">
              <button type="submit" class="btn btn-primary {% if not wled_installed %}btn-disabled{% endif %}">
                Speichern
              </button>
            </div>
          </form>

          <hr style="border:none; border-top:1px solid #333; margin:14px 0;">




      <p class="hint" style="margin-top:0;">
        <strong>Wichtige Pfade (für Zukunft/Backup):</strong><br>
        <code>{{ usr_local_bin_dir }}</code> → Webdaten + GPIO/Tools (z.B. pi_monitor_test.sh)<br>
        <code>{{ autodarts_data_dir }}</code> → Manual + GPIO Bild (GPIO_Setup.jpeg)<br>
        <code>{{ extensions_dir }}</code> → Extensions (darts-caller, darts-wled)
      </p>

      <hr style="border:none; border-top:1px solid #333; margin:14px 0;">

      <h3 style="margin:0 0 8px;">GPIO Setup</h3>
      {% if admin_gpio_exists %}
        <img src="{{ url_for('admin_gpio_image') }}" alt="GPIO Setup"
             style="width:100%; border-radius:12px; border:1px solid #333; margin-top:10px;">
        <p class="hint" style="margin-top:8px;">
          Bildpfad: <code>{{ autodarts_data_dir }}/GPIO_Setup.jpeg</code>
        </p>
      {% else %}
        <p class="info-msg">GPIO_Setup.jpeg nicht gefunden unter <code>{{ autodarts_data_dir }}</code></p>
      {% endif %}

      <hr style="border:none; border-top:1px solid #333; margin:14px 0;">

      <h3 style="margin:0 0 8px;">Pi Monitor Test / Log</h3>
<div style="margin:10px 0 12px; padding:12px; border:1px solid #333; border-radius:12px; background:#101010;">
  <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:end;">
    <div>
      <label for="piMonInterval">Intervall</label>
      <select id="piMonInterval">
        <option value="5">5s</option>
        <option value="10" selected>10s</option>
        <option value="15">15s</option>
        <option value="30">30s</option>
        <option value="60">60s</option>
      </select>
    </div>
    <div>
      <label for="piMonDuration">Dauer</label>
      <select id="piMonDuration">
        <option value="5">5min</option>
        <option value="10">10min</option>
        <option value="15">15min</option>
        <option value="30" selected>30min</option>
        <option value="60">60min</option>
      </select>
    </div>

    <button id="piMonStartBtn" class="btn">Pi Monitor starten</button>
    <button id="piMonStopBtn" class="btn btn-danger" style="display:none;">Stop</button>
  </div>

  <div id="piMonStatusText" class="hint" style="margin-top:8px;"></div>
</div>

<script>
  (function(){
    const statusEl = document.getElementById('piMonStatusText');
    const startBtn = document.getElementById('piMonStartBtn');
    const stopBtn  = document.getElementById('piMonStopBtn');
    const intervalSel = document.getElementById('piMonInterval');
    const durationSel = document.getElementById('piMonDuration');

    let pollTimer = null;

    function fmtSeconds(s){
      s = Math.max(0, Number(s||0));
      const m = Math.floor(s/60);
      const r = s % 60;
      if(m <= 0) return r + "s";
      return m + "m " + r + "s";
    }

    function setRunningUI(st){
      const running = !!(st && st.running);
      if(running){
        startBtn.classList.add('btn-disabled');
        startBtn.disabled = true;
        stopBtn.style.display = 'inline-block';
        const rem = fmtSeconds(st.remaining_sec || 0);
        statusEl.textContent = (st.msg || "Läuft…") + (st.remaining_sec != null ? (" · Rest: " + rem) : "");
      }else{
        startBtn.classList.remove('btn-disabled');
        startBtn.disabled = false;
        stopBtn.style.display = 'none';
        statusEl.textContent = (st && st.msg) ? st.msg : "Nicht aktiv.";
      }
    }

    async function fetchStatus(){
      try{
        const rs = await fetch('/api/pi_monitor/status');
        const st = await rs.json();
        if(st && st.ok === false){
          setRunningUI({running:false, msg: st.msg || "Fehler"});
          return null;
        }
        setRunningUI(st);
        return st;
      }catch(e){
        setRunningUI({running:false, msg: "Status nicht erreichbar: " + e});
        return null;
      }
    }

    function startPolling(){
      if(pollTimer) return;
      pollTimer = setInterval(async ()=>{
        const st = await fetchStatus();
        if(!st || !st.running){
          clearInterval(pollTimer);
          pollTimer = null;
        }
      }, 5000);
    }

    startBtn.addEventListener('click', async ()=>{
      if(startBtn.disabled) return;
      startBtn.classList.add('btn-disabled');
      startBtn.disabled = true;
      statusEl.textContent = "Starte…";
      const interval_s = parseInt(intervalSel.value, 10);
      const duration_min = parseInt(durationSel.value, 10);
      try{
        const rs = await fetch('/api/pi_monitor/start', {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({interval_s, duration_min})
        });
        const st = await rs.json();
        if(!st.ok){
          setRunningUI({running:false, msg: st.msg || "Start fehlgeschlagen."});
          return;
        }
        setRunningUI(st);
        startPolling();
      }catch(e){
        setRunningUI({running:false, msg:"Start fehlgeschlagen: " + e});
      }
    });

    stopBtn.addEventListener('click', async ()=>{
      stopBtn.classList.add('btn-disabled');
      stopBtn.disabled = true;
      try{
        const rs = await fetch('/api/pi_monitor/stop', {method:'POST'});
        const st = await rs.json();
        stopBtn.classList.remove('btn-disabled');
        stopBtn.disabled = false;
        await fetchStatus();
      }catch(e){
        stopBtn.classList.remove('btn-disabled');
        stopBtn.disabled = false;
        statusEl.textContent = "Stop fehlgeschlagen: " + e;
      }
    });

    // initialer Status aus Server-Render
    const initial = {{ pi_mon_status|tojson }};
    setRunningUI(initial);
    if(initial && initial.running) startPolling();
  })();
</script>

      <pre style="background:#0b0b0b; padding:12px; border-radius:10px; border:1px solid #333; overflow:auto; white-space:pre-wrap; margin:0;">
# 1) Script bearbeiten (falls nötig)
sudo nano /usr/local/bin/pi_monitor_test.sh

# 2) Script ausführbar machen (einmalig)
sudo chmod +x /usr/local/bin/pi_monitor_test.sh

# 3) Test starten (DEFAULT: 30min, alle 5s, CSV wird vorher gelöscht)
sudo /usr/local/bin/pi_monitor_test.sh

# 4) Beispiel: 15min, alle 15s
sudo /usr/local/bin/pi_monitor_test.sh 15 15

# 5) Beispiel: 30min, alle 10s
sudo /usr/local/bin/pi_monitor_test.sh 10 30

# 6) Ergebnis ansehen (letzte 20 Zeilen)
tail -n 20 /var/log/pi_monitor_test.csv

# 7) Erklärung + Summary ansehen
cat /var/log/pi_monitor_test_README.txt

# 8) Datei auf den Desktop/Home kopieren (zum leichter runterladen)
cp /var/log/pi_monitor_test.csv ~/
cp /var/log/pi_monitor_test_README.txt ~/
      </pre>

      <p class="hint" style="margin-top:10px;">
        Log-Dateien:<br>
        Script: <code>{{ pi_monitor_script }}</code><br>
        CSV: <code>{{ pi_monitor_csv }}</code><br>
        README: <code>{{ pi_monitor_readme }}</code>
      </p>

      {% if pi_csv_exists %}
        <h3 style="margin:14px 0 8px;">Letzte 20 Zeilen (CSV)</h3>
        <pre style="background:#0b0b0b; padding:12px; border-radius:10px; border:1px solid #333; overflow:auto; white-space:pre-wrap; margin:0;">{{ pi_csv_tail }}</pre>
      {% else %}
        <p class="hint">CSV existiert aktuell nicht (noch kein Test gelaufen).</p>
      {% endif %}

      <p class="hint" style="margin-top:12px;">
        Extensions Details:<br>
        Caller: <code>{{ extensions_dir }}/darts-caller</code><br>
        LED: <code>{{ extensions_dir }}/darts-wled</code>
      </p>

        {% endif %}
      </details>
    </div>


    <footer>
      Autodarts Installations-Panel · 10.77.0.1 · Mini PC-AP: {{ current_ap_ssid or 'Autodartsinstall1' }}
    </footer>
  </div>

<div id="pingOverlay" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.75); z-index:2000; align-items:center; justify-content:center;">
  <div style="background:#1c1c1c; border:1px solid #333; border-radius:14px; padding:18px 18px; width:min(520px,92vw); box-shadow:0 0 30px rgba(0,0,0,.7);">
    <div style="font-weight:800; margin-bottom:10px;">Verbindungstest läuft…</div>
    <div id="pingOverlayText" style="margin-bottom:10px; opacity:.9;">Starte…</div>
    <div style="height:10px; background:#0b0b0b; border:1px solid #333; border-radius:999px; overflow:hidden;">
      <div id="pingOverlayBar" style="height:100%; width:0%; background:#3b82f6;"></div>
    </div>
    <div class="hint" style="margin-top:10px;">Bitte warten – das dauert ca. 30 Sekunden.</div>
  </div>
</div>

<script>
(function(){
  const btn = document.getElementById('pingBtn');
  const overlay = document.getElementById('pingOverlay');
  const txt = document.getElementById('pingOverlayText');
  const bar = document.getElementById('pingOverlayBar');
  const out = document.getElementById('pingResult');

  function showOverlay(){ if(overlay){ overlay.style.display='flex'; } }
  function hideOverlay(){ if(overlay){ overlay.style.display='none'; } }
  function setProgress(done, total){
    const pct = total ? Math.max(0, Math.min(100, Math.round((done/total)*100))) : 0;
    if(bar) bar.style.width = pct + '%';
  }

  async function start(){
    if(!btn || btn.classList.contains('btn-disabled')) return;
    btn.classList.add('btn-disabled');
    if(out) out.textContent = '';
    showOverlay();
    if(txt) txt.textContent = 'Starte…';

    try{
      const r = await fetch('/wifi/ping/start', {method:'POST'});
      const j = await r.json();
      if(!j.ok){
        hideOverlay();
        if(out) out.textContent = j.msg || 'Ping konnte nicht gestartet werden.';
        btn.classList.remove('btn-disabled');
        return;
      }
      const jobId = j.job_id;
      const total = 30;
      let tries = 0;

      const timer = setInterval(async ()=>{
        tries += 1;
        try{
          const rs = await fetch('/wifi/ping/status/' + jobId);
          const s = await rs.json();
          if(!s.ok){
            clearInterval(timer);
            hideOverlay();
            if(out) out.textContent = s.msg || 'Fehler beim Status.';
            btn.classList.remove('btn-disabled');
            return;
          }

          const prog = Number(s.progress||0);
          const recv = Number(s.received||0);
          if(txt) txt.textContent = `${prog} von ${total} Paketen… (empfangen: ${recv})`;
          setProgress(prog, total);

          if(s.done){
            clearInterval(timer);
            hideOverlay();
            const sent = total;
            const rec = recv;
            let result = `${rec} von ${sent} Paketen wurden erfolgreich gesendet.`;
            if(s.min_ms!=null && s.max_ms!=null && s.avg_ms!=null){
              result += ` Schnellstes: ${s.min_ms} ms · Langsamstes: ${s.max_ms} ms · Durchschnitt: ${s.avg_ms} ms`;
            }
            if(s.error){
              result += ` (Hinweis: ${s.error})`;
            }
            if(out) out.textContent = result;
            btn.classList.remove('btn-disabled');
          }
        }catch(e){
          if(tries > 120){
            clearInterval(timer);
            hideOverlay();
            if(out) out.textContent = 'Verbindungstest abgebrochen (Timeout).';
            btn.classList.remove('btn-disabled');
          }
        }
      }, 600);

    }catch(e){
      hideOverlay();
      if(out) out.textContent = 'Verbindungstest fehlgeschlagen.';
      btn.classList.remove('btn-disabled');
    }
  }

  if(btn){ btn.addEventListener('click', start); }
})();
</script>

</body>
</html>
"""
    return render_template_string(
        html,
        darts_url=darts_url,
        desired_cams=desired_cams,
        max_cams=MAX_CAMERAS,
        cam_count_options=cam_count_options,
        cam_devices_enum=cam_devices_enum,
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
        admin_unlocked=admin_unlocked,
        adminerr=adminerr,
        adminmsg=adminmsg,
        adminok=adminok,
        update_state=update_state,
        update_log_tail=update_log_tail,
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
        usr_local_bin_dir=USR_LOCAL_BIN_DIR,
        autodarts_data_dir=AUTODARTS_DATA_DIR,
        extensions_dir=EXTENSIONS_DIR,
        autodarts_active=autodarts_active,
        autodarts_version=autodarts_version,
        camera_mode=camera_mode,
        autodarts_notice=ad_restarted,
        wifi_ok=wifi_ok,
        dongle_ok=dongle_ok,
        current_ap_ssid=current_ap_ssid,
        ssid=ssid,
        ip=ip,
        wifi_signal=wifi_signal,
        msg=msg,
        autoupdate_enabled=autoupdate_enabled,
        update_check=update_check,
        update_available=update_available,
        wifi_interface=WIFI_INTERFACE,
        webpanel_version=webpanel_version,
        webpanel_check=webpanel_check,
        webpanel_update_available=webpanel_update_available,
        webpanel_state=webpanel_state,
        webpanel_log_tail=webpanel_log_tail,
    )


@app.route("/led/save", methods=["POST"])
def led_save():
    ad_email = request.form.get("ad_email", "").strip()
    ad_password = request.form.get("ad_password", "").strip()
    ad_board = request.form.get("ad_board", "").strip()

    cur_email, cur_pw, cur_board, exists, err = read_darts_caller_credentials()
    if not exists:
        return redirect(url_for("index", ledcheck="bad", ledmsg="start-custom.sh nicht gefunden."))

    # leer lassen => unverändert
    if not ad_email:
        ad_email = cur_email
    if not ad_password:
        ad_password = cur_pw
    if not ad_board:
        ad_board = cur_board

    try:
        write_darts_caller_credentials(ad_email, ad_password, ad_board)
        return redirect(url_for("index", ledcheck="ok", ledmsg="Gespeichert (start-custom.sh aktualisiert)."))
    except Exception as e:
        return redirect(url_for("index", ledcheck="bad", ledmsg=f"Speichern fehlgeschlagen: {e}"))


@app.route("/led/check", methods=["POST"])
def led_check():
    email, pw, bid, exists, err = read_darts_caller_credentials()
    if not exists:
        return redirect(url_for("index", ledcheck="bad", ledmsg="start-custom.sh nicht gefunden."))
    if err:
        return redirect(url_for("index", ledcheck="bad", ledmsg=err))
    if not email or not pw or not bid:
        return redirect(url_for("index", ledcheck="bad", ledmsg="Bitte Account/Passwort/Board-ID setzen. (2FA muss AUS sein)"))
    return redirect(url_for("index", ledcheck="ok", ledmsg="Daten vorhanden. Hinweis: 2FA muss AUS sein."))


@app.route("/wled-open", methods=["GET"])
def wled_open():
    # Slot 1 öffnen
    return redirect(url_for("wled_open_slot", slot=1))


@app.route("/wled/open/<int:slot>", methods=["GET"])
def wled_open_slot(slot: int):
    cfg = load_wled_config()
    if not bool(cfg.get("master_enabled", True)):
        return (
            "<html><body style='font-family:system-ui;background:#111;color:#eee;padding:20px'>"
            "<h1>WLED deaktiviert</h1>"
            "<p>WLED wurde in der Weboberfläche deaktiviert.</p>"
            f"<p><a style='color:#3b82f6' href='{url_for('index')}'>Zurück</a></p>"
            "</body></html>",
            200,
        )

    targets = cfg.get("targets", [])
    if slot < 1 or slot > len(targets):
        return (
            "<html><body style='font-family:system-ui;background:#111;color:#eee;padding:20px'>"
            "<h1>Ungültiger WLED Slot</h1>"
            f"<p>Slot {slot} existiert nicht.</p>"
            f"<p><a style='color:#3b82f6' href='{url_for('index')}'>Zurück</a></p>"
            "</body></html>",
            404,
        )

    host = str(targets[slot - 1].get("host", "")).strip()
    slot_enabled = bool(targets[slot - 1].get("enabled", False))
    if not slot_enabled:
        return (
            "<html><body style='font-family:system-ui;background:#111;color:#eee;padding:20px'>"
            "<h1>WLED Slot deaktiviert</h1>"
            f"<p>Slot {slot} ist aktuell nicht aktiviert.</p>"
            f"<p><a style='color:#3b82f6' href='{url_for('index')}'>Zurück</a></p>"
            "</body></html>",
            200,
        )

    if not host:
        return (
            "<html><body style='font-family:system-ui;background:#111;color:#eee;padding:20px'>"
            "<h1>Kein WLED eingetragen</h1>"
            f"<p>Für Slot {slot} wurde noch kein Hostname/IP eingetragen.</p>"
            f"<p><a style='color:#3b82f6' href='{url_for('index')}'>Zurück</a></p>"
            "</body></html>",
            400,
        )

    ok, ip = is_http_reachable(host, timeout_s=0.8)
    if not ok:
        return (
            "<html><body style='font-family:system-ui;background:#111;color:#eee;padding:20px'>"
            "<h1>WLED nicht erreichbar</h1>"
            "<p>Sie haben kein offizielles LED Band mit Controller im Einsatz, oder der Controller ist aktuell nicht verbunden.</p>"
            f"<p>Host: <code>{host}</code></p>"
            f"<p><a style='color:#3b82f6' href='{url_for('index')}'>Zurück</a></p>"
            "</body></html>",
            503,
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
    msg_parts = ["WLED Targets gespeichert."]
    ok = True

    if service_exists(DARTS_WLED_SERVICE):
        master = bool(cfg.get("master_enabled", True))
        hosts = get_enabled_wled_hosts(cfg) if master else []

        if (not master) or (not hosts):
            service_disable_now(DARTS_WLED_SERVICE)
            if not master:
                msg_parts.append("WLED ist deaktiviert → darts-wled wurde gestoppt.")
            else:
                msg_parts.append("Kein Target aktiv → darts-wled wurde gestoppt.")
        else:
            ok_weps, msg_weps = update_darts_wled_start_custom_weps(hosts)
            msg_parts.append(msg_weps)
            if ok_weps:
                service_enable_now(DARTS_WLED_SERVICE)
                service_restart(DARTS_WLED_SERVICE)
            else:
                ok = False

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
    if service_exists(DARTS_WLED_SERVICE):
        if not new_master:
            service_disable_now(DARTS_WLED_SERVICE)
            msg_parts.append("WLED deaktiviert (merkt sich das nach Neustart). darts-wled wurde gestoppt.")
        else:
            hosts = get_enabled_wled_hosts(cfg)
            if hosts:
                ok_weps, msg_weps = update_darts_wled_start_custom_weps(hosts)
                msg_parts.append("WLED aktiviert (merkt sich das nach Neustart).")
                msg_parts.append(msg_weps)
                if ok_weps:
                    service_enable_now(DARTS_WLED_SERVICE)
                    service_restart(DARTS_WLED_SERVICE)
                else:
                    ok = False
            else:
                service_disable_now(DARTS_WLED_SERVICE)
                msg_parts.append("WLED aktiviert, aber kein Target aktiv → darts-wled bleibt aus.")
    else:
        msg_parts.append("WLED Toggle gespeichert (Service nicht gefunden).")

    return redirect(url_for("index", ledcheck=("ok" if ok else "bad"), ledmsg="\n".join(msg_parts)))




@app.route("/wled/set-enabled/<int:slot>", methods=["POST"])
def wled_set_enabled(slot: int):
    # User-UI: Slot 1..3
    if slot < 1 or slot > 3:
        return jsonify({"ok": False, "msg": "Ungültiger Slot."}), 400

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
    if service_exists(DARTS_WLED_SERVICE):
        hosts = get_enabled_wled_hosts(cfg)
        if hosts:
            update_darts_wled_start_custom_weps(hosts)
            service_enable_now(DARTS_WLED_SERVICE)
        else:
            service_disable_now(DARTS_WLED_SERVICE)

    return jsonify({"ok": True})


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
    if service_exists(DARTS_WLED_SERVICE):
        hosts = get_enabled_wled_hosts(cfg)
        if hosts:
            update_darts_wled_start_custom_weps(hosts)
            service_enable_now(DARTS_WLED_SERVICE)
        else:
            service_disable_now(DARTS_WLED_SERVICE)

    return redirect(url_for("index"))


@app.route("/wled/save-hosts", methods=["POST"])
def wled_save_hosts():
    if not bool(session.get("admin_unlocked", False)):
        return redirect(url_for("index", adminerr="1") + "#admin")

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
    if service_exists(DARTS_WLED_SERVICE):
        hosts = get_enabled_wled_hosts(cfg)
        if hosts:
            update_darts_wled_start_custom_weps(hosts)
            service_enable_now(DARTS_WLED_SERVICE)
        else:
            service_disable_now(DARTS_WLED_SERVICE)

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



@app.route("/api/wifi/signal", methods=["GET"])
def api_wifi_signal():
    """Signalstärke (0..100) des aktuellen WLANs – nur auf Knopfdruck."""
    now = time.time()
    try:
        if (now - float(WIFI_SIGNAL_CACHE.get('ts', 0.0))) < WIFI_SIGNAL_CACHE_TTL_SEC:
            return jsonify({"signal": WIFI_SIGNAL_CACHE.get('v')})
    except Exception:
        pass

    sig = get_wifi_signal_percent()
    try:
        WIFI_SIGNAL_CACHE['ts'] = now
        WIFI_SIGNAL_CACHE['v'] = sig
    except Exception:
        pass
    iface = _get_default_route_interface() or _get_connected_wifi_interface(prefer=WIFI_INTERFACE if WIFI_INTERFACE else None) or WIFI_INTERFACE
    if iface == AP_INTERFACE:
        iface = WIFI_INTERFACE
    return jsonify({"signal": sig, "iface": iface})

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
    return jsonify({"bands": bands})


# === Pi Monitor Test API (Admin) ===
@app.route("/api/pi_monitor/status", methods=["GET"])
def api_pi_monitor_status():
    if not bool(session.get("admin_unlocked", False)):
        return jsonify({"ok": False, "msg": "Admin gesperrt."}), 403
    st = get_pi_monitor_status()
    st["ok"] = True
    return jsonify(st)


@app.route("/api/pi_monitor/start", methods=["POST"])
def api_pi_monitor_start():
    if not bool(session.get("admin_unlocked", False)):
        return jsonify({"ok": False, "msg": "Admin gesperrt."}), 403
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
        return jsonify({"ok": False, "msg": "Admin gesperrt."}), 403
    res = stop_pi_monitor()
    if not res.get("ok"):
        return jsonify(res), 400
    return jsonify(res)

@app.route("/admin/unlock", methods=["POST"])
def admin_unlock():
    pw = (request.form.get("admin_password") or "").strip()
    if pw == ADMIN_PASSWORD:
        session["admin_unlocked"] = True
        return redirect(url_for("index", admin="1") + "#admin")
    session.pop("admin_unlocked", None)
    return redirect(url_for("index", adminerr="1") + "#admin")


@app.route("/admin/lock", methods=["POST"])
def admin_lock():
    session.pop("admin_unlocked", None)
    return redirect(url_for("index") + "#admin")


@app.route("/admin/autodarts/check", methods=["POST"])
def admin_autodarts_check():
    if not bool(session.get("admin_unlocked", False)):
        return ("Forbidden", 403)

    installed = get_autodarts_version()
    latest = fetch_latest_autodarts_version()
    channel = _get_updater_channel()
    data = {
        "ts": int(time.time()),
        "installed": installed,
        "latest": latest,
        "channel": channel,
    }
    save_update_check(data)

    if installed and latest:
        if installed == latest:
            msg = f"Kein Update verfügbar (bereits v{installed})."
        else:
            msg = f"Update verfügbar: v{installed} → v{latest}."
    else:
        msg = "Update-Check nicht möglich (Version oder Internet nicht verfügbar)."

    return redirect(url_for("index", admin="1", adminmsg=msg, adminok="1") + "#admin")


@app.route("/admin/autodarts/update", methods=["POST"])
def admin_autodarts_update():
    if not bool(session.get("admin_unlocked", False)):
        return ("Forbidden", 403)

    installed = get_autodarts_version()
    latest = fetch_latest_autodarts_version()

    # Wenn wir sicher wissen, dass es kein Update gibt → nicht starten
    if installed and latest and installed == latest:
        msg = f"Kein Update verfügbar (bereits v{installed})."
        return redirect(url_for("index", admin="1", adminok="1", adminmsg=msg) + "#admin")

    ok, msg = start_autodarts_update_background()
    return redirect(url_for("index", admin="1", adminok=("1" if ok else "0"), adminmsg=msg) + "#admin")



@app.route("/admin/gpio-image", methods=["GET"])
def admin_gpio_image():
    if not bool(session.get("admin_unlocked", False)):
        return ("Forbidden", 403)

    if os.path.exists(ADMIN_GPIO_IMAGE):
        return send_file(ADMIN_GPIO_IMAGE, mimetype="image/jpeg")
    return (
        "<html><body style='font-family:system-ui;background:#111;color:#eee;padding:20px'>"
        "<h1>GPIO Bild nicht gefunden</h1>"
        f"<p>Datei fehlt: <code>{ADMIN_GPIO_IMAGE}</code></p>"
        f"<p><a style='color:#3b82f6' href='{url_for('index')}'>Zurück</a></p>"
        "</body></html>",
        404,
    )


@app.route("/set_cams", methods=["POST"])
def set_cams():
    raw = request.form.get("cam_count", "3")
    try:
        desired = int(raw)
    except ValueError:
        desired = 3

    desired = max(1, min(MAX_CAMERAS, desired))
    devices = detect_cameras(desired)

    cfg = load_cam_config()
    cfg["desired_cams"] = desired
    cfg["devices"] = devices
    save_cam_config(cfg)

    return redirect(url_for("index"))


@app.route("/camera-mode/start", methods=["POST"])
def camera_mode_start():
    """Kamera-Einstellung starten: Autodarts stoppen, Streams stoppen, Flag setzen."""
    subprocess.run(["systemctl", "stop", AUTODARTS_SERVICE], capture_output=True, text=True)
    subprocess.run(["pkill", "-f", "mjpg_streamer"], capture_output=True, text=True)

    cfg = load_cam_config()
    cfg["camera_mode"] = True
    save_cam_config(cfg)

    return redirect(url_for("index"))


@app.route("/camera-mode/end", methods=["POST"])
def camera_mode_end():
    """Kamera-Einstellung beenden: Streams stoppen, Autodarts neu starten, Flag zurücksetzen."""
    subprocess.run(["pkill", "-f", "mjpg_streamer"], capture_output=True, text=True)
    subprocess.run(["systemctl", "restart", AUTODARTS_SERVICE], capture_output=True, text=True)

    cfg = load_cam_config()
    cfg["camera_mode"] = False
    save_cam_config(cfg)

    return redirect(url_for("index", ad_restarted=1))


@app.route("/wifi", methods=["GET", "POST"])
def wifi():
    message = ""
    success = False

    if request.method == "POST":
        ssid = request.form.get("ssid", "").strip()
        password = request.form.get("password", "").strip()

        if not ssid:
            message = "Bitte WLAN-Namen (SSID) eingeben."
        else:
            # 1) Nur den WLAN-USB-Dongle weich zurücksetzen
            soft_reset_wifi_dongle()

            # 2) Alte Verbindung löschen (wenn vorhanden) – Fehler ignorieren
            subprocess.run(
                ["nmcli", "connection", "delete", WIFI_CONNECTION_NAME],
                capture_output=True,
                text=True,
            )

            # 3) Neue Verbindung anlegen (mit unserem WLAN-Stick als Interface)
            add = subprocess.run(
                [
                    "nmcli",
                    "connection",
                    "add",
                    "type",
                    "wifi",
                    "ifname",
                    WIFI_INTERFACE,
                    "con-name",
                    WIFI_CONNECTION_NAME,
                    "ssid",
                    ssid,
                ],
                capture_output=True,
                text=True,
            )

            if add.returncode != 0:
                message = "Fehler beim Anlegen der WLAN-Verbindung: " + interpret_nmcli_error(add.stdout, add.stderr)
            else:
                # 4) Passwort + IP-Konfiguration setzen
                subprocess.run(
                    [
                        "nmcli",
                        "connection",
                        "modify",
                        WIFI_CONNECTION_NAME,
                        "wifi-sec.key-mgmt",
                        "wpa-psk",
                        "wifi-sec.psk",
                        password,
                    ],
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    [
                        "nmcli",
                        "connection",
                        "modify",
                        WIFI_CONNECTION_NAME,
                        "ipv4.method",
                        "auto",
                        "ipv6.method",
                        "ignore",
                    ],
                    capture_output=True,
                    text=True,
                )

                # 5) Erster Verbindungsversuch
                up = subprocess.run(
                    ["nmcli", "connection", "up", WIFI_CONNECTION_NAME],
                    capture_output=True,
                    text=True,
                )

                if up.returncode == 0:
                    message = "Erfolgreich mit WLAN verbunden."
                    success = True
                else:
                    err_text_full = (up.stderr or up.stdout or "")
                    err_lower = err_text_full.lower()

                    device_error = (
                        "no suitable device" in err_lower
                        or "no wifi device" in err_lower
                        or "no device" in err_lower
                        or "device not available because profile is not compatible" in err_lower
                        or "profile is not compatible with device" in err_lower
                        or "mismatching interface name" in err_lower
                    )

                    if device_error:
                        soft_reset_wifi_dongle()
                        up2 = subprocess.run(
                            ["nmcli", "connection", "up", WIFI_CONNECTION_NAME],
                            capture_output=True,
                            text=True,
                        )

                        if up2.returncode == 0:
                            message = (
                                "Verbindung fehlgeschlagen, wird erneut versucht ...\n"
                                "Der zweite Versuch war erfolgreich. "
                                "(Hinweis: WLAN-USB-Stick wurde kurz neu initialisiert.)"
                            )
                            success = True
                        else:
                            message = (
                                "Verbindung fehlgeschlagen, wird erneut versucht ...\n"
                                "Auch der zweite Versuch ist fehlgeschlagen: "
                                + interpret_nmcli_error(up2.stdout, up2.stderr)
                            )
                    else:
                        message = "Verbindung konnte nicht hergestellt werden: " + interpret_nmcli_error(up.stdout, up.stderr)

    # Aktuellen Status des WLAN-Dongles anzeigen
    ssid_cur, ip_cur = get_wifi_status()
    wifi_signal = get_wifi_signal_percent()
    if ssid_cur and ip_cur:
        current_info = f"Aktuell verbunden mit <strong>{ssid_cur}</strong> (IP {ip_cur})" + (f" · Signal: <strong>{wifi_signal}%</strong>." if wifi_signal is not None else "." )
    elif ssid_cur and not ip_cur:
        current_info = f"WLAN verbunden mit <strong>{ssid_cur}</strong>, aber es wurde keine IPv4-Adresse vergeben." + (f" (Signal: <strong>{wifi_signal}%</strong>)" if wifi_signal is not None else "")
    else:
        current_info = "Der USB-Dongle ist aktuell mit keinem WLAN verbunden."

    html = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>WLAN Konfiguration</title>
  <style>
    body { font-family: system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:#111; color:#eee; margin:0; padding:0; }
    .container { max-width: 700px; margin: 0 auto; padding: 24px 16px 60px; }
    h1 { font-size: 1.6rem; margin-bottom: 8px; }
    .card { background:#1c1c1c; border-radius:12px; padding:16px 18px 18px; margin-bottom:18px; box-shadow:0 0 20px rgba(0,0,0,0.4); }
    label { font-size:0.9rem; display:block; margin-bottom:4px; }
    input[type=text], input[type=password] { width:100%; padding:8px; border-radius:8px; border:1px solid #444; background:#111; color:#eee; margin-bottom:10px; font-family:inherit; }
    .btn-row { display:flex; gap:10px; margin-top:10px; flex-wrap:wrap; }
    .btn { display:inline-block; padding:10px 16px; border-radius:999px; border:1px solid #444; background:#2a2a2a; color:#eee; text-decoration:none; font-size:0.95rem; cursor:pointer; }
    .btn-primary { background:#3b82f6; border-color:#3b82f6; color:white; }
    .msg-ok { color:#6be26b; margin-bottom:8px; white-space:pre-line; }
    .msg-bad { color:#ff6b6b; margin-bottom:8px; white-space:pre-line; }
    .hint { font-size:0.8rem; opacity:0.7; margin-top:4px; }
    code { background:#0b0b0b; padding:2px 6px; border-radius:6px; border:1px solid #333; }
    .dot { display:inline-block; width:10px; height:10px; border-radius:999px; margin-right:6px; vertical-align:middle; }
    .dot-green { background:#6be26b; }
    .dot-red { background:#ff6b6b; }
    .dot-gray { background:#9ca3af; }
    details { border:1px solid #333; border-radius:12px; padding:12px 14px; background:#171717; }
    details summary { cursor:pointer; list-style:none; user-select:none; font-weight:600; }
    details summary::-webkit-details-marker { display:none; }
    .admin-hint { font-size:0.8rem; opacity:0.75; margin-top:6px; }

  </style>
</head>
<body>
  <div class="container">
    <h1>WLAN Konfiguration (USB-Dongle)</h1>

    <div class="card">
      <p>{{ current_info | safe }}</p>

      {% if message %}
        <p class="{{ 'msg-ok' if success else 'msg-bad' }}">{{ message }}</p>
      {% endif %}

      <form method="post">
        <label for="ssid">WLAN Name (SSID)</label>
        <input type="text" id="ssid" name="ssid" required>

        <label for="password">WLAN Passwort</label>
        <input type="password" id="password" name="password">

        <p class="hint">
          Für Profis: Interface = <code>{{ wifi_interface }}</code>,
          Verbindung = <code>{{ wifi_connection_name }}</code><br>
          Hinweis: Bei bestimmten Gerätefehlern wird die Verbindung
          automatisch ein zweites Mal versucht (inkl. kurzem WLAN-Dongle-Reset).
        </p>

        <div class="btn-row">
          <button type="submit" class="btn btn-primary">Verbinden</button>
          <a href="{{ url_for('index') }}" class="btn">Zurück</a>
        </div>
      </form>
    </div>
  </div>
</body>
</html>
"""
    return render_template_string(
        html,
        message=message,
        success=success,
        current_info=current_info,
        wifi_interface=WIFI_INTERFACE,
        wifi_connection_name=WIFI_CONNECTION_NAME,
    )




@app.route("/autoupdate/toggle", methods=["POST"])
def autoupdate_toggle():
    cur = autodarts_autoupdate_is_enabled()
    if cur is None:
        return redirect(url_for("index", msg="Auto-Update Service nicht gefunden."))

    ok, msg = autodarts_set_autoupdate(not bool(cur))
    return redirect(url_for("index", msg=msg))


@app.route("/wifi/ping/start", methods=["POST"])
def wifi_ping_start():
    ok, msg, job_id = start_ping_test(count=30)
    return jsonify({"ok": bool(ok), "msg": msg, "job_id": job_id})


@app.route("/wifi/ping/status/<job_id>", methods=["GET"])
def wifi_ping_status(job_id: str):
    job = PING_JOBS.get(job_id)
    if not job:
        return jsonify({"ok": False, "msg": "Job nicht gefunden."}), 404

    # Auto-cleanup nach 15min
    try:
        if time.time() - float(job.get("started", 0)) > 900:
            PING_JOBS.pop(job_id, None)
    except Exception:
        pass

    return jsonify({
        "ok": True,
        "target": job.get("target"),
        "count": job.get("count", 30),
        "progress": job.get("progress", 0),
        "received": job.get("received", 0),
        "done": bool(job.get("done", False)),
        "min_ms": job.get("min_ms"),
        "max_ms": job.get("max_ms"),
        "avg_ms": job.get("avg_ms"),
        "error": job.get("error"),
    })
@app.route("/ap", methods=["GET", "POST"])
def ap_config():
    message = ""
    success = False
    current_ssid = get_ap_ssid()
    ap_choices = AP_SSID_CHOICES
    selected_ssid = current_ssid if (current_ssid in ap_choices) else (ap_choices[0] if ap_choices else "")

    if request.method == "POST":
        new_ssid = (request.form.get("ap_ssid_select") or "").strip()

        if not new_ssid:
            message = "Bitte einen Access-Point-Namen auswählen."
        elif new_ssid not in ap_choices:
            message = "Ungültige Auswahl. Bitte einen Namen aus der Liste wählen."
        elif len(new_ssid) > 32:
            message = "Der Access-Point-Name ist zu lang (max. 32 Zeichen)."
        else:
            res = subprocess.run(
                ["nmcli", "connection", "modify", AP_CONNECTION_NAME, "802-11-wireless.ssid", new_ssid],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                message = "Fehler beim Ändern des Access-Point-Namens: " + interpret_nmcli_error(res.stdout, res.stderr)
            else:
                subprocess.run(["nmcli", "connection", "down", AP_CONNECTION_NAME], capture_output=True, text=True)
                subprocess.run(["nmcli", "connection", "up", AP_CONNECTION_NAME], capture_output=True, text=True)
                success = True
                current_ssid = new_ssid
                message = f"Access-Point-Name wurde geändert auf „{new_ssid}“."

    html = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Access-Point auswählen</title>
  <style>
    body { font-family: system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:#111; color:#eee; margin:0; padding:0; }
    .container { max-width: 700px; margin: 0 auto; padding: 24px 16px 60px; }
    h1 { font-size: 1.6rem; margin-bottom: 8px; }
    .card { background:#1c1c1c; border-radius:12px; padding:16px 18px 18px; margin-bottom:18px; box-shadow:0 0 20px rgba(0,0,0,0.4); }
    label { font-size:0.9rem; display:block; margin-bottom:4px; }
    input[type=text], select { width:100%; padding:8px; border-radius:8px; border:1px solid #444; background:#111; color:#eee; margin-bottom:10px; font-family:inherit; }
    .btn-row { display:flex; gap:10px; margin-top:10px; flex-wrap:wrap; }
    .btn { display:inline-block; padding:10px 16px; border-radius:999px; border:1px solid #444; background:#2a2a2a; color:#eee; text-decoration:none; font-size:0.95rem; cursor:pointer; }
    .btn-primary { background:#3b82f6; border-color:#3b82f6; color:white; }
    .msg-ok { color:#6be26b; margin-bottom:8px; }
    .msg-bad { color:#ff6b6b; margin-bottom:8px; }
    .hint { font-size:0.8rem; opacity:0.7; margin-top:4px; }
    code { background:#0b0b0b; padding:2px 6px; border-radius:6px; border:1px solid #333; }
    .dot { display:inline-block; width:10px; height:10px; border-radius:999px; margin-right:6px; vertical-align:middle; }
    .dot-green { background:#6be26b; }
    .dot-red { background:#ff6b6b; }
    .dot-gray { background:#9ca3af; }
    details { border:1px solid #333; border-radius:12px; padding:12px 14px; background:#171717; }
    details summary { cursor:pointer; list-style:none; user-select:none; font-weight:600; }
    details summary::-webkit-details-marker { display:none; }
    .admin-hint { font-size:0.8rem; opacity:0.75; margin-top:6px; }

  </style>
</head>
<body>
  <div class="container">
    <h1>Access-Point (Mini PC-WLAN) auswählen</h1>

    <div class="card">
      {% if current_ssid %}
        <p>Aktueller Access-Point-Name (SSID): <strong>{{ current_ssid }}</strong></p>
      {% else %}
        <p>Der Access-Point <code>{{ ap_connection_name }}</code> wurde nicht gefunden.
           Bitte prüfen, ob das Access-Point-Setup korrekt installiert ist.</p>
      {% endif %}

      {% if message %}
        <p class="{{ 'msg-ok' if success else 'msg-bad' }}">{{ message }}</p>
      {% endif %}

      <form method="post">
        <label for="ap_ssid_select">Neuer Name für das Mini PC-WLAN</label>
        <select id="ap_ssid_select" name="ap_ssid_select" required>
          {% for opt in ap_choices %}
            <option value="{{ opt }}" {% if opt == selected_ssid %}selected{% endif %}>{{ opt }}</option>
          {% endfor %}
        </select>

        <p class="hint">
          Hinweis: Nach dem Speichern müssen Sie sich mit Ihren Geräten mit dem neuen WLAN-Namen verbinden
          (Access-Point des Mini PC).
        </p>

        <div class="btn-row">
          <button type="submit" class="btn btn-primary">Speichern</button>
          <a href="{{ url_for('index') }}" class="btn">Zurück</a>
        </div>
      </form>
    </div>
  </div>
</body>
</html>
"""
    return render_template_string(
        html,
        message=message,
        success=success,
        current_ssid=current_ssid,
        ap_connection_name=AP_CONNECTION_NAME,
        ap_choices=ap_choices,
        selected_ssid=selected_ssid,
    )



@app.route("/cam/<int:cam_id>", methods=["GET"])
def cam_view(cam_id: int):
    """
    Live-View einer Kamera:
      - nur möglich, wenn Kamera-Einstellung aktiv ist
      - startet genau EINEN mjpg_streamer (alle anderen werden beendet)
    """
    cam_config = load_cam_config()
    camera_mode = bool(cam_config.get("camera_mode", False))
    devices = cam_config.get("devices", [])
    cam_count = len(devices)

    if not camera_mode:
        return (
            "<html><body style='font-family:system-ui;background:#111;color:#eee;padding:20px'>"
            "<h1>Kamera-Einstellung ist nicht aktiv</h1>"
            "<p>Bitte gehen Sie zurück und klicken Sie zuerst auf <strong>Kamera einstellen</strong>.</p>"
            f"<p><a style='color:#3b82f6' href='{url_for('index')}'>Zurück</a></p>"
            "</body></html>",
            400,
        )

    if cam_count == 0 or cam_id < 1 or cam_id > cam_count:
        return (
            f"<html><body><h1>Kamera {cam_id} nicht verfügbar</h1>"
            f"<p>Es sind aktuell {cam_count} Kamera(s) konfiguriert.</p>"
            f"<p>Bitte gehen Sie zurück zur Hauptseite und führen Sie die Kamera-Erkennung erneut aus.</p>"
            f"<p><a href='{url_for('index')}'>Zurück</a></p>"
            f"</body></html>",
            404,
        )

    dev = devices[cam_id - 1]

    # Autodarts stoppen, damit nichts die Kamera blockiert
    # Alle mjpg_streamer beenden
    subprocess.run(["pkill", "-f", "mjpg_streamer"], capture_output=True, text=True)

    port = STREAM_BASE_PORT + (cam_id - 1)

    # Probe device capabilities (hilft bei "gefunden, aber kein Stream")
    probe = probe_v4l2_device(dev)
    preferred_formats = ["MJPG", "YUYV"]
    fmt, res = _best_resolution_for_formats(probe.get("resolutions", {}) if isinstance(probe, dict) else {}, preferred_formats)

    force_yuyv = (fmt == "YUYV")

    input_args = f"input_uvc.so -d {dev}"
    if res:
        input_args += f" -r {res}"
    if force_yuyv:
        # Einige Kameras liefern kein MJPG, sondern nur YUYV – mjpg_streamer kann das mit -y.
        input_args += " -y"

    log_path = f"/var/log/autodarts_mjpg_streamer_cam{cam_id}.log"
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
    except Exception:
        pass

    try:
        with open(log_path, "a", encoding="utf-8") as logf:
            logf.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} cam{cam_id} dev={dev} args={input_args} ---\n")
            p = subprocess.Popen(
                [
                    "mjpg_streamer",
                    "-i",
                    input_args,
                    "-o",
                    f"output_http.so -p {port}",
                ],
                stdout=logf,
                stderr=logf,
            )

        # kurzer Healthcheck: wenn Prozess sofort stirbt, zeigen wir das Log an
        time.sleep(0.3)
        if p.poll() is not None:
            tail = tail_file(log_path, n=120, max_chars=7000)
            fmts = ""
            hint = ""
            if probe.get("ok"):
                fmts = ", ".join(sorted(list(probe.get("formats") or [])))
                if fmts:
                    fmts = f"<p><strong>Erkannte Formate:</strong> {fmts}</p>"
                if probe.get("formats") and ("MJPG" not in probe["formats"]) and ("YUYV" not in probe["formats"]):
                    hint = (
                        "<p style='color:#ffb347'><strong>Hinweis:</strong> Die Kamera bietet kein MJPG/YUYV an "
                        "(z.B. nur H264). In diesem Fall kann mjpg_streamer oft keinen MJPEG-Stream erzeugen.</p>"
                    )
            else:
                err = (probe.get("error") or "").strip()
                if err:
                    fmts = f"<p><strong>v4l2 Probe-Fehler:</strong> {err}</p>"

            return (
                "<html><body style='font-family:system-ui;background:#111;color:#eee;padding:20px'>"
                "<h1>Fehler: Kamera-Stream konnte nicht gestartet werden</h1>"
                f"<p>Device: <code>{dev}</code></p>"
                f"<p>mjpg_streamer Input: <code>{input_args}</code></p>"
                f"{fmts}"
                f"{hint}"
                "<p>Log (letzte Zeilen):</p>"
                f"<pre style='white-space:pre-wrap;background:#0b0b0b;border:1px solid #333;padding:12px;border-radius:10px'>{tail}</pre>"
                f"<p><a style='color:#3b82f6' href='{url_for('index')}'>Zurück</a></p>"
                "</body></html>",
                500,
            )

    except FileNotFoundError:
        return (
            "<html><body><h1>Fehler: mjpg_streamer nicht gefunden</h1>"
            "<p>Bitte installieren Sie mjpg-streamer oder passen Sie den Aufruf im Script an.</p>"
            f"<p><a href='{url_for('index')}'>Zurück</a></p>"
            "</body></html>",
            500,
        )


    host = request.host.split(":", 1)[0]
    stream_url = f"http://{host}:{port}/?action=stream"

    html = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Kamera {{ cam_id }} – Live-Stream</title>
  <style>
    body { margin:0; padding:10px; background:#000; color:#eee; font-family: system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
    h1 { font-size:1.4rem; margin-bottom:8px; }
    .info { font-size:0.9rem; opacity:0.8; margin-bottom:8px; }
    img { display:block; width:100%; max-height:90vh; object-fit:contain; background:#000; }
  </style>
</head>
<body>
  <h1>Kamera {{ cam_id }} – Live-Stream</h1>
  <div class="info">
    Autodarts wurde für die Kamera-Feinjustierung vorübergehend angehalten.<br>
    Stream-URL: {{ stream_url }}
  </div>
  <img src="{{ stream_url }}" alt="Kamera-Stream {{ cam_id }}">
</body>
</html>
"""
    return render_template_string(html, cam_id=cam_id, stream_url=stream_url)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)