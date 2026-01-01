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

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    render_template_string,
    send_file,
    send_from_directory,
    jsonify,
    session,
    Response,

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

PI_MONITOR_SCRIPT = "/usr/local/bin/pi_monitor_test.sh"
PI_MONITOR_CSV = "/var/log/pi_monitor_test.csv"
PI_MONITOR_README = "/var/log/pi_monitor_test_README.txt"

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
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split(":")
                if len(parts) >= 3 and parts[0] == dev:
                    state = parts[1]
                    if state == "connected":
                        cn = parts[2].strip()
                        if cn != "--":
                            conn_name = cn
                    break
    except Exception:
        pass

    # 2) aus dem Connection-Profil die echte SSID holen
    if conn_name:
        try:
            res2 = subprocess.run(
                ["nmcli", "-t", "-f", "802-11-wireless.ssid", "connection", "show", conn_name],
                capture_output=True,
                text=True,
            )
            if res2.returncode == 0:
                for line in res2.stdout.splitlines():
                    if line.startswith("802-11-wireless.ssid:"):
                        val = line.split(":", 1)[1].strip()
                        if val:
                            ssid = val
                            break
        except Exception:
            pass

        # Fallback: wenn keine SSID gefunden wurde, nimm wenigstens den Connection-Namen
        if ssid is None:
            ssid = conn_name

    # 3) IPv4-Adresse vom Interface holen
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", dev],
            capture_output=True,
            text=True,
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


def get_wifi_signal_percent() -> int | None:
    """
    Liefert die Signalstärke (0-100) des aktuell verbundenen WLANs am WIFI_INTERFACE.
    Ressourcenschonend: wird nur beim Seitenaufruf ermittelt (kein Loop).
    """
    dev = WIFI_INTERFACE
    try:
        # nmcli liefert SIGNAL bereits als 0..100
        r = subprocess.run(
            ["nmcli", "-t", "-f", "IN-USE,SIGNAL,SSID", "device", "wifi", "list", "ifname", dev],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if r.returncode != 0:
            return None
        for line in r.stdout.splitlines():
            # Format: *:70:MyWifi
            parts = line.split(":", 2)
            if len(parts) >= 2 and parts[0].strip() == "*":
                try:
                    return int(parts[1].strip())
                except Exception:
                    return None
    except Exception:
        return None
    return None

def wifi_dongle_present() -> bool:
    """Prüft, ob der WLAN-USB-Dongle (WIFI_INTERFACE) als WiFi-Device beim NetworkManager sichtbar ist."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE", "device"],
            capture_output=True,
            text=True,
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


# ---------------- Verbindungstest (Ping zum Router) ----------------

def get_default_gateway(interface: str | None = None) -> str | None:
    """
    Ermittelt das Default-Gateway (Router-IP).
    Wenn interface gesetzt ist (z.B. wlan0), bevorzugen wir die Default-Route über dieses Interface.
    """
    try:
        r = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=1.5)
        if r.returncode != 0:
            return None
        lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
        if not lines:
            return None

        def parse_line(line: str) -> tuple[str | None, str | None]:
            # Beispiel: "default via 192.168.0.1 dev wlan0 proto dhcp metric 600"
            m = re.search(r"\bvia\s+(\d+\.\d+\.\d+\.\d+)\b", line)
            gw = m.group(1) if m else None
            m2 = re.search(r"\bdev\s+(\S+)\b", line)
            dev = m2.group(1) if m2 else None
            return gw, dev

        # Prefer matching interface
        if interface:
            for ln in lines:
                gw, dev = parse_line(ln)
                if gw and dev == interface:
                    return gw

        # Fallback: first default route with gateway
        for ln in lines:
            gw, _ = parse_line(ln)
            if gw:
                return gw
    except Exception:
        return None
    return None


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def ping_test_generator(total: int = 30) :
    """
    Streamt Fortschritt + Ergebnis als SSE (Server-Sent Events).
    Zielt auf den Router (Default Gateway), damit man die Stabilität zum Router sieht.
    """
    gw = get_default_gateway(WIFI_INTERFACE)
    if not gw:
        yield _sse({"done": True, "error": "Kein Router (Default-Gateway) gefunden. Bitte zuerst mit dem Heim-WLAN verbinden."})
        return

    yield _sse({"start": True, "target": gw, "total": total})

    cmd = ["ping", "-O", "-n", "-c", str(total), "-W", "1", "-i", "0.2", gw]

    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    except Exception as e:
        yield _sse({"done": True, "error": f"Ping konnte nicht gestartet werden: {e}"})
        return

    last_seq = 0
    received = 0
    rtts: list[float] = []

    # Wir zählen "gesendet" über icmp_seq (auch bei Packetloss)
    for raw in (p.stdout or []):
        line = (raw or "").strip()
        mseq = re.search(r"icmp_seq=(\d+)", line)
        if not mseq:
            continue

        seq = int(mseq.group(1))
        if seq > last_seq:
            last_seq = seq  # Fortschritt (1..total)

        # Erfolg?
        if "bytes from" in line:
            received += 1
            mt = re.search(r"time=([\d\.]+)\s*ms", line)
            if mt:
                try:
                    rtts.append(float(mt.group(1)))
                except Exception:
                    pass

        # Event push (auch bei Timeouts)
        yield _sse({"progress": True, "sent": last_seq, "total": total, "received": received})

    try:
        p.wait(timeout=3.0)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass

    # Stats berechnen (nur aus Antworten)
    if rtts:
        tmin = min(rtts)
        tmax = max(rtts)
        tavg = sum(rtts) / len(rtts)
    else:
        tmin = tmax = tavg = None

    yield _sse({
        "done": True,
        "sent": total,
        "received": received,
        "min_ms": (round(tmin, 2) if tmin is not None else None),
        "max_ms": (round(tmax, 2) if tmax is not None else None),
        "avg_ms": (round(tavg, 2) if tavg is not None else None),
        "target": gw,
    })



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
    r = subprocess.run(
        ["systemctl", "is-active", AUTODARTS_SERVICE],
        capture_output=True,
        text=True,
    )
    return r.stdout.strip() == "active"


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
                # typische Meta/ISP Geräte raus
                if "bcm2835" in n or "isp" in n or "codec" in n or "rp1" in n:
                    return False
                # typische Kamera-Wörter rein
                if "usb" in n or "camera" in n or "webcam" in n or "uvc" in n:
                    return True
                # unbekannt: trotzdem erlauben (manche Dongles haben komische Namen)
                return True

            cam_groups = [g for g in groups if _looks_like_camera(g[0])]

            devices = []
            for name, videos in cam_groups:
                videos_sorted = sorted(videos)
                dev = videos_sorted[0]  # erstes /dev/videoX der Gruppe
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
        if os.path.exists(dev):
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


def service_is_active(service_name: str) -> bool:
    r = subprocess.run(["systemctl", "is-active", service_name], capture_output=True, text=True)
    return r.stdout.strip() == "active"


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



def service_enable_now(service_name: str):
    subprocess.run(["systemctl", "enable", "--now", service_name], capture_output=True, text=True)


def service_disable_now(service_name: str):
    subprocess.run(["systemctl", "disable", "--now", service_name], capture_output=True, text=True)


def service_restart(service_name: str):
    subprocess.run(["systemctl", "restart", service_name], capture_output=True, text=True)


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
    tmp = WLED_CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, WLED_CONFIG_PATH)



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
    ssid, ip = get_wifi_status()
    wifi_signal = get_wifi_signal_percent()
    gateway_ip = get_default_gateway(WIFI_INTERFACE) if (ssid and ip) else None
    can_ping_test = bool(gateway_ip)
    autodarts_active = is_autodarts_active()
    autodarts_version = get_autodarts_version()
    cpu_pct, mem_used, mem_total, temp_c = get_system_stats()
    ad_restarted = request.args.get("ad_restarted") == "1"

    current_ap_ssid = get_ap_ssid()

    wifi_ok = bool(ssid and ip)
    dongle_ok = wifi_ok or wifi_dongle_present()

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

    admin_unlocked = bool(session.get('admin_unlocked', False))
    adminerr = request.args.get('adminerr')
    adminmsg = (request.args.get('adminmsg') or '').strip()
    adminok = (request.args.get('adminok') == '1')

    update_state = load_update_state() if admin_unlocked else {}
    update_log_tail = tail_file(AUTODARTS_UPDATE_LOG, n=25, max_chars=3500) if admin_unlocked else ""

    # Wenn aus Versehen ein altes Setup "master_enabled": false gesetzt hat,
    # drehen wir das still wieder auf – sonst wirkt "Ein/Aus" kaputt.
    if not bool(wled_cfg.get("master_enabled", True)):
        wled_cfg["master_enabled"] = True
        save_wled_config(wled_cfg)

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
  
    .overlay { position:fixed; inset:0; background:rgba(0,0,0,0.75); display:none; align-items:center; justify-content:center; z-index:2000; padding:16px; }
    .overlay-card { max-width:520px; width:100%; background:#1c1c1c; border:1px solid #333; border-radius:14px; padding:16px; box-shadow:0 0 20px rgba(0,0,0,0.6); }
    progress { width:100%; height:18px; }
</style>
</head>
<body>

  {% if cpu_pct is not none or mem_used is not none or temp_c is not none %}
  <div class="sysinfo">
    <h3>Pi-Status</h3>
    {% if cpu_pct is not none %}
      <div class="sysinfo-row">CPU: {{ cpu_pct }}%</div>
    {% endif %}
    {% if mem_used is not none and mem_total is not none %}
      <div class="sysinfo-row">RAM: {{ mem_used }} / {{ mem_total }} GB</div>
    {% endif %}
    {% if temp_c is not none %}
      <div class="sysinfo-row">Temp: {{ "%.1f"|format(temp_c) }} °C</div>
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
    <div class="subtitle">by Peter Rottmann v.1.20 (Multi-WLED)</div>

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
          (IP-Adresse: <strong>{{ ip }}</strong>){% if wifi_signal is not none %} · Signal: <strong>{{ wifi_signal }}%</strong>{% endif %}.
        {% elif ssid and not ip %}
          Mit WLAN <strong>{{ ssid }}</strong> verbunden,
          aber es wurde keine IPv4-Adresse vergeben.{% if wifi_signal is not none %} (Signal: <strong>{{ wifi_signal }}%</strong>){% endif %}
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
      </div>
      {% if can_ping_test %}
      <div class="btn-row" style="margin-top:10px;">
        <button type="button" id="ping_btn" class="btn">Verbindungstest (30 Pakete)</button>
      </div>
      {% endif %}
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

<form method="post" action="{{ url_for('wled_save_enabled') }}">
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

          <a id="wled_cfgbtn_{{ b.slot }}" href="{{ url_for('wled_open_slot', slot=b.slot) }}" target="_blank"
             class="btn btn-small {% if not wled_installed or not b.enabled %}btn-disabled{% endif %}">
            LED konfigurieren
          </a>
        </div>
      </div>
    </div>
  {% endfor %}

  <div class="btn-row" style="margin-top:12px;">
    <button type="submit" class="btn btn-primary {% if not wled_installed %}btn-disabled{% endif %}">
      Speichern
    </button>
  </div>
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
  (function () {
    const statusUrl = "{{ url_for('api_wled_status') }}";
    const setUrl = "{{ url_for('api_wled_set_enabled') }}";

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

    function setCfgBtn(slot, enabled) {
      const a = document.getElementById("wled_cfgbtn_" + slot);
      if (!a) return;
      if (enabled) a.classList.remove("btn-disabled");
      else a.classList.add("btn-disabled");
    }

    function refresh() {
      fetch(statusUrl, { cache: "no-store" })
        .then(r => r.json())
        .then(data => {
          (data.bands || []).forEach(b => {
            setCfgBtn(b.slot, !!b.enabled);
            if (!b.enabled) return setStatus(b.slot, "off");
            if (b.online === true) return setStatus(b.slot, "ok");
            if (b.online === false) return setStatus(b.slot, "bad");
            setStatus(b.slot, "checking");
          });
        })
        .catch(() => {});
    }

    function postEnabled(slot, enabled) {
      const fd = new FormData();
      fd.append("slot", String(slot));
      fd.append("enabled", enabled ? "1" : "0");
      return fetch(setUrl, { method: "POST", body: fd, cache: "no-store" });
    }

    window.addEventListener("load", refresh);

    document.querySelectorAll("input[type=checkbox][name^=wled_enabled_]").forEach(cb => {
      cb.addEventListener("change", () => {
        const slot = Number((cb.name || "").split("_").pop());
        if (!slot) return;

        setCfgBtn(slot, cb.checked);
        setStatus(slot, cb.checked ? "checking" : "off");

        postEnabled(slot, cb.checked)
          .then(r => {
            if (!r.ok) throw new Error("save_failed");
            // Nur checken, wenn eingeschaltet (sonst kein Traffic)
            if (cb.checked) setTimeout(refresh, 200);
          })
          .catch(() => {
            // revert on error
            cb.checked = !cb.checked;
            setCfgBtn(slot, cb.checked);
            setStatus(slot, cb.checked ? "bad" : "off");
          });
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

          <h3 style="margin:0 0 8px;">Autodarts Software</h3>
          <p class="hint" style="margin-top:0;">
            Installierte Version: <strong>{{ autodarts_version or 'unbekannt' }}</strong>
          </p>

          <form method="post" action="{{ url_for('admin_autodarts_update') }}"
                onsubmit="return confirm('Autodarts jetzt aktualisieren?');">
            <div class="btn-row">
              <button type="submit" class="btn btn-primary">Autodarts aktualisieren</button>
            </div>
            <p class="hint" style="margin-top:6px;">
              Hinweis: Das Update nutzt das offizielle Autodarts-Update-Script (updater.sh) und schreibt ein Log.
            </p>
          </form>

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

  <!-- Verbindungstest Overlay -->
  <div id="ping_overlay" class="overlay" aria-hidden="true">
    <div class="overlay-card">
      <div style="display:flex; justify-content:space-between; align-items:center; gap:12px;">
        <div style="font-weight:700; font-size:1.05rem;">Verbindungstest</div>
        <button type="button" id="ping_close" class="btn btn-small">Schließen</button>
      </div>
      <p class="hint" style="margin-top:8px;" id="ping_target">Starte…</p>
      <progress id="ping_progress" max="30" value="0"></progress>
      <p style="margin:10px 0 0;" id="ping_text">Bitte warten…</p>
    </div>
  </div>

<script>
(function(){
  const btn = document.getElementById("ping_btn");
  const overlay = document.getElementById("ping_overlay");
  const closeBtn = document.getElementById("ping_close");
  const prog = document.getElementById("ping_progress");
  const txt = document.getElementById("ping_text");
  const tgt = document.getElementById("ping_target");
  const streamUrl = "{{ url_for('api_pingtest_stream') }}";
  let es = null;

  function show(){ if (overlay){ overlay.style.display="flex"; overlay.setAttribute("aria-hidden","false"); } }
  function hide(){ 
    if (es){ try{ es.close(); }catch(e){} es=null; }
    if (overlay){ overlay.style.display="none"; overlay.setAttribute("aria-hidden","true"); }
    if (btn){ btn.disabled=false; btn.classList.remove("btn-disabled"); }
  }

  function start(){
    if (!btn) return;
    btn.disabled=true;
    btn.classList.add("btn-disabled");
    prog.value = 0;
    prog.max = 30;
    txt.textContent = "Starte…";
    tgt.textContent = "Starte…";
    show();

    es = new EventSource(streamUrl);
    es.onmessage = (ev) => {
      let data = null;
      try { data = JSON.parse(ev.data); } catch(e) { return; }

      if (data.start){
        if (data.total) prog.max = data.total;
        tgt.textContent = data.target ? ("Ziel (Router): " + data.target) : "Ziel: —";
        txt.textContent = "0 von " + (data.total || 30) + " Paketen…";
      }
      if (data.progress){
        const total = data.total || 30;
        prog.max = total;
        prog.value = data.sent || 0;
        txt.textContent = (data.sent || 0) + " von " + total + " Paketen… (" + (data.received || 0) + " Antworten)";
      }
      if (data.done){
        if (data.error){
          txt.textContent = "Fehler: " + data.error;
        } else {
          const sent = data.sent ?? 30;
          const rec = data.received ?? 0;
          const min = (data.min_ms != null) ? data.min_ms + " ms" : "—";
          const max = (data.max_ms != null) ? data.max_ms + " ms" : "—";
          const avg = (data.avg_ms != null) ? data.avg_ms + " ms" : "—";
          txt.innerHTML = 
            "<strong>" + rec + " von " + sent + " Paketen</strong> wurden erfolgreich gesendet.<br>" +
            "Schnellstes Paket: <strong>" + min + "</strong><br>" +
            "Langsamstes Paket: <strong>" + max + "</strong><br>" +
            "Durchschnitt: <strong>" + avg + "</strong>";
        }
        try{ es.close(); }catch(e){}
        es = null;
        btn.disabled=false;
        btn.classList.remove("btn-disabled");
      }
    };

    es.onerror = () => {
      txt.textContent = "Verbindungstest abgebrochen oder fehlgeschlagen.";
      try{ es.close(); }catch(e){}
      es = null;
      btn.disabled=false;
      btn.classList.remove("btn-disabled");
    };
  }

  if (btn) btn.addEventListener("click", start);
  if (closeBtn) closeBtn.addEventListener("click", hide);
  if (overlay) overlay.addEventListener("click", (e)=>{ if(e.target===overlay) hide(); });
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
        can_ping_test=can_ping_test,
        wifi_interface=WIFI_INTERFACE,
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


@app.route("/api/wled/set-enabled", methods=["POST"])
def api_wled_set_enabled():
    """
    Speichert Ein/Aus für einen Slot sofort (ohne extra "Speichern"-Klick).
    """
    try:
        slot = int((request.form.get("slot") or "0").strip())
    except Exception:
        slot = 0
    enabled = (request.form.get("enabled") or "").strip() in ("1", "true", "True", "on", "yes")

    if slot < 1 or slot > 3:
        return jsonify({"ok": False, "error": "invalid_slot"}), 400

    cfg = load_wled_config()
    cfg["master_enabled"] = True  # User-UI hat keinen Master-Schalter

    targets = cfg.get("targets", []) or []
    while len(targets) < 3:
        targets.append({"label": f"Dart LED{len(targets)+1}", "host": "", "enabled": False})
    targets = targets[:3]

    targets[slot - 1]["enabled"] = bool(enabled)
    cfg["targets"] = targets
    try:
        save_wled_config(cfg)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    # Service handling: nur wenn installiert/exists
    if service_exists(DARTS_WLED_SERVICE):
        hosts = get_enabled_wled_hosts(cfg)
        if hosts:
            update_darts_wled_start_custom_weps(hosts)
            service_enable_now(DARTS_WLED_SERVICE)
        else:
            # Keine aktiven Bänder -> Service AUS (keine Ressourcen / keine Logs)
            service_disable_now(DARTS_WLED_SERVICE)

    return jsonify({"ok": True, "slot": slot, "enabled": bool(enabled)})


@app.route("/api/pingtest/stream", methods=["GET"])
def api_pingtest_stream():
    return Response(ping_test_generator(total=30), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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


@app.route("/admin/autodarts/update", methods=["POST"])
def admin_autodarts_update():
    if not bool(session.get("admin_unlocked", False)):
        return ("Forbidden", 403)

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
    gateway_ip = get_default_gateway(WIFI_INTERFACE) if (ssid and ip) else None
    can_ping_test = bool(gateway_ip)
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

    try:
        subprocess.Popen(
            [
                "mjpg_streamer",
                "-i",
                f"input_uvc.so -d {dev}",
                "-o",
                f"output_http.so -p {port}",
            ]
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
