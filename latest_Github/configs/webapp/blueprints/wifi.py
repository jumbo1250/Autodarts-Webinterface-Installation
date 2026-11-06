"""Blueprint: WLAN-Client, Access-Point und Verbindungstest.

Aus autodarts-web.py ausgelagert (Blueprint-Refactoring).
"""
import json
import os
import threading
import time
import subprocess

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    jsonify,
    session,
    flash,
    g,
)

from webapp.config import *
from webapp.utils.i18n import t
from webapp.modules.wifi import *
from webapp.modules.wifi import (
    _get_default_route_interface,
    _get_connected_wifi_interface,
    _load_ap_client_internet_status,
    _nmcli_terse_split,
    _nmcli_unescape,
    _schedule_channel_apply_without_reboot,
    _trigger_legacy_ap_fix_async,
    _remove_wifi_reboot_state,
    _active_wifi_connection_name,
    get_direct_connect_state,
)

network_bp = Blueprint("network", __name__)


def _ap_connection_active() -> bool:
    """Returns True if AP_CONNECTION_NAME is currently active in NetworkManager."""
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,STATE", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=3,
        )
        for line in r.stdout.splitlines():
            parts = line.strip().split(":", 1)
            if parts[0].strip() == AP_CONNECTION_NAME:
                return True
        return False
    except Exception:
        return True




@network_bp.route("/wifi/reboot/cancel", methods=["POST"])
def wifi_reboot_cancel():
    state, _stopped = cancel_scheduled_wifi_channel_reboot(remove_state=False)
    target_channel = str(state.get("target_channel") or "")
    ap_ssid = str(state.get("ap_ssid") or get_ap_ssid() or "Autodartsinstall1")

    if not target_channel:
        _remove_wifi_reboot_state()
        clear_manual_wifi_connect_marker()
        return jsonify(
            ok=False,
            msg=t("wifi.reboot_no_longer_pending", "Der geplante Neustart ist nicht mehr aktiv."),
        ), 409

    if not _schedule_channel_apply_without_reboot(target_channel):
        return jsonify(
            ok=False,
            msg=t("wifi.reboot_cancel_failed", "Neustart konnte nicht sicher abgebrochen werden."),
        ), 500

    return jsonify(
        ok=True,
        msg=t(
            "wifi.reboot_cancelled_channel_switch",
            "Neustart abgebrochen. Der WLAN-Kanal wird jetzt gewechselt. Bitte danach erneut mit „{ssid}“ verbinden.",
            ssid=ap_ssid,
        ),
    )


@network_bp.route("/api/wifi/signal", methods=["GET"])
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


@network_bp.route("/api/ap-client-internet-status", methods=["GET"])
def api_ap_client_internet_status():
    st = _load_ap_client_internet_status()
    st["ok"] = True
    return jsonify(st)


@network_bp.get("/api/wifi/scan")
def api_wifi_scan():
    """
    Scan available WiFi networks via nmcli and return a clean list for a dropdown.
    Does NOT change connection logic – only discovery.
    With ?trigger=1: runs nmcli rescan first (slower, used by Refresh button).
    Without trigger: uses cached results only (--rescan no, fast).
    """
    trigger = request.args.get("trigger") == "1"
    rescan_flag = "yes" if trigger else "no"
    try:
        cmd = ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", rescan_flag]
        timeout = 20 if trigger else 8
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return jsonify(ok=False, msg=t("wifi.scan_timeout", "WLAN-Scan dauert zu lange (Timeout). Bitte erneut versuchen.")), 504
    except Exception as e:
        return jsonify(ok=False, msg=t("wifi.scan_failed1", "WLAN-Scan fehlgeschlagen: {error}", error=e)), 500

    if r.returncode != 0:
        return jsonify(ok=False, msg=t("wifi.nmcli_error", "nmcli Fehler: {error}", error=interpret_nmcli_error(r.stdout, r.stderr))), 500

    # Merge duplicate SSIDs: keep best signal, combine security labels
    merged: dict[str, dict] = {}

    for raw in r.stdout.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        parts = _nmcli_terse_split(raw)
        # Expected: IN-USE,SSID,SIGNAL,SECURITY (some may be missing)
        while len(parts) < 4:
            parts.append("")
        in_use, ssid, signal, sec = parts[0], parts[1], parts[2], parts[3]
        ssid = _nmcli_unescape((ssid or "").strip())
        if not ssid:
            # hidden SSID: can't be selected reliably -> user can use manual entry
            continue
        try:
            sig_i = int((signal or "0").strip() or 0)
        except Exception:
            sig_i = 0
        sec = _nmcli_unescape((sec or "").strip())
        key = ssid

        item = merged.get(key)
        if item is None:
            merged[key] = {
                "ssid": ssid,
                "signal": sig_i,
                "security": sec,
                "in_use": (in_use.strip() == "*"),
            }
        else:
            # keep best signal; mark in_use if any entry is in use
            item["signal"] = max(int(item.get("signal") or 0), sig_i)
            item["in_use"] = bool(item.get("in_use")) or (in_use.strip() == "*")
            # combine security labels
            if sec and sec not in (item.get("security") or ""):
                item["security"] = (item.get("security") + ("/" if item.get("security") else "") + sec)

    networks = sorted(merged.values(), key=lambda x: (x.get("in_use") is True, int(x.get("signal") or 0)), reverse=True)

    # Hide the AP SSID from results when a WiFi dongle is present (users shouldn't connect the stick to the AP)
    if wifi_dongle_present():
        ap_ssid_hide = get_ap_ssid()
        if ap_ssid_hide:
            networks = [n for n in networks if n.get("ssid") != ap_ssid_hide]

    return jsonify(ok=True, networks=networks)


@network_bp.route("/wifi", methods=["GET", "POST"])
def wifi():
    message = ""
    success = False
    show_wifi_reboot_popup = False

    if request.method == "POST":
        ssid = request.form.get("ssid", "").strip()
        password = request.form.get("password", "").strip()

        if not ssid:
            message = t("wifi.enter_ssid", "Bitte WLAN-Namen (SSID) eingeben.")
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
                message = t("wifi.create_connection_failed", "Fehler beim Anlegen der WLAN-Verbindung: {error}", error=interpret_nmcli_error(add.stdout, add.stderr))
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
                # Marker setzen, damit der Fix bei einem notwendigen Funkwechsel kurz wartet.
                # So sieht der Benutzer die Meldung noch, bevor die Setup-Verbindung getrennt wird.
                mark_manual_wifi_connect()
                up = subprocess.run(
                    ["nmcli", "connection", "up", WIFI_CONNECTION_NAME],
                    capture_output=True,
                    text=True,
                )

                if up.returncode == 0:
                    ap_ssid = get_ap_ssid() or "Autodartsinstall1"
                    target_channel = get_setup_wifi_target_channel()
                    if target_channel and schedule_wifi_channel_reboot(target_channel):
                        show_wifi_reboot_popup = True
                        message = t("wifi.connected_successfully", "Erfolgreich mit WLAN verbunden.")
                    elif target_channel:
                        # Sicherer Fallback: Alte AP-Logik wieder freigeben und sofort anwenden.
                        clear_manual_wifi_connect_marker()
                        _trigger_legacy_ap_fix_async()
                        message = t(
                            "wifi.connected_setup_wifi_reconnect",
                            "WLAN verbunden. Die Verbindung wird gleich kurz optimiert. Bitte danach erneut mit „{ssid}“ verbinden.",
                            ssid=ap_ssid,
                        )
                    else:
                        clear_manual_wifi_connect_marker()
                        cancel_scheduled_wifi_channel_reboot(remove_state=True)
                        message = t("wifi.connected_successfully", "Erfolgreich mit WLAN verbunden.")
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
                            ap_ssid = get_ap_ssid() or "Autodartsinstall1"
                            target_channel = get_setup_wifi_target_channel()
                            if target_channel and schedule_wifi_channel_reboot(target_channel):
                                show_wifi_reboot_popup = True
                                message = t(
                                    "wifi.retry_success_after_reset",
                                    "Der zweite Verbindungsversuch war erfolgreich."
                                )
                            elif target_channel:
                                clear_manual_wifi_connect_marker()
                                _trigger_legacy_ap_fix_async()
                                message = t(
                                    "wifi.connected_setup_wifi_reconnect",
                                    "WLAN verbunden. Die Verbindung wird gleich kurz optimiert. Bitte danach erneut mit „{ssid}“ verbinden.",
                                    ssid=ap_ssid,
                                )
                            else:
                                clear_manual_wifi_connect_marker()
                                cancel_scheduled_wifi_channel_reboot(remove_state=True)
                                message = t(
                                    "wifi.retry_success_after_reset",
                                    "Verbindung fehlgeschlagen, wird erneut versucht ...\nDer zweite Versuch war erfolgreich. (Hinweis: WLAN-USB-Stick wurde kurz neu initialisiert.)"
                                )
                            success = True
                        else:
                            clear_manual_wifi_connect_marker()
                            message = t(
                                "wifi.retry_failed_after_reset",
                                "Verbindung fehlgeschlagen, wird erneut versucht ...\nAuch der zweite Versuch ist fehlgeschlagen: {error}",
                                error=interpret_nmcli_error(up2.stdout, up2.stderr),
                            )
                    else:
                        clear_manual_wifi_connect_marker()
                        message = t("wifi.connect_failed", "Verbindung konnte nicht hergestellt werden: {error}", error=interpret_nmcli_error(up.stdout, up.stderr))

    # Aktuellen Status des WLAN-Dongles anzeigen
    ssid_cur, ip_cur = get_wifi_status()
    wifi_signal = get_wifi_signal_percent()
    if ssid_cur and ip_cur:
        current_info = t("wifi.current_info_connected", "Aktuell verbunden mit <strong>{ssid}</strong> (IP {ip})", ssid=ssid_cur, ip=ip_cur) + (t("wifi.current_info_signal", " · Signal: <strong>{signal}%</strong>.", signal=wifi_signal) if wifi_signal is not None else ".")
    elif ssid_cur and not ip_cur:
        current_info = t("wifi.current_info_connected_no_ipv4", "WLAN verbunden mit <strong>{ssid}</strong>, aber es wurde keine IPv4-Adresse vergeben.", ssid=ssid_cur) + (t("wifi.current_info_signal_paren", " (Signal: <strong>{signal}%</strong>)", signal=wifi_signal) if wifi_signal is not None else "")
    else:
        current_info = t("wifi.current_info_not_connected", "Der USB-Dongle ist aktuell mit keinem WLAN verbunden.")

    dongle_present = wifi_dongle_present()
    ap_active = _ap_connection_active()
    ap_ssid_display = get_ap_ssid() or AP_CONNECTION_NAME
    direct_connected, direct_ssid, direct_ip = get_direct_connect_state()

    return render_template(
        "wifi.html",
        message=message,
        success=success,
        current_info=current_info,
        wifi_interface=WIFI_INTERFACE,
        wifi_connection_name=WIFI_CONNECTION_NAME,
        show_wifi_reboot_popup=show_wifi_reboot_popup,
        wifi_reboot_countdown_seconds=WIFI_REBOOT_COUNTDOWN_SECONDS,
        admin_unlocked=bool(session.get('admin_unlocked', False)),
        dongle_present=dongle_present,
        ap_active=ap_active,
        ap_ssid=ap_ssid_display,
        direct_connected=direct_connected,
        direct_ssid=direct_ssid,
        direct_ip=direct_ip,
    )


@network_bp.route("/wifi/autoconnect/<mode>", methods=["POST"])
def wifi_autoconnect(mode: str):
    # Schutz: nur im Admin-Modus
    if not bool(session.get("admin_unlocked", False)):
        flash(t("admin.unlock_first", "Bitte zuerst im Admin-Bereich entsperren."), "warning")
        return redirect(url_for("index", admin="1") + "#admin_details")

    enable = (mode or "").lower() in ("on", "enable", "yes", "1", "true")
    conn = _active_wifi_connection_name(WIFI_INTERFACE)
    if not conn:
        flash(t("wifi.no_active_profile_on_uplink", "Kein aktives WLAN-Profil auf dem Internet-WLAN-Interface gefunden."), "warning")
        return redirect(url_for("network.wifi"))

    try:
        cmd = ["nmcli", "connection", "modify", conn, "connection.autoconnect", ("yes" if enable else "no")]
        if os.geteuid() != 0:
            cmd = ["sudo", "-n"] + cmd
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=6.0)
        if r.returncode == 0:
            flash(t("wifi.autoconnect_set", "Autoconnect {state} für: {conn}", state=(t("generic.enabled", "aktiviert") if enable else t("generic.disabled", "deaktiviert")), conn=conn), "success")
        else:
            err = (r.stderr or r.stdout or "").strip()
            flash(t("wifi.autoconnect_set_failed", "Autoconnect konnte nicht gesetzt werden: {error}", error=(err or t("generic.unknown", "unbekannt"))), "danger")
    except Exception as e:
        flash(t("wifi.autoconnect_error", "Autoconnect Fehler: {error}", error=e), "danger")

    return redirect(url_for("network.wifi"))


@network_bp.route("/wifi/forget-saved", methods=["POST"])
def wifi_forget_saved():
    # Schutz: nur im Admin-Modus
    if not bool(session.get("admin_unlocked", False)):
        flash(t("admin.unlock_first", "Bitte zuerst im Admin-Bereich entsperren."), "warning")
        return redirect(url_for("index", admin="1") + "#admin_details")

    KEEP = "Autodarts-AP"
    deleted = []
    try:
        # NAME,TYPE -> wifi oder 802-11-wireless
        r = subprocess.run(
            ["nmcli", "-g", "NAME,TYPE", "connection", "show"],
            capture_output=True,
            text=True,
            timeout=4.0,
        )
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or "nmcli error").strip())

        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            name, typ = line.split(":", 1)
            typ = typ.strip().lower()
            name = name.strip()
            if typ not in ("wifi", "802-11-wireless"):
                continue
            if name == KEEP:
                continue

            cmd = ["nmcli", "connection", "delete", name]
            if os.geteuid() != 0:
                cmd = ["sudo", "-n"] + cmd
            subprocess.run(cmd, capture_output=True, text=True, timeout=6.0)
            deleted.append(name)

        if deleted:
            flash(t("wifi.saved_connections_deleted", "Gespeicherte WLAN-Verbindungen gelöscht: {names}", names=", ".join(deleted)), "success")
        else:
            flash(t("wifi.no_saved_connections_found", "Keine gespeicherten WLAN-Verbindungen gefunden (außer Autodarts-AP)."), "info")
    except Exception as e:
        flash(t("wifi.delete_saved_connections_failed", "Konnte gespeicherte WLANs nicht löschen: {error}", error=e), "danger")

    return redirect(url_for("network.wifi"))


@network_bp.route("/wifi/ping/start", methods=["POST"])
def wifi_ping_start():
    ok, msg, job_id = start_ping_test(count=30)
    iface_label = None
    if job_id and job_id in PING_JOBS:
        iface_label = PING_JOBS[job_id].get("iface_label")
    return jsonify({"ok": bool(ok), "msg": msg, "job_id": job_id, "iface_label": iface_label})


@network_bp.route("/wifi/ping/status/<job_id>", methods=["GET"])
def wifi_ping_status(job_id: str):
    job = PING_JOBS.get(job_id)
    if not job:
        return jsonify({"ok": False, "msg": t("jobs.not_found", "Job nicht gefunden.")}), 404

    # Auto-cleanup nach 15min
    try:
        if time.time() - float(job.get("started", 0)) > 900:
            PING_JOBS.pop(job_id, None)
    except Exception:
        pass

    return jsonify({
        "ok": True,
        "target": job.get("target"),
        "iface": job.get("iface"),
        "iface_label": job.get("iface_label"),
        "count": job.get("count", 30),
        "progress": job.get("progress", 0),
        "received": job.get("received", 0),
        "done": bool(job.get("done", False)),
        "min_ms": job.get("min_ms"),
        "max_ms": job.get("max_ms"),
        "avg_ms": job.get("avg_ms"),
        "error": job.get("error"),
    })


@network_bp.route("/wifi/ping/ui", methods=["GET"])
def wifi_ping_ui():
    """Fallback/UI-Seite für den Verbindungstest (falls JS im Hauptscreen nicht greift)."""
    return render_template("wifi_ping_ui.html")


@network_bp.route("/ap", methods=["GET", "POST"])
def ap_config():
    message = ""
    success = False
    current_ssid = get_ap_ssid()
    ap_choices = g.ap_ssid_choices
    selected_ssid = current_ssid if (current_ssid in ap_choices) else (ap_choices[0] if ap_choices else "")

    if request.method == "POST":
        new_ssid = (request.form.get("ap_ssid_select") or "").strip()

        if not new_ssid:
            message = t("ap.select_name", "Bitte einen Access-Point-Namen auswählen.")
        elif new_ssid not in ap_choices:
            message = t("generic.invalid_selection", "Ungültige Auswahl. Bitte einen Namen aus der Liste wählen.")
        elif len(new_ssid) > 32:
            message = t("ap.name_too_long", "Der Access-Point-Name ist zu lang (max. 32 Zeichen).")
        else:
            res = subprocess.run(
                ["nmcli", "connection", "modify", AP_CONNECTION_NAME, "802-11-wireless.ssid", new_ssid],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                message = t("ap.rename_failed", "Fehler beim Ändern des Access-Point-Namens: {error}", error=interpret_nmcli_error(res.stdout, res.stderr))
            else:
                subprocess.run(["nmcli", "connection", "down", AP_CONNECTION_NAME], capture_output=True, text=True)
                subprocess.run(["nmcli", "connection", "up", AP_CONNECTION_NAME], capture_output=True, text=True)
                success = True
                current_ssid = new_ssid
                message = t("ap.renamed", "Access-Point-Name wurde geändert auf „{ssid}“.", ssid=new_ssid)

    return render_template(
        "ap_config.html",
        message=message,
        success=success,
        current_ssid=current_ssid,
        ap_connection_name=AP_CONNECTION_NAME,
        ap_choices=ap_choices,
        selected_ssid=selected_ssid,
    )


@network_bp.route("/api/wifi/save-direct-connect", methods=["POST"])
def api_wifi_save_direct_connect():
    ssid = (request.form.get("ssid") or "").strip()
    password = (request.form.get("password") or "").strip()

    if not ssid:
        return jsonify(ok=False, msg=t("wifi.enter_ssid", "Bitte WLAN-Namen (SSID) eingeben."))

    connect_data = {"ssid": ssid, "password": password}
    json_path = "/var/lib/autodarts/wifi_connect_no_stick.json"
    try:
        os.makedirs("/var/lib/autodarts", exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(connect_data, f)
    except Exception as e:
        return jsonify(ok=False, msg=t("wifi.direct_save_failed", "Fehler beim Speichern: {error}", error=e))

    # AP-Autoconnect deaktivieren damit er beim nächsten Boot nicht startet
    try:
        subprocess.run(
            ["nmcli", "connection", "modify", AP_CONNECTION_NAME, "connection.autoconnect", "no"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        pass

    # Reboot nach kurzem Delay damit Response noch zugestellt wird
    def _reboot():
        time.sleep(3)
        subprocess.run(["systemctl", "reboot"], capture_output=True)

    threading.Thread(target=_reboot, daemon=True).start()
    return jsonify(ok=True, msg=t("wifi.direct_rebooting", "Pi wird neu gestartet…"))


@network_bp.route("/api/wifi/ap-toggle", methods=["POST"])
def api_wifi_ap_toggle():
    state = (request.form.get("state") or "").strip().lower()
    if state not in ("on", "off"):
        return jsonify(ok=False, msg="Invalid state"), 400
    action = "up" if state == "on" else "down"
    cmd = ["nmcli", "connection", action, AP_CONNECTION_NAME]
    if os.geteuid() != 0:
        cmd = ["sudo", "-n"] + cmd
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return jsonify(ok=False, msg=interpret_nmcli_error(r.stdout, r.stderr))
    except Exception as e:
        return jsonify(ok=False, msg=str(e))
    return jsonify(ok=True, state=state)
