#!/usr/bin/env bash
# BUILD: AUTODARTS-POWEROFF-SHORTCUT-20261004-01
#
# Erstellt einen Poweroff-Desktop-Shortcut für Autodarts-Pis.
# Headless-sicher: Kein Desktop-Ordner -> sauberer Exit, kein Fehler.
# Idempotent: Kann bei jedem Update erneut aufgerufen werden.

set -euo pipefail

USER_NAME="${AUTODARTS_DESKTOP_USER:-peter}"
USER_HOME="$(getent passwd "$USER_NAME" 2>/dev/null | awk -F: '{print $6}' || true)"
HOME_DIR="${USER_HOME:-/home/${USER_NAME}}"
DESKTOP_DIR="${HOME_DIR}/Desktop"
SHORTCUT="${DESKTOP_DIR}/Poweroff.desktop"

# Headless-Prüfung: Kein Desktop-Ordner -> kein Fehler, einfach skip
if [[ ! -d "$DESKTOP_DIR" ]]; then
    echo "INFO: Kein Desktop-Ordner gefunden (${DESKTOP_DIR}) -> headless oder kein Desktop -> skip"
    exit 0
fi

cat > "$SHORTCUT" <<'DESKTOP'
[Desktop Entry]
Version=1.0
Type=Application
Name=System Herunterfahren
Name[en]=Shutdown System
Comment=Raspberry Pi jetzt ausschalten
Comment[en]=Shut down the Raspberry Pi now
Exec=bash -c 'sudo systemctl poweroff'
Icon=system-shutdown
Terminal=false
StartupNotify=false
Categories=System;
DESKTOP

chmod +x "$SHORTCUT"
chown "${USER_NAME}:${USER_NAME}" "$SHORTCUT" 2>/dev/null || true

echo "OK: Poweroff-Shortcut erstellt: ${SHORTCUT}"
