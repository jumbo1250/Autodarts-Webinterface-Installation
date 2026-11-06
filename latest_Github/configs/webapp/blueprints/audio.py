import re
import subprocess

from flask import Blueprint, render_template, request, jsonify

from webapp.modules.autodarts import _get_ad_user_ctx

audio_bp = Blueprint("audio", __name__)


def _run_pipewire(cmd_args, timeout=10):
    user, uid = _get_ad_user_ctx()
    return subprocess.run(
        ["sudo", "-u", user, "env",
         f"XDG_RUNTIME_DIR=/run/user/{uid}",
         f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
         f"HOME=/home/{user}",
         ] + cmd_args,
        capture_output=True, text=True, timeout=timeout
    )


def _get_audio_sinks():
    try:
        result = _run_pipewire(["wpctl", "status"])
        if result.returncode != 0:
            return []
        sinks = []
        in_sinks = False
        for line in result.stdout.splitlines():
            if "Sinks:" in line:
                in_sinks = True
                continue
            if in_sinks:
                if not line.strip() or "Sources:" in line or "Sink endpoints:" in line or "Filters:" in line:
                    break
                m = re.search(r"(\*?)\s*(\d+)\.\s+(.+?)(?:\s+\[|$)", line)
                if m:
                    name = re.sub(r"\s+\[.*", "", m.group(3)).strip()
                    sinks.append({
                        "id": m.group(2).strip(),
                        "name": name,
                        "is_default": bool(m.group(1).strip()),
                    })
        return sinks
    except Exception:
        return []


def _get_audio_volume():
    try:
        result = _run_pipewire(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        if result.returncode != 0:
            return None, None
        m = re.search(r"Volume:\s*([\d.]+)(\s*\[MUTED\])?", result.stdout)
        if not m:
            return None, None
        return round(float(m.group(1)) * 100), bool(m.group(2))
    except Exception:
        return None, None


def _pipewire_alsa_installed() -> bool:
    try:
        r = subprocess.run(["dpkg", "-s", "pipewire-alsa"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return True


@audio_bp.route("/audio")
def audio():
    sinks = _get_audio_sinks()
    volume, muted = _get_audio_volume()
    return render_template(
        "audio.html",
        sinks=sinks,
        volume=volume if volume is not None else 80,
        muted=bool(muted),
        pipewire_available=volume is not None,
        pipewire_alsa_installed=_pipewire_alsa_installed(),
    )


@audio_bp.route("/api/audio/volume", methods=["POST"])
def api_audio_volume():
    try:
        vol = max(0, min(100, int(request.form.get("volume", 80))))
    except (ValueError, TypeError):
        return jsonify(ok=False, msg="Ungültiger Wert")
    try:
        result = _run_pipewire(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{vol / 100:.2f}"])
        if result.returncode != 0:
            return jsonify(ok=False, msg="Fehler beim Setzen der Lautstärke")
    except Exception as e:
        return jsonify(ok=False, msg=str(e))
    return jsonify(ok=True, volume=vol)


@audio_bp.route("/api/audio/mute", methods=["POST"])
def api_audio_mute():
    state = request.form.get("mute", "toggle")
    if state not in ("0", "1", "toggle"):
        state = "toggle"
    try:
        result = _run_pipewire(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", state])
        if result.returncode != 0:
            return jsonify(ok=False, msg="Fehler beim Mute")
        _, muted = _get_audio_volume()
        return jsonify(ok=True, muted=bool(muted))
    except Exception as e:
        return jsonify(ok=False, msg=str(e))


@audio_bp.route("/api/audio/default", methods=["POST"])
def api_audio_default():
    sink_id = request.form.get("sink_id", "").strip()
    if not re.match(r"^\d+$", sink_id):
        return jsonify(ok=False, msg="Ungültige Geräte-ID")
    try:
        result = _run_pipewire(["wpctl", "set-default", sink_id])
        if result.returncode != 0:
            return jsonify(ok=False, msg="Fehler beim Setzen des Standard-Geräts")
    except Exception as e:
        return jsonify(ok=False, msg=str(e))
    return jsonify(ok=True)


@audio_bp.route("/api/audio/install-pipewire-alsa", methods=["POST"])
def api_audio_install_pipewire_alsa():
    if _pipewire_alsa_installed():
        return jsonify(ok=True, already=True)
    try:
        subprocess.Popen(
            ["apt-get", "install", "-y", "pipewire-alsa"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        return jsonify(ok=False, msg=str(e))
    return jsonify(ok=True, already=False)


@audio_bp.route("/api/audio/test", methods=["POST"])
def api_audio_test():
    user, uid = _get_ad_user_ctx()
    try:
        subprocess.Popen(
            ["sudo", "-u", user, "env",
             f"XDG_RUNTIME_DIR=/run/user/{uid}",
             f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
             f"HOME=/home/{user}",
             "timeout", "2", "speaker-test", "-t", "sine", "-f", "1000", "-l", "1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        return jsonify(ok=False, msg=str(e))
    return jsonify(ok=True)

