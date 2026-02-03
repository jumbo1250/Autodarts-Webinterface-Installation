#!/usr/bin/env python3
from gpiozero import Button, LED
from signal import pause
import subprocess, time, threading
import json
from pathlib import Path

# ======= KONFIGURATION ANPASSEN, WENN NÖTIG =======
BUTTON_PIN = 17                   # GPIO für Taster (BCM 17, Pin 11)
LED_PIN = 27                      # GPIO für LED / Signalleuchte (BCM 27, Pin 13)

SERVICE_NAME = "autodarts.service"      # Name des Autodarts-Dienstes (ggf. anpassen!)
CAM_CFG_PATH = Path("/var/lib/autodarts/cam-config.json")
# ================================================

# Zeiten (Sekunden)
MIN_SHORT = 0.3       # unter 0,3s: ignorieren
SHUTDOWN_MIN = 3.0    # ab 3s: „Ausschaltmodus scharf“

# ======= LED- / STATUS-TUNING (CPU vs. Reaktionsgeschwindigkeit) =======
LED_SHUTDOWN_BLINK   = 0.5   # Blinkgeschwindigkeit beim tatsächlichen Shutdown
LED_RESTART_SLEEP    = 0.5   # Pause, wenn "service_restarting" aktiv ist
LED_ON_SLEEP         = 0.5   # Pause, wenn Autodarts läuft (LED dauerhaft an)
LED_BLINK_NO_SERVER  = 1.0   # Blink-Periode wenn Autodarts aus, aber Netz verbunden
LED_BLINK_NO_NET     = 2.0   # Blink-Periode wenn Autodarts aus und kein Netz
STATUS_REFRESH_SECONDS = 3.0 # wie oft Autodarts-/Netz-Status neu geprüft wird

LED_ARMED_BLINK      = 0.1   # *** sehr schnelles Blinken, wenn Shutdown „scharf“ ***
# =======================================================================

button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.05)
led = LED(LED_PIN)

press_time = None

# Zustände
service_restarting = False
shutting_down = False
shutdown_armed = False   # NEU: lange gedrückt, loslassen = ausschalten
running = True


def is_autodarts_active() -> bool:
    """Prüfen, ob der Autodarts-Dienst läuft."""
    result = subprocess.run(
        ["systemctl", "is-active", SERVICE_NAME],
        capture_output=True,
        text=True
    )
    return result.stdout.strip() == "active"


def is_network_connected() -> bool:
    """
    Prüfen, ob irgendein Netzwerk-Interface (WLAN oder LAN)
    als 'connected' gemeldet wird.
    """
    result = subprocess.run(
        ["nmcli", "-t", "-f", "DEVICE,STATE,TYPE", "device"],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        return False

    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 3:
            continue
        dev, state, devtype = parts[0], parts[1], parts[2]
        if devtype in ("wifi", "ethernet") and state == "connected":
            return True
    return False


def restart_autodarts():
    """
    Kurz drücken: Autodarts-Dienst neu starten, Kamera-Setup aufräumen.
      - mjpg_streamer stoppen
      - camera_mode=False im Web-Config-JSON
      - Autodarts neu starten
    """
    global service_restarting
    service_restarting = True
    led.off()

    # 1) Alle laufenden Streams stoppen
    try:
        subprocess.run(
            ["pkill", "-f", "mjpg_streamer"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as e:
        print(f"[autodarts-button] Warnung: pkill mjpg_streamer: {e}")

    # 2) camera_mode-Flag im JSON zurücksetzen
    try:
        data = {}
        if CAM_CFG_PATH.exists():
            with CAM_CFG_PATH.open("r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    data = {}

        data["camera_mode"] = False

        tmp_path = CAM_CFG_PATH.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f)
        tmp_path.replace(CAM_CFG_PATH)
    except Exception as e:
        print(f"[autodarts-button] Warnung beim Setzen von camera_mode=False: {e}")

    # 3) Autodarts-Dienst neu starten
    subprocess.run(
        ["systemctl", "restart", SERVICE_NAME],
        capture_output=True
    )

    # LED-Loop übernimmt wieder das Anzeigen des Status
    service_restarting = False


def shutdown_pi():
    """>=3s gedrückt UND losgelassen: Pi sauber runterfahren, LED schnell blinken bis Shutdown greift."""
    global shutting_down
    shutting_down = True
    subprocess.run(
        ["shutdown", "-h", "now"],
        capture_output=True
    )
    # Danach ist eh Feierabend, Script wird vom Shutdown gekillt.


def led_manager():
    """
    LED-Logik:
      - shutting_down: schnell blinken (Pi fährt runter)
      - service_restarting: LED aus
      - shutdown_armed + Taste noch gedrückt: EXTREM schnelles Blinken (Info: „loslassen = ausschalten“)
      - Autodarts läuft: LED dauerhaft an
      - Autodarts läuft NICHT + Netz verbunden: mittleres Blinken
      - Autodarts läuft NICHT + KEIN Netz: langsames Blinken
    """
    global running, shutdown_armed, press_time

    last_status_check = 0.0
    cached_server_ok = False
    cached_net_ok = False
    blink_state = False

    while running:
        # 1) Shutdown läuft gerade
        if shutting_down:
            led.toggle()
            time.sleep(LED_SHUTDOWN_BLINK)
            continue

        # 2) Service wird neu gestartet
        if service_restarting:
            led.off()
            time.sleep(LED_RESTART_SLEEP)
            continue

        # 3) Prüfen, ob die Taste lang genug gedrückt ist, um "scharf" zu werden
        if press_time is not None and not shutdown_armed:
            if time.monotonic() - press_time >= SHUTDOWN_MIN:
                shutdown_armed = True

        # 4) Wenn Shutdown „scharf“ und Taste noch gedrückt → extrem schnelles Blinken
        if shutdown_armed and button.is_pressed:
            led.toggle()
            time.sleep(LED_ARMED_BLINK)
            continue

        # 5) Normale Status-Anzeige
        now = time.monotonic()
        # Nur alle STATUS_REFRESH_SECONDS neu prüfen
        if now - last_status_check >= STATUS_REFRESH_SECONDS:
            cached_server_ok = is_autodarts_active()
            cached_net_ok = is_network_connected()
            last_status_check = now

        server_ok = cached_server_ok
        net_ok = cached_net_ok

        if server_ok:
            # Autodarts läuft
            led.on()
            time.sleep(LED_ON_SLEEP)
        else:
            # Autodarts läuft NICHT -> LED blinken
            blink_state = not blink_state
            if blink_state:
                led.on()
            else:
                led.off()

            if net_ok:
                # Netz da, aber Autodarts nicht aktiv -> "mittleres" Blinken
                time.sleep(LED_BLINK_NO_SERVER)
            else:
                # Kein Netz -> langsames Blinken
                time.sleep(LED_BLINK_NO_NET)


def on_press():
    global press_time, shutdown_armed
    press_time = time.monotonic()
    shutdown_armed = False   # neu drücken = wieder von vorne


def on_release():
    global press_time, shutdown_armed
    if press_time is None:
        return

    duration = time.monotonic() - press_time
    press_time = None

    # Zustand zurücksetzen – die Entscheidung fällt jetzt
    armed = shutdown_armed
    shutdown_armed = False

    if duration < MIN_SHORT:
        # zu kurz -> ignorieren
        return

    if duration >= SHUTDOWN_MIN or armed:
        # lange gedrückt -> Shutdown
        shutdown_pi()
    else:
        # 0,3–<3s: Autodarts-Dienst neu starten + Streams aufräumen
        restart_autodarts()


button.when_pressed = on_press
button.when_released = on_release

# LED-Thread starten
threading.Thread(target=led_manager, daemon=True).start()

pause()
