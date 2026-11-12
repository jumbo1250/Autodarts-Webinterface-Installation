#!/usr/bin/env bash
# Autodarts: AP-Standardkanal dauerhaft auf 1 setzen.
# Aendert nur das gespeicherte NetworkManager-Profil.
# KEIN AP-Neustart, KEIN Reboot, KEIN Eingriff in den Dispatcher.
# Beliebig oft ausfuehrbar (idempotent).
set -euo pipefail

AP_CONN="${AP_CONN:-Autodarts-AP}"
TARGET_CHANNEL=1

log() { echo "[modificat_AP_Channel] $*"; }

if [[ "${EUID}" -ne 0 ]]; then
  log "FEHLER: Bitte als root ausfuehren."
  exit 1
fi
if ! command -v nmcli >/dev/null 2>&1; then
  log "FEHLER: nmcli/NetworkManager fehlt."
  exit 1
fi

# Ohne AP-Profil nichts aendern (z.B. abweichende Installation).
if ! nmcli -g connection.id connection show "${AP_CONN}" >/dev/null 2>&1; then
  log "SKIP: AP-Profil '${AP_CONN}' nicht vorhanden."
  exit 0
fi

# Sicherheit: Nur ein wirkliches AP-Profil bearbeiten.
mode="$(nmcli -g 802-11-wireless.mode connection show "${AP_CONN}" 2>/dev/null | tr -d '\r\n')"
if [[ "${mode}" != "ap" ]]; then
  log "SKIP: '${AP_CONN}' ist kein WLAN-Access-Point (mode=${mode:-unbekannt})."
  exit 0
fi

current="$(nmcli -g 802-11-wireless.channel connection show "${AP_CONN}" 2>/dev/null | tr -d '\r\n')"
if [[ "${current}" == "${TARGET_CHANNEL}" ]]; then
  log "OK: AP-Profil bereits auf Kanal ${TARGET_CHANNEL}. Keine Aenderung."
  exit 0
fi

# Ausschliesslich das persistente Profil aendern, NICHT die aktive Verbindung.
nmcli connection modify "${AP_CONN}" \
  802-11-wireless.band bg \
  802-11-wireless.channel "${TARGET_CHANNEL}"

saved="$(nmcli -g 802-11-wireless.channel connection show "${AP_CONN}" 2>/dev/null | tr -d '\r\n')"
if [[ "${saved}" != "${TARGET_CHANNEL}" ]]; then
  log "FEHLER: Gespeicherter Kanal ist ${saved:-unbekannt} statt ${TARGET_CHANNEL}."
  exit 1
fi

log "OK: AP-Profil von Kanal ${current:-unbekannt} auf Kanal ${TARGET_CHANNEL} umgestellt."
log "Die laufende AP-Verbindung bleibt unveraendert. Wirksam bei naechster AP-Aktivierung/Neustart."
log "Der bestehende Dispatcher kann den Kanal danach wie bisher auf 1 oder 11 optimieren."
