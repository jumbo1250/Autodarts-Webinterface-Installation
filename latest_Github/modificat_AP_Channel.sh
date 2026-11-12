#!/usr/bin/env bash
# Autodarts: AP-Standardkanal + Emoji-/Symbolschriftarten.
# AP: Aendert nur das gespeicherte NetworkManager-Profil (KEIN Neustart).
# Fonts: Installiert fehlende Pakete nur bei Bedarf (auch auf Headless-Pis sicher).
# Beliebig oft ausfuehrbar. Der bestehende Dispatcher bleibt unveraendert.
set -euo pipefail

AP_CONN="${AP_CONN:-Autodarts-AP}"
TARGET_CHANNEL=1
FONT_PACKAGES=(fonts-noto-color-emoji fonts-noto-core fonts-symbola)

log() { echo "[modificat_AP_Channel] $*"; }

update_ap_channel() {
  if ! command -v nmcli >/dev/null 2>&1; then
    log "WARN: nmcli/NetworkManager fehlt. AP-Anpassung uebersprungen."
    return 0
  fi

  if ! nmcli -g connection.id connection show "${AP_CONN}" >/dev/null 2>&1; then
    log "SKIP: AP-Profil '${AP_CONN}' nicht vorhanden."
    return 0
  fi

  local mode current saved
  mode="$(nmcli -g 802-11-wireless.mode connection show "${AP_CONN}" 2>/dev/null | tr -d '\r\n')"
  if [[ "${mode}" != "ap" ]]; then
    log "SKIP: '${AP_CONN}' ist kein WLAN-Access-Point (mode=${mode:-unbekannt})."
    return 0
  fi

  current="$(nmcli -g 802-11-wireless.channel connection show "${AP_CONN}" 2>/dev/null | tr -d '\r\n')"
  if [[ "${current}" == "${TARGET_CHANNEL}" ]]; then
    log "OK: AP-Profil bereits auf Kanal ${TARGET_CHANNEL}. Keine Aenderung."
    return 0
  fi

  # Nur Profil speichern; laufender AP und WLED-Verbindung bleiben unberuehrt.
  nmcli connection modify "${AP_CONN}" \
    802-11-wireless.band bg \
    802-11-wireless.channel "${TARGET_CHANNEL}"

  saved="$(nmcli -g 802-11-wireless.channel connection show "${AP_CONN}" 2>/dev/null | tr -d '\r\n')"
  if [[ "${saved}" != "${TARGET_CHANNEL}" ]]; then
    log "FEHLER: Gespeicherter Kanal ist ${saved:-unbekannt} statt ${TARGET_CHANNEL}."
    return 1
  fi

  log "OK: Gespeicherter AP-Kanal ${current:-0} -> ${TARGET_CHANNEL}. Wirksam bei naechster AP-Aktivierung."
  log "Der bestehende Dispatcher darf spaeter weiterhin Kanal 1 oder 11 waehlen."
}

install_missing_fonts() {
  if ! command -v dpkg-query >/dev/null 2>&1 || ! command -v apt-get >/dev/null 2>&1; then
    log "WARN: APT/dpkg nicht vorhanden. Font-Installation uebersprungen."
    return 0
  fi

  local missing=() pkg
  for pkg in "${FONT_PACKAGES[@]}"; do
    if ! dpkg-query -W -f='${Status}' "${pkg}" 2>/dev/null | grep -Fxq 'install ok installed'; then
      missing+=("${pkg}")
    fi
  done

  if [[ "${#missing[@]}" -eq 0 ]]; then
    log "OK: Alle Emoji-/Symbolschriftarten bereits installiert."
    return 0
  fi

  log "Fehlende Schriftarten: ${missing[*]}"
  log "APT-Paketlisten aktualisieren (max. 120 s) ..."
  if ! timeout 120s apt-get -qq \
      -o Acquire::Retries=1 \
      -o Acquire::http::Timeout=15 \
      -o Acquire::https::Timeout=15 \
      -o DPkg::Lock::Timeout=20 update; then
    log "WARN: apt-get update fehlgeschlagen. Font-Installation wird spaeter erneut versucht."
    return 0
  fi

  log "Fehlende Schriftarten installieren (max. 240 s) ..."
  if ! timeout 240s env DEBIAN_FRONTEND=noninteractive apt-get -y -qq \
      -o Acquire::Retries=1 \
      -o Acquire::http::Timeout=20 \
      -o Acquire::https::Timeout=20 \
      -o DPkg::Lock::Timeout=20 \
      --no-install-recommends install "${missing[@]}"; then
    log "WARN: Font-Installation fehlgeschlagen. AP-Korrektur bleibt erhalten; erneuter Versuch beim naechsten Update."
    return 0
  fi

  if command -v fc-cache >/dev/null 2>&1; then
    fc-cache -f || log "WARN: Font-Cache konnte nicht vollstaendig aktualisiert werden."
  fi
  log "OK: Schriftarten installiert. Chromium ggf. neu starten."
}

if [[ "${EUID}" -ne 0 ]]; then
  log "FEHLER: Bitte als root ausfuehren."
  exit 1
fi

# Font-Installation ist unabhaengig vom AP-Status (wichtig, wenn Kanal 1 bereits gesetzt ist).
update_ap_channel
install_missing_fonts
log "Fertig."
