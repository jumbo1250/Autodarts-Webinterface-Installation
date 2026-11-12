import re
import subprocess


_HOSTNAME_RE = re.compile(r'^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$')


def validate_hostname(name: str) -> str | None:
    """Returns None if valid, error message string if invalid."""
    if not name:
        return "Name darf nicht leer sein."
    if len(name) > 63:
        return f"Name zu lang (max. 63 Zeichen, aktuell {len(name)})."
    if not _HOSTNAME_RE.match(name):
        return (
            "Ungültiger Name. Erlaubt: Buchstaben (a–z, A–Z), Ziffern (0–9) und Bindestriche (-).\n"
            "Darf nicht mit einem Bindestrich beginnen oder enden. Keine Leerzeichen oder Sonderzeichen."
        )
    return None


def get_hostname() -> str:
    try:
        r = subprocess.run(["hostname"], capture_output=True, text=True, timeout=2)
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _cloud_init_preserve_hostname() -> bool:
    """
    Schreibt /etc/cloud/cloud.cfg.d/99-autodarts-hostname.cfg mit
    preserve_hostname: true  → cloud-init überschreibt den Hostname nach Reboot nicht mehr.
    manage_etc_hosts: false  → /etc/hosts wird ebenfalls nicht zurückgesetzt.
    """
    override = "/etc/cloud/cloud.cfg.d/99-autodarts-hostname.cfg"
    content = "preserve_hostname: true\nmanage_etc_hosts: false\n"
    try:
        import os as _os
        _os.makedirs("/etc/cloud/cloud.cfg.d", exist_ok=True)
        with open(override, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except Exception:
        return False


def set_hostname(new_name: str) -> tuple[bool, str]:
    """
    Sets the system hostname permanently.
    Returns (success, error_message).
    """
    err = validate_hostname(new_name)
    if err:
        return False, err

    new_name = new_name.lower()

    _cloud_init_preserve_hostname()

    old_name = get_hostname()

    try:
        r = subprocess.run(
            ["hostnamectl", "set-hostname", new_name],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or "hostnamectl fehlgeschlagen").strip()
    except Exception as e:
        return False, str(e)

    # /etc/hosts aktualisieren
    try:
        with open("/etc/hosts", "r", encoding="utf-8", errors="ignore") as f:
            hosts = f.read()
        updated = re.sub(
            r'(\b)' + re.escape(old_name) + r'(\b)',
            new_name,
            hosts,
        )
        with open("/etc/hosts", "w", encoding="utf-8") as f:
            f.write(updated)
    except Exception:
        pass

    # avahi-daemon neu starten → sofort im Netzwerk sichtbar
    try:
        subprocess.run(["systemctl", "restart", "avahi-daemon"], capture_output=True, timeout=5)
    except Exception:
        pass

    return True, ""
