import os
import subprocess
import time
import urllib.request
from datetime import datetime

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    jsonify,
    Response,
    stream_with_context,
)

from webapp.config import *
from webapp.utils.i18n import t
from webapp.utils.web import tail_file, _inline_notice_page
from webapp.modules.autodarts import is_autodarts_active
from webapp.modules.camera import *
from webapp.modules.camera import (
    _camera_mode_runtime_active,
    _set_camera_mode_state,
    _best_resolution_for_formats,
    _legacy_inventory_from_devices,
    _normalize_camera_slots,
    _devices_for_slots,
)

camera_bp = Blueprint("camera", __name__)


def _camera_stream_context(cam_id: int):
    """
    Bereitet den Live-Stream für eine Kamera vor und liefert alle nötigen Daten
    für Live-View oder Präzisions-Fokus.
    """
    cam_config = load_cam_config()
    autodarts_active = is_autodarts_active()
    camera_mode = _camera_mode_runtime_active(cam_config, autodarts_active=autodarts_active)

    if not camera_mode:
        if bool(cam_config.get("camera_mode", False)):
            _set_camera_mode_state(cam_config, False)
            save_cam_config(cam_config)
        return {
            "error": redirect(url_for("index", msg=t("camera.mode_not_active_text", "Bitte gehen Sie zurück und klicken Sie zuerst auf Kamera einstellen.")))
        }

    live_inventory = detect_camera_inventory(MAX_CAMERAS)
    if not live_inventory:
        live_inventory = cam_config.get("camera_inventory", [])
        if not isinstance(live_inventory, list):
            live_inventory = []
        if not live_inventory:
            live_inventory = _legacy_inventory_from_devices(cam_config.get("devices", []))

    cam_slots = _normalize_camera_slots(live_inventory, cam_config.get("camera_slots"))
    cam_count = len(cam_slots)

    if cam_count == 0 or cam_id < 1 or cam_id > cam_count:
        return {
            "error": redirect(url_for("index", msg=t("camera.run_detection_again", "Bitte gehen Sie zurück zur Hauptseite und führen Sie die Kamera-Erkennung erneut aus.")))
        }

    slot = cam_slots[cam_id - 1]
    selected_camera_id = str(slot.get("camera_id") or "")
    cam_entry = next((cam for cam in live_inventory if str(cam.get("id") or "") == selected_camera_id), None)
    if not cam_entry and 0 <= (cam_id - 1) < len(live_inventory):
        cam_entry = live_inventory[cam_id - 1]

    if not cam_entry:
        return {
            "error": redirect(url_for("index", msg=t("camera.saved_assignment_unresolved", "Die gespeicherte Kamera-Zuordnung konnte nicht mehr aufgelöst werden.")))
        }

    dev = str(cam_entry.get("preferred_dev") or "").strip()
    if not dev:
        return {
            "error": redirect(url_for("index", msg=t("camera.no_valid_video_device", "Für diese Kamera konnte aktuell kein gültiges /dev/videoX gefunden werden.")))
        }

    # Konfiguration mit Live-Zuordnung aktualisieren, ohne extra im Idle zu scannen
    cfg_dirty = False
    if cam_config.get("camera_inventory") != live_inventory:
        cam_config["camera_inventory"] = live_inventory
        cfg_dirty = True
    if cam_config.get("camera_slots") != cam_slots:
        cam_config["camera_slots"] = cam_slots
        cfg_dirty = True
    live_devices = _devices_for_slots(live_inventory, cam_slots)
    if cam_config.get("devices") != live_devices:
        cam_config["devices"] = live_devices
        cfg_dirty = True
    if cfg_dirty:
        save_cam_config(cam_config)

    subprocess.run(["pkill", "-f", "mjpg_streamer"], capture_output=True, text=True)
    # Warten bis mjpg_streamer wirklich beendet ist und /dev/videoX freigegeben hat
    for _ in range(15):
        if subprocess.run(["pgrep", "-f", "mjpg_streamer"], capture_output=True).returncode != 0:
            break
        time.sleep(0.2)

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

        time.sleep(0.3)
        if p.poll() is not None:
            tail = tail_file(log_path, n=120, max_chars=7000)
            fmts = ""
            hint = ""
            if probe.get("ok"):
                fmts = ", ".join(sorted(list(probe.get("formats") or [])))
                if fmts:
                    fmts = f"<p><strong>{t('camera.detected_formats', 'Erkannte Formate')}:</strong> {fmts}</p>"
                if probe.get("formats") and ("MJPG" not in probe["formats"]) and ("YUYV" not in probe["formats"]):
                    hint = (
                        f"<p style='color:#ffb347'><strong>{t('generic.note', 'Hinweis')}:</strong> "
                        f"{t('camera.no_mjpg_yuyv_hint', 'Die Kamera bietet kein MJPG/YUYV an (z.B. nur H264). In diesem Fall kann mjpg_streamer oft keinen MJPEG-Stream erzeugen.')}</p>"
                    )
            else:
                err = (probe.get("error") or "").strip()
                if err:
                    fmts = f"<p><strong>{t('camera.v4l2_probe_error', 'v4l2 Probe-Fehler')}:</strong> {err}</p>"

            return {
                "error": _inline_notice_page(
                    t("camera.stream_start_failed_title", "Fehler: Kamera-Stream konnte nicht gestartet werden"),
                    (
                        f"<p>{t('camera.device_label', 'Device')}: <code>{dev}</code></p>"
                        f"<p>{t('camera.mjpg_streamer_input', 'mjpg_streamer Input')}: <code>{input_args}</code></p>"
                        f"{fmts}"
                        f"{hint}"
                        f"<p>{t('camera.log_last_lines', 'Log (letzte Zeilen)')}:</p>"
                        f"<pre style='white-space:pre-wrap;background:#0b0b0b;border:1px solid #333;padding:12px;border-radius:10px'>{tail}</pre>"
                        f"<p><a style='color:#3b82f6' href='{url_for('index')}'>{t('generic.back', 'Zurück')}</a></p>"
                    ),
                    status=500,
                )
            }

    except FileNotFoundError:
        return {
            "error": _inline_notice_page(
                t("camera.mjpg_streamer_missing_title", "Fehler: mjpg_streamer nicht gefunden"),
                (
                    f"<p>{t('camera.mjpg_streamer_missing_text', 'Bitte installieren Sie mjpg-streamer oder passen Sie den Aufruf im Script an.')}</p>"
                    f"<p><a href='{url_for('index')}'>{t('generic.back', 'Zurück')}</a></p>"
                ),
                status=500,
            )
        }

    host = request.host.split(":", 1)[0]
    stream_url = f"http://{host}:{port}/?action=stream"
    proxy_stream_url = url_for("camera.cam_focus_proxy", cam_id=cam_id)

    return {
        "cam_id": cam_id,
        "dev": dev,
        "port": port,
        "stream_url": stream_url,
        "proxy_stream_url": proxy_stream_url,
    }


@camera_bp.route("/api/calibration/mtime")
def api_calibration_mtime():
    cal_file = "/home/peter/.config/autodarts/calibration.json"
    if not os.path.exists(cal_file):
        return jsonify({"exists": False, "mtime": None, "display": None})
    try:
        mtime = os.path.getmtime(cal_file)
        from datetime import datetime
        dt = datetime.fromtimestamp(mtime)
        return jsonify({"exists": True, "mtime": int(mtime), "display": dt.strftime("%d.%m.%Y %H:%M")})
    except Exception as e:
        return jsonify({"exists": False, "mtime": None, "display": None, "error": str(e)})


@camera_bp.route("/schnellkonfiguration")
def schnellkonfiguration():
    return render_template("schnellkonfiguration.html")


@camera_bp.route("/board-manager/cam/<int:cam_id>")
def board_manager_cam(cam_id):
    if cam_id not in (0, 1, 2):
        return "Ungültige Kamera-ID", 400
    host = request.host.split(":", 1)[0]
    darts_url = f"http://{host}:3180"
    return render_template("board_cam.html", cam_id=cam_id, darts_url=darts_url)


@camera_bp.route("/set_cams", methods=["POST"])
def set_cams():
    cfg = load_cam_config()
    if not _camera_mode_runtime_active(cfg, autodarts_active=is_autodarts_active()):
        _set_camera_mode_state(cfg, False)
        save_cam_config(cfg)
        return redirect(url_for("index"))

    cameras = detect_camera_inventory(MAX_CAMERAS)
    slots = _normalize_camera_slots(cameras, cfg.get("camera_slots"))
    cfg["camera_inventory"] = cameras
    cfg["camera_slots"] = slots
    cfg["desired_cams"] = len(cameras)
    cfg["devices"] = _devices_for_slots(cameras, slots)
    save_cam_config(cfg)

    if not cameras:
        return redirect(url_for("index", msg=t("camera.none_connected", "Keine Kamera erkannt. Bitte Kamera anschlie�?en und erneut versuchen.")))

    return redirect(url_for("index"))


@camera_bp.route("/set_cam_slot/<int:slot_id>", methods=["POST"])
def set_cam_slot(slot_id: int):
    cfg = load_cam_config()
    if not _camera_mode_runtime_active(cfg, autodarts_active=is_autodarts_active()):
        _set_camera_mode_state(cfg, False)
        save_cam_config(cfg)
        return redirect(url_for("index"))

    cameras = cfg.get("camera_inventory", [])
    if not isinstance(cameras, list) or not cameras:
        cameras = detect_camera_inventory(MAX_CAMERAS)

    slots = _normalize_camera_slots(cameras, cfg.get("camera_slots"))
    if slot_id < 1 or slot_id > len(slots):
        cfg["camera_inventory"] = cameras
        cfg["camera_slots"] = slots
        cfg["devices"] = _devices_for_slots(cameras, slots)
        cfg["desired_cams"] = len(cameras)
        save_cam_config(cfg)
        return redirect(url_for("index"))

    selected_camera_id = str(request.form.get("camera_id") or "").strip()
    valid_ids = {str(cam.get("id") or "").strip() for cam in cameras if isinstance(cam, dict)}
    if selected_camera_id not in valid_ids:
        return redirect(url_for("index"))

    for slot in slots:
        if slot.get("slot") != slot_id and str(slot.get("camera_id") or "") == selected_camera_id:
            slot["camera_id"] = ""
    slots[slot_id - 1]["camera_id"] = selected_camera_id
    slots = _normalize_camera_slots(cameras, slots)

    cfg["camera_inventory"] = cameras
    cfg["camera_slots"] = slots
    cfg["devices"] = _devices_for_slots(cameras, slots)
    cfg["desired_cams"] = len(cameras)
    save_cam_config(cfg)

    return redirect(url_for("index"))


@camera_bp.route("/camera-mode/start", methods=["POST"])
def camera_mode_start():
    """Kamera-Einstellung starten: Autodarts zuerst stoppen, dann Kameras erkennen."""
    cfg = load_cam_config()

    # Erst stoppen damit Camera-Devices freigegeben werden, dann detecten
    subprocess.run(["/usr/local/bin/autodarts-userctl", "stop"], capture_output=True, text=True)
    subprocess.run(["pkill", "-f", "mjpg_streamer"], capture_output=True, text=True)
    # Warten bis Autodarts wirklich gestoppt ist (max 10 Sek)
    for _ in range(25):
        if not is_autodarts_active():
            break
        time.sleep(0.4)
    time.sleep(3.0)  # USB-Kamera-Devices brauchen ~3s zum Freigeben

    cameras = detect_camera_inventory(MAX_CAMERAS)
    slots = _normalize_camera_slots(cameras, cfg.get("camera_slots"))
    cfg["camera_inventory"] = cameras
    cfg["camera_slots"] = slots
    cfg["desired_cams"] = len(cameras)
    cfg["devices"] = _devices_for_slots(cameras, slots)

    if not cameras:
        _set_camera_mode_state(cfg, False)
        save_cam_config(cfg)
        return redirect(url_for("index", msg=t("camera.none_connected", "Keine Kamera erkannt. Bitte Kamera anschließen und erneut versuchen.")))

    _set_camera_mode_state(cfg, True)
    save_cam_config(cfg)

    return redirect(url_for("index"))


@camera_bp.route("/camera-mode/end", methods=["POST"])
def camera_mode_end():
    """Kamera-Einstellung beenden: Streams stoppen, Autodarts neu starten, Flag zurücksetzen."""
    subprocess.run(["pkill", "-f", "mjpg_streamer"], capture_output=True, text=True)
    subprocess.run(["/usr/local/bin/autodarts-userctl", "restart"], capture_output=True, text=True)

    cfg = load_cam_config()
    _set_camera_mode_state(cfg, False)
    save_cam_config(cfg)

    return redirect(url_for("index", ad_restarted=1))


@camera_bp.route("/cam/<int:cam_id>", methods=["GET"])
def cam_view(cam_id: int):
    ctx = _camera_stream_context(cam_id)
    if ctx.get("error"):
        return ctx["error"]

    return render_template(
        "cam_view.html",
        cam_id=cam_id,
        stream_url=ctx["stream_url"],
    )


@camera_bp.route("/cam/<int:cam_id>/focus", methods=["GET"])
def cam_focus(cam_id: int):
    ctx = _camera_stream_context(cam_id)
    if ctx.get("error"):
        return ctx["error"]

    return render_template(
        "cam_focus.html",
        cam_id=cam_id,
        stream_url=ctx["stream_url"],
        proxy_stream_url=ctx["proxy_stream_url"],
        focus_pdf_url=url_for("siemens_star_pdf"),
    )


@camera_bp.route("/cam/<int:cam_id>/proxy.mjpg", methods=["GET"])
def cam_focus_proxy(cam_id: int):
    port = STREAM_BASE_PORT + (cam_id - 1)
    upstream_url = f"http://127.0.0.1:{port}/?action=stream"

    def generate():
        resp = None
        try:
            req = urllib.request.Request(upstream_url, headers={"User-Agent": "AutodartsWebPanel/1.0"})
            resp = urllib.request.urlopen(req, timeout=15)
            while True:
                chunk = resp.read(16384)
                if not chunk:
                    break
                yield chunk
        except Exception:
            return
        finally:
            try:
                if resp is not None:
                    resp.close()
            except Exception:
                pass

    return Response(
        stream_with_context(generate()),
        mimetype="multipart/x-mixed-replace; boundary=boundarydonotcross",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )
