"""Netzwerk-Helfer: WLAN-Client, Access-Point, Signal, Kanal/Reboot, Ping, nmcli.

Aus autodarts-web.py ausgelagert (Blueprint-Refactoring).
"""
import os
import re
import json
import time
import uuid
import shlex
import threading
import subprocess
from pathlib import Path

from webapp.config import (
    WIFI_INTERFACE,
    WIFI_CONNECTION_NAME,
    AP_INTERFACE,
    AP_CONNECTION_NAME,
    AP_INTERNET_STATUS_PATH,
    MANUAL_WIFI_CONNECT_FLAG,
    WIFI_REBOOT_COUNTDOWN_SECONDS,
    WIFI_REBOOT_STATE_FILE,
    WIFI_REBOOT_UNIT_PREFIX,
    WIFI_CHANNEL_APPLY_UNIT_PREFIX,
)
from webapp.utils.i18n import t

WIFI_SIGNAL_CACHE = {'ts': 0.0, 'v': None}


WIFI_SIGNAL_CACHE_TTL_SEC = 5.0  # Signalstärke nur auf Knopfdruck, kurz cachen


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


def get_lan_status():
    """
    Liefert die IPv4-Adresse von eth0 oder None,
    wenn keine aktive LAN-Verbindung vorhanden ist.
    """
    ip = None

    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,STATE", "device"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if result.returncode != 0:
            return None

        eth_connected = False
        for line in result.stdout.splitlines():
            parts = line.strip().split(":", 1)
            if len(parts) >= 2 and parts[0] == "eth0":
                if parts[1] == "connected":
                    eth_connected = True
                break

        if not eth_connected:
            return None
    except Exception:
        return None

    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", "eth0"],
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

    return ip


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

    user_msg = t("wifi.connection_failed", "Verbindung konnte nicht hergestellt werden.")

    if "no device" in lower or "unknown device" in lower:
        user_msg = t("wifi.dongle_not_detected", "WLAN-USB-Stick wird nicht richtig erkannt.")
    elif "no wifi device" in lower:
        user_msg = t("wifi.no_valid_device", "Kein gültiges WLAN-Gerät gefunden (WLAN-Stick fehlt oder wird nicht richtig erkannt).")
    elif "no network with ssid" in lower or "wifi network could not be found" in lower or "ssid not found" in lower:
        user_msg = t("wifi.ssid_not_found", "Der eingegebene WLAN-Name (SSID) wurde nicht gefunden. Bitte Schreibweise und Abstand zum Router prüfen.")
    elif "wrong password" in lower or "secrets were required, but not provided" in lower or "invalid passphrase" in lower:
        user_msg = t("wifi.password_invalid", "Das WLAN-Passwort scheint nicht zu stimmen. Bitte erneut eingeben.")
    elif "no suitable device found" in lower or "profile is not compatible with device" in lower:
        user_msg = t("wifi.profile_not_compatible", "Das WLAN-Profil passt nicht zum Gerät (z.B. falsches Interface wie eth0 statt WLAN-Stick).")
    elif "activation failed" in lower:
        user_msg = t("wifi.router_rejected", "Der Router hat die Verbindung abgelehnt oder es gibt ein Problem mit den WLAN-Einstellungen.")

    debug_msg = t("wifi.debug_details", " (Details für Profis: {details})", details=short) if short else ""
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


def mark_manual_wifi_connect():
    """Marker für den AP-Internet-Fix: manuelle WLAN-Verbindung über Webpanel läuft."""
    try:
        Path(MANUAL_WIFI_CONNECT_FLAG).write_text(str(int(time.time())), encoding="utf-8")
        os.chmod(MANUAL_WIFI_CONNECT_FLAG, 0o644)
    except Exception:
        pass


def clear_manual_wifi_connect_marker():
    try:
        Path(MANUAL_WIFI_CONNECT_FLAG).unlink(missing_ok=True)
    except Exception:
        pass


def _iw_channel_freq(iface: str) -> tuple[str | None, int | None]:
    try:
        r = subprocess.run(["iw", "dev", iface, "info"], capture_output=True, text=True, timeout=1.5)
        if r.returncode != 0:
            return None, None
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("channel "):
                parts = line.split()
                channel = parts[1] if len(parts) > 1 else None
                m = re.search(r"\((\d+)\s+MHz\)", line)
                freq = int(m.group(1)) if m else None
                return channel, freq
    except Exception:
        pass
    return None, None


def _target_setup_wifi_channel(uplink_ch: str | None, uplink_freq: int | None) -> str | None:
    # Nur bei 2.4 GHz optimieren. Bei 5 GHz bleibt das Setup-WLAN unangetastet.
    if uplink_freq is None:
        return None
    if uplink_freq >= 5000:
        return None
    if uplink_ch in {"1", "2", "3", "4", "5"}:
        return "11"
    if uplink_ch in {"6", "7", "8", "9", "10", "11", "12", "13"}:
        return "1"
    return "1"


def setup_wifi_channel_change_likely_needed() -> bool:
    """True, wenn nach der WLAN-Verbindung ein anderer AP-Kanal sinnvoll ist."""
    return get_setup_wifi_target_channel() is not None


def get_setup_wifi_target_channel() -> str | None:
    """Liefert den Zielkanal für den Setup-AP, aber nur wenn er geändert werden muss."""
    uplink_ch, uplink_freq = _iw_channel_freq(WIFI_INTERFACE)
    target = _target_setup_wifi_channel(uplink_ch, uplink_freq)
    if not target:
        return None
    current_ap_ch, _current_ap_freq = _iw_channel_freq(AP_INTERFACE)
    if not current_ap_ch or current_ap_ch == target:
        return None
    return target


def _root_cmd(cmd: list[str]) -> list[str]:
    if os.geteuid() == 0:
        return cmd
    return ["sudo", "-n", *cmd]


def _read_wifi_reboot_state() -> dict:
    try:
        data = json.loads(Path(WIFI_REBOOT_STATE_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_wifi_reboot_state(data: dict) -> None:
    path = Path(WIFI_REBOOT_STATE_FILE)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.chmod(path, 0o644)


def _remove_wifi_reboot_state() -> None:
    try:
        Path(WIFI_REBOOT_STATE_FILE).unlink(missing_ok=True)
    except Exception:
        pass


def _stop_transient_unit(unit_name: str | None) -> bool:
    if not unit_name:
        return False
    stopped = False
    for suffix in (".timer", ".service"):
        try:
            r = subprocess.run(
                _root_cmd(["systemctl", "stop", f"{unit_name}{suffix}"]),
                capture_output=True,
                text=True,
                timeout=4.0,
            )
            stopped = stopped or r.returncode == 0
        except Exception:
            pass
    return stopped


def cancel_scheduled_wifi_channel_reboot(remove_state: bool = True) -> tuple[dict, bool]:
    """Stoppt nur den geplanten Reboot. Ein manueller Kanalwechsel wird hier nicht gestartet."""
    state = _read_wifi_reboot_state()
    stopped = _stop_transient_unit(str(state.get("unit") or ""))
    if remove_state:
        _remove_wifi_reboot_state()
    return state, stopped


def schedule_wifi_channel_reboot(target_channel: str, delay_seconds: int = WIFI_REBOOT_COUNTDOWN_SECONDS) -> bool:
    """
    Plant den kontrollierten Ablauf:
    - AP bleibt bis zum Ablauf erreichbar.
    - Zielkanal wird nur im NetworkManager-Profil gespeichert.
    - Danach wird der Raspberry neu gestartet; beim Boot startet der AP am neuen Kanal.
    """
    if str(target_channel) not in {str(i) for i in range(1, 14)}:
        return False

    cancel_scheduled_wifi_channel_reboot(remove_state=True)
    delay = max(3, int(delay_seconds))
    unit = f"{WIFI_REBOOT_UNIT_PREFIX}-{uuid.uuid4().hex[:8]}"
    state = {
        "unit": unit,
        "target_channel": str(target_channel),
        "ap_connection": AP_CONNECTION_NAME,
        "ap_ssid": get_ap_ssid() or "Autodartsinstall1",
        "created_at": int(time.time()),
        "delay_seconds": delay,
    }

    script = "; ".join([
        "set -e",
        f"nmcli connection modify {shlex.quote(AP_CONNECTION_NAME)} 802-11-wireless.band bg 802-11-wireless.channel {shlex.quote(str(target_channel))}",
        f"rm -f {shlex.quote(MANUAL_WIFI_CONNECT_FLAG)} {shlex.quote(WIFI_REBOOT_STATE_FILE)}",
        "sync",
        "systemctl reboot",
    ])

    try:
        _write_wifi_reboot_state(state)
        r = subprocess.run(
            _root_cmd([
                "systemd-run",
                f"--unit={unit}",
                f"--on-active={delay}s",
                "--timer-property=AccuracySec=1s",
                "/bin/bash",
                "-lc",
                script,
            ]),
            capture_output=True,
            text=True,
            timeout=8.0,
        )
        if r.returncode == 0:
            return True
    except Exception:
        pass

    _remove_wifi_reboot_state()
    return False


def _schedule_channel_apply_without_reboot(target_channel: str) -> bool:
    """
    Wird bei 'Neustart abbrechen' verwendet. Nach kurzer Verzögerung wird nur
    der AP-Kanal umgestellt. Dabei verliert der Browser erwartungsgemäß den AP.
    """
    if str(target_channel) not in {str(i) for i in range(1, 14)}:
        return False

    unit = f"{WIFI_CHANNEL_APPLY_UNIT_PREFIX}-{uuid.uuid4().hex[:8]}"
    script = "; ".join([
        "set -e",
        f"nmcli connection modify {shlex.quote(AP_CONNECTION_NAME)} 802-11-wireless.band bg 802-11-wireless.channel {shlex.quote(str(target_channel))}",
        f"nmcli connection down {shlex.quote(AP_CONNECTION_NAME)} || true",
        "sleep 1",
        f"nmcli connection up {shlex.quote(AP_CONNECTION_NAME)}",
        f"rm -f {shlex.quote(MANUAL_WIFI_CONNECT_FLAG)} {shlex.quote(WIFI_REBOOT_STATE_FILE)}",
    ])
    try:
        r = subprocess.run(
            _root_cmd([
                "systemd-run",
                f"--unit={unit}",
                "--on-active=2s",
                "--timer-property=AccuracySec=1s",
                "/bin/bash",
                "-lc",
                script,
            ]),
            capture_output=True,
            text=True,
            timeout=8.0,
        )
        return r.returncode == 0
    except Exception:
        return False


def _trigger_legacy_ap_fix_async() -> None:
    """Fallback, falls der kontrollierte Reboot ausnahmsweise nicht geplant werden kann."""
    try:
        subprocess.Popen(
            _root_cmd(["systemctl", "restart", "webpanel_AP.service"]),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


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


def get_ping_uplink_interface() -> str | None:
    """
    Für den Verbindungstest nur echte Uplinks erlauben:
    - eth0 (Kabel)
    - WIFI_INTERFACE (z.B. wlan0)
    Access Point Interface (wlan_ap) ist bereits ausgeschlossen.
    """
    iface = _get_default_route_interface()
    if not iface:
        return None
    if iface == "eth0":
        return iface
    if iface == WIFI_INTERFACE:
        return iface
    return None


def get_default_gateway_for_interface(iface: str) -> str | None:
    try:
        r = subprocess.run(["ip", "route", "show", "default", "dev", iface], capture_output=True, text=True, timeout=1.2)
        if r.returncode != 0:
            return None
        m = re.search(r"default\s+via\s+(\d+\.\d+\.\d+\.\d+)", r.stdout or "")
        return m.group(1) if m else None
    except Exception:
        return None


def ping_iface_label(iface: str) -> str:
    if iface == "eth0":
        return t("ping.label_eth0", "Verbindungstest über Kabel (eth0)")
    if iface == WIFI_INTERFACE:
        return t("ping.label_wifi", "Verbindungstest über WLAN ({iface})", iface=iface)
    return t("ping.label_generic", "Verbindungstest über {iface}", iface=iface)


def _ping_worker(job_id: str, target: str, count: int):
    job = PING_JOBS.get(job_id)
    if not job:
        return
    times = []
    received = 0
    try:
        p = subprocess.Popen(
            ["ping", "-n", "-c", str(count), *(["-I", str(job.get("iface"))] if job and job.get("iface") else []), target],
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
    iface = get_ping_uplink_interface()
    if not iface:
        return False, t("ping.no_gateway_found", "Kein Gateway gefunden (nur Access Point oder nicht verbunden?)."), None

    gw = get_default_gateway_for_interface(iface)
    if not gw:
        return False, t("ping.no_gateway_on_iface", "Kein Gateway auf {iface} gefunden (nicht verbunden?).", iface=iface), None

    job_id = uuid.uuid4().hex[:10]
    PING_JOBS[job_id] = {
        "target": gw,
        "iface": iface,
        "iface_label": ping_iface_label(iface),
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


def _load_ap_client_internet_status():
    """Liest den Status der AP-Internetfreigabe für verbundene Geräte."""
    data = {
        "status": "red",
        "client_internet": False,
        "ap_active": False,
        "forwarding_ready": False,
        "pi_online": False,
        "uplinks": [],
        "note": t("ap_internet.status_file_missing", "Statusdatei nicht gefunden"),
    }
    try:
        with open(AP_INTERNET_STATUS_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return data
        status = str(raw.get("status") or "").strip().lower()
        pi_online = bool(raw.get("pi_online", False))
        forwarding_ready = bool(raw.get("forwarding_ready", False))
        ap_active = bool(raw.get("ap_active", False))
        client_internet = bool(status == "green" and pi_online and forwarding_ready and ap_active)
        note = (
            t("ap_internet.clients_online", "AP-Clients haben Internet")
            if client_internet
            else t("ap_internet.clients_offline", "AP-Clients haben aktuell kein Internet")
        )
        return {
            "status": "green" if client_internet else "red",
            "client_internet": client_internet,
            "ap_active": ap_active,
            "forwarding_ready": forwarding_ready,
            "pi_online": pi_online,
            "uplinks": raw.get("uplinks") if isinstance(raw.get("uplinks"), list) else [],
            "note": note,
        }
    except Exception:
        return data


def _nmcli_terse_split(line: str) -> list[str]:
    r"""
    Split nmcli -t output line by unescaped ':'.
    nmcli escapes ':' as '\:' and '\' as '\\'.
    """
    parts = []
    cur = []
    esc = False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
            continue
        if ch == '\\':
            esc = True
            continue
        if ch == ':':
            parts.append(''.join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append(''.join(cur))
    return parts


def _nmcli_unescape(val: str) -> str:
    return val.replace(r'\:', ':').replace(r'\\', '\\')


def _active_wifi_connection_name(iface: str) -> str | None:
    """
    Gibt den aktuell aktiven Connection-Namen auf dem Interface zurück (z.B. für Autoconnect Toggle).
    """
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "GENERAL.CONNECTION", "dev", "show", iface],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if r.returncode != 0:
            return None
        # Format: "GENERAL.CONNECTION:<NAME>"
        val = (r.stdout or "").split(":", 1)[-1].strip()
        if not val or val in ("--", "unknown"):
            return None
        return val
    except Exception:
        return None


def get_active_wifi_autoconnect_state() -> tuple[str | None, bool | None]:
    """Return (active_wifi_connection_name, autoconnect_enabled). Values may be None."""
    conn = _active_wifi_connection_name(WIFI_INTERFACE)
    if not conn:
        return None, None
    try:
        r = subprocess.run(
            ["nmcli", "-g", "connection.autoconnect", "connection", "show", conn],
            capture_output=True,
            text=True,
            timeout=1.2,
        )
        if r.returncode != 0:
            return conn, None
        val = (r.stdout or "").strip().lower()
        if not val:
            return conn, None
        enabled = val in ("yes", "true", "1", "on")
        return conn, enabled
    except Exception:
        return conn, None


def get_direct_connect_state() -> tuple[bool, str, str]:
    """Returns (is_active, ssid, ip) if Autodarts-WiFi-NoStick is active on wlan_ap."""
    import re as _re
    nostick = "Autodarts-WiFi-NoStick"
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=3,
        )
        for line in r.stdout.splitlines():
            parts = line.strip().split(":", 1)
            if parts[0].strip() != nostick:
                continue
            iface = parts[1].strip() if len(parts) > 1 else "wlan_ap"
            r2 = subprocess.run(
                ["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", nostick],
                capture_output=True, text=True, timeout=3,
            )
            ssid = r2.stdout.strip() if r2.returncode == 0 else "Heimnetzwerk"
            r3 = subprocess.run(
                ["ip", "-4", "-o", "addr", "show", iface],
                capture_output=True, text=True, timeout=3,
            )
            m = _re.search(r"inet (\d+\.\d+\.\d+\.\d+)", r3.stdout)
            ip = m.group(1) if m else ""
            return True, ssid, ip
        return False, "", ""
    except Exception:
        return False, "", ""
