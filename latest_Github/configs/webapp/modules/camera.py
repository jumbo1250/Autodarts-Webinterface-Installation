import os
import re
import json
import subprocess

from webapp.config import (
    CAM_CONFIG_PATH,
    CAMERA_MODE_SESSION,
    MAX_CAMERAS,
    MAX_VIDEO_INDEX,
)


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



def _camera_mode_runtime_active(cfg: dict | None, autodarts_active: bool | None = None) -> bool:
    cfg = cfg or {}
    if not bool(cfg.get("camera_mode", False)):
        return False
    if str(cfg.get("camera_mode_session") or "") != CAMERA_MODE_SESSION:
        return False
    if autodarts_active is True:
        return False
    return True


def _set_camera_mode_state(cfg: dict, enabled: bool):
    cfg["camera_mode"] = bool(enabled)
    if enabled:
        cfg["camera_mode_session"] = CAMERA_MODE_SESSION
    else:
        cfg["camera_mode"] = False
        cfg.pop("camera_mode_session", None)



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
        return {"ok": False, "formats": set(), "resolutions": {}, "error": "v4l2-ctl nicht verfÃ¼gbar oder Timeout.", "raw": ""}

    raw = (r.stdout or "") + ("\n" + (r.stderr or "") if r.stderr else "")
    if r.returncode != 0:
        # HÃ¤ufig: Permission denied / Not a capture device / busy
        err = (r.stderr or r.stdout or "").strip() or f"v4l2-ctl returncode {r.returncode}"
        return {"ok": False, "formats": set(), "resolutions": {}, "error": err, "raw": raw}

    fmt = None
    formats: set[str] = set()
    resolutions: dict[str, list[tuple[int, int]]] = {}

    re_fmt = re.compile(r"(?:Pixel\s+Format:\s+\'([A-Z0-9]+)\'|\[\d+\]:\s+\'([A-Z0-9]+)\')")
    re_size = re.compile(r"Size:\s+Discrete\s+(\d+)x(\d+)")
    for line in (r.stdout or "").splitlines():
        m = re_fmt.search(line)
        if m:
            fmt = m.group(1) or m.group(2)
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
    Erkennt Kameras mÃ¶glichst zuverlÃ¤ssig.

    1) Wenn verfÃ¼gbar, nutzt es 'v4l2-ctl --list-devices', um physische USB-Kameras zu gruppieren
       und pro Kamera genau EIN /dev/videoX zu wÃ¤hlen (verhindert Doppel-/Meta-Devices).
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
                    # Neue GerÃ¤tegruppe beginnt
                    if current_name and current_videos:
                        groups.append((current_name, current_videos))
                    current_name = line.strip().rstrip(":")
                    current_videos = []
                else:
                    # EingrÃ¼ckte Zeile -> Device
                    path = line.strip()
                    if path.startswith("/dev/video"):
                        current_videos.append(path)

            # letzte Gruppe Ã¼bernehmen
            if current_name and current_videos:
                groups.append((current_name, current_videos))

            # Gruppen filtern: echte Kameras bevorzugen, System/Meta-GerÃ¤te eher raus
            def _looks_like_camera(name: str) -> bool:
                n = (name or "").lower()

                # harte AusschlÃ¼sse: typische System/Codec/ISP/Decoder-GerÃ¤te
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

                # Default: konservativ, sonst findet man ohne Kamera gerne 'GeistergerÃ¤te'
                return False

            cam_groups = [g for g in groups if _looks_like_camera(g[0])]

            devices = []
            for name, videos in cam_groups:
                videos_sorted = sorted(videos)
                # Einige Kameras liefern mehrere /dev/videoX Nodes (Meta/H264/etc.).
                # Wir wÃ¤hlen nach MÃ¶glichkeit den Node, der MJPG (oder YUYV) anbietet.
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


def _camera_symlink_map() -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {"by-id": {}, "by-path": {}}
    for kind, base in (("by-id", "/dev/v4l/by-id"), ("by-path", "/dev/v4l/by-path")):
        if not os.path.isdir(base):
            continue
        try:
            for name in sorted(os.listdir(base)):
                full = os.path.join(base, name)
                try:
                    real = os.path.realpath(full)
                except Exception:
                    continue
                if not real.startswith("/dev/video"):
                    continue
                out[kind].setdefault(real, []).append(full)
        except Exception:
            continue
    return out


def _camera_aliases_for_device(dev: str, symlink_map: dict[str, dict[str, list[str]]] | None = None) -> tuple[list[str], list[str]]:
    symlink_map = symlink_map or _camera_symlink_map()
    by_id = list(symlink_map.get("by-id", {}).get(dev, []))
    by_path = list(symlink_map.get("by-path", {}).get(dev, []))
    return by_id, by_path


def _sanitize_camera_key(raw: str) -> str:
    s = (raw or "").strip().lower()
    s = re.sub(r"[^a-z0-9._-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "camera"


def _camera_stable_id(group_name: str, dev: str, symlink_map: dict[str, dict[str, list[str]]] | None = None) -> str:
    symlink_map = symlink_map or _camera_symlink_map()
    by_id, by_path = _camera_aliases_for_device(dev, symlink_map)
    if by_id:
        return f"byid:{os.path.basename(by_id[0])}"
    if by_path:
        return f"bypath:{os.path.basename(by_path[0])}"

    info = _v4l2_device_info(dev)
    bus = (info.get("bus info") or "").strip()
    card = (info.get("card type") or group_name or dev).strip()
    if bus:
        return f"bus:{_sanitize_camera_key(bus)}:{_sanitize_camera_key(card)}"
    return f"dev:{_sanitize_camera_key(group_name or dev)}"


def _camera_label(group_name: str, dev: str, symlink_map: dict[str, dict[str, list[str]]] | None = None) -> str:
    symlink_map = symlink_map or _camera_symlink_map()
    info = _v4l2_device_info(dev)
    base = (group_name or info.get("card type") or dev or "Kamera").strip().rstrip(":")
    by_id, by_path = _camera_aliases_for_device(dev, symlink_map)

    extra = ""
    if by_id:
        extra = os.path.basename(by_id[0]).replace("-video-index0", "")
    elif by_path:
        extra = os.path.basename(by_path[0]).replace("-video-index0", "")
    elif info.get("bus info"):
        extra = str(info.get("bus info") or "").strip()

    if extra and extra not in base:
        return f"{base} [{extra}]"
    return base


def _looks_like_camera_group(name: str) -> bool:
    n = (name or "").lower()
    bad = ["codec", "isp", "rpivid", "v4l2loopback", "loopback", "virtual", "m2m", "mem2mem", "decoder", "encoder"]
    if any(b in n for b in bad):
        return False
    if "usb" in n or "uvc" in n or "webcam" in n or "camera" in n:
        return True
    sensor = ["ov", "imx", "ar", "gc", "s5k", "unicam", "csi"]
    if any(s in n for s in sensor):
        return True
    return False


def detect_camera_inventory(limit: int = MAX_CAMERAS) -> list[dict]:
    limit = max(0, min(MAX_CAMERAS, int(limit)))
    symlink_map = _camera_symlink_map()
    cameras: list[dict] = []

    try:
        result = subprocess.run(["v4l2-ctl", "--list-devices"], capture_output=True, text=True)
        if result.returncode == 0:
            lines = result.stdout.splitlines()
            groups: list[tuple[str, list[str]]] = []
            current_name = None
            current_videos: list[str] = []

            for line in lines:
                if line.strip() == "":
                    continue
                if not line.startswith("	") and not line.startswith(" "):
                    if current_name and current_videos:
                        groups.append((current_name, current_videos))
                    current_name = line.strip().rstrip(":")
                    current_videos = []
                else:
                    dev_path = line.strip()
                    if dev_path.startswith("/dev/video"):
                        current_videos.append(dev_path)

            if current_name and current_videos:
                groups.append((current_name, current_videos))

            for name, videos in groups:
                if not _looks_like_camera_group(name):
                    continue
                videos_sorted = sorted(videos)
                preferred_dev = _pick_best_video_device(videos_sorted) or videos_sorted[0]
                if not _is_probably_camera_device(preferred_dev):
                    continue
                by_id, by_path = _camera_aliases_for_device(preferred_dev, symlink_map)
                info = _v4l2_device_info(preferred_dev)
                cameras.append({
                    "id": _camera_stable_id(name, preferred_dev, symlink_map),
                    "label": _camera_label(name, preferred_dev, symlink_map),
                    "group_name": name,
                    "preferred_dev": preferred_dev,
                    "video_devs": videos_sorted,
                    "bus_info": info.get("bus info", ""),
                    "card_type": info.get("card type", ""),
                    "by_id": by_id[0] if by_id else "",
                    "by_path": by_path[0] if by_path else "",
                })
                if limit and len(cameras) >= limit:
                    break
            if cameras:
                return cameras
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[autodarts-web] Warnung detect_camera_inventory mit v4l2-ctl: {e}")

    for idx in range(MAX_VIDEO_INDEX):
        dev = f"/dev/video{idx}"
        if not os.path.exists(dev) or not _is_probably_camera_device(dev):
            continue
        by_id, by_path = _camera_aliases_for_device(dev, symlink_map)
        info = _v4l2_device_info(dev)
        name = info.get("card type") or dev
        cameras.append({
            "id": _camera_stable_id(name, dev, symlink_map),
            "label": _camera_label(name, dev, symlink_map),
            "group_name": name,
            "preferred_dev": dev,
            "video_devs": [dev],
            "bus_info": info.get("bus info", ""),
            "card_type": info.get("card type", ""),
            "by_id": by_id[0] if by_id else "",
            "by_path": by_path[0] if by_path else "",
        })
        if limit and len(cameras) >= limit:
            break
    return cameras


def _legacy_inventory_from_devices(devices: list[str]) -> list[dict]:
    symlink_map = _camera_symlink_map()
    out: list[dict] = []
    for dev in devices or []:
        if not isinstance(dev, str) or not dev.strip():
            continue
        dev = dev.strip()
        info = _v4l2_device_info(dev)
        name = info.get("card type") or dev
        by_id, by_path = _camera_aliases_for_device(dev, symlink_map)
        out.append({
            "id": _camera_stable_id(name, dev, symlink_map),
            "label": _camera_label(name, dev, symlink_map),
            "group_name": name,
            "preferred_dev": dev,
            "video_devs": [dev],
            "bus_info": info.get("bus info", ""),
            "card_type": info.get("card type", ""),
            "by_id": by_id[0] if by_id else "",
            "by_path": by_path[0] if by_path else "",
        })
    return out


def _normalize_camera_slots(camera_inventory: list[dict], stored_slots) -> list[dict]:
    cams = [c for c in (camera_inventory or []) if isinstance(c, dict) and str(c.get("id") or "").strip()]
    by_id = {str(c.get("id")): c for c in cams}
    camera_ids = [str(c.get("id")) for c in cams]
    raw_slots = stored_slots if isinstance(stored_slots, list) else []

    slots: list[dict] = []
    used: set[str] = set()
    for idx in range(len(cams)):
        chosen = ""
        if idx < len(raw_slots):
            raw = raw_slots[idx]
            if isinstance(raw, dict):
                candidate = str(raw.get("camera_id") or "").strip()
            else:
                candidate = str(raw or "").strip()
            if candidate in by_id and candidate not in used:
                chosen = candidate

        if not chosen:
            for candidate in camera_ids:
                if candidate not in used:
                    chosen = candidate
                    break

        if chosen:
            used.add(chosen)
        cam = by_id.get(chosen, {})
        slots.append({
            "slot": idx + 1,
            "camera_id": chosen,
            "label": cam.get("label", ""),
            "device": cam.get("preferred_dev", ""),
        })
    return slots


def _devices_for_slots(camera_inventory: list[dict], camera_slots: list[dict]) -> list[str]:
    by_id = {str(c.get("id")): c for c in (camera_inventory or []) if isinstance(c, dict)}
    out: list[str] = []
    for slot in camera_slots or []:
        cam = by_id.get(str((slot or {}).get("camera_id") or ""))
        if cam and cam.get("preferred_dev"):
            out.append(str(cam.get("preferred_dev")))
    return out
