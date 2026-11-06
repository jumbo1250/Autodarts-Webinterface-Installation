#!/usr/bin/env bash
# webpanel_AP_apply.sh – AP NAT/Forwarding-Setup + optionale Direktverbindung ohne WLAN-Stick
# Wird von webpanel_AP.service (Type=oneshot) aufgerufen.
set -euo pipefail

AP_IF="${AP_IF:-wlan_ap}"
AP_CONN="${AP_CONN:-Autodarts-AP}"
UPLINK_IF="${UPLINK_IF:-wlan0}"
EXTRA_UPLINK_IFS="${EXTRA_UPLINK_IFS:-eth0}"
STATUS_FILE="/var/lib/autodarts/ap-internet-status.json"
MANUAL_WIFI_FLAG="${MANUAL_WIFI_FLAG:-/run/autodarts_manual_wifi_connect.flag}"
MANUAL_WIFI_DELAY="${MANUAL_WIFI_DELAY:-10}"
WIFI_CONNECT_JSON="/var/lib/autodarts/wifi_connect_no_stick.json"
NOSTICK_CONN="Autodarts-WiFi-NoStick"

NFT_TABLE="autodarts_ap"
NFT_TABLE_FAMILY="inet"
NFT_FORWARD_CHAIN="autodarts_forward"
NFT_POSTROUTING_CHAIN="autodarts_postrouting"

json_escape() {
  local s="${1:-}"
  python3 - "$s" <<'PY'
import json, sys
print(json.dumps(sys.argv[1]))
PY
}

write_status() {
  local status="$1"
  local pi_online="$2"
  local forwarding_ready="$3"
  local ap_active="$4"
  local uplinks_json="$5"
  local note_json="$6"
  mkdir -p /var/lib/autodarts
  cat > "${STATUS_FILE}" <<JSON
{
  "status": "${status}",
  "pi_online": ${pi_online},
  "forwarding_ready": ${forwarding_ready},
  "ap_active": ${ap_active},
  "uplinks": ${uplinks_json},
  "note": ${note_json}
}
JSON
}

rule_exists() {
  local pattern="$1"
  nft -a list table "${NFT_TABLE_FAMILY}" "${NFT_TABLE}" 2>/dev/null | grep -Fq -- "${pattern}"
}

add_rule_if_missing() {
  local chain="$1"
  local pattern="$2"
  shift 2
  if ! rule_exists "${pattern}"; then
    nft add rule "${NFT_TABLE_FAMILY}" "${NFT_TABLE}" "${chain}" "$@"
  fi
}

ensure_base_table() {
  if ! nft list table "${NFT_TABLE_FAMILY}" "${NFT_TABLE}" >/dev/null 2>&1; then
    nft add table "${NFT_TABLE_FAMILY}" "${NFT_TABLE}"
  fi
  if ! nft list chain "${NFT_TABLE_FAMILY}" "${NFT_TABLE}" "${NFT_FORWARD_CHAIN}" >/dev/null 2>&1; then
    nft add chain "${NFT_TABLE_FAMILY}" "${NFT_TABLE}" "${NFT_FORWARD_CHAIN}" "{ type filter hook forward priority 0; policy accept; }"
  fi
  if ! nft list chain "${NFT_TABLE_FAMILY}" "${NFT_TABLE}" "${NFT_POSTROUTING_CHAIN}" >/dev/null 2>&1; then
    nft add chain "${NFT_TABLE_FAMILY}" "${NFT_TABLE}" "${NFT_POSTROUTING_CHAIN}" "{ type nat hook postrouting priority 100; policy accept; }"
  fi
}

get_uplink_channel() {
  local info
  info="$(iw dev "${UPLINK_IF}" info 2>/dev/null || true)"
  awk '/channel /{print $2; exit}' <<<"${info}"
}

get_uplink_freq() {
  local info
  info="$(iw dev "${UPLINK_IF}" info 2>/dev/null || true)"
  awk -F'[()]' '/channel /{print $2; exit}' <<<"${info}" | awk '{print $1}'
}

get_ap_channel() {
  local info
  info="$(iw dev "${AP_IF}" info 2>/dev/null || true)"
  awk '/channel /{print $2; exit}' <<<"${info}"
}

pick_best_ap_channel() {
  local uplink_ch="$1"
  local uplink_freq="$2"

  if [ -z "${uplink_freq}" ]; then
    echo ""
    return 0
  fi

  case "${uplink_freq}" in
    ''|*[!0-9]*)
      echo ""
      return 0
      ;;
  esac

  if [ "${uplink_freq}" -ge 5000 ]; then
    echo ""
    return 0
  fi
  if [ "${uplink_freq}" -lt 2400 ] || [ "${uplink_freq}" -gt 2500 ]; then
    echo ""
    return 0
  fi

  case "${uplink_ch}" in
    1|2|3|4|5) echo "11" ;;
    6) echo "1" ;;
    7|8|9|10|11|12|13) echo "1" ;;
    *) echo "" ;;
  esac
}

manual_wifi_flag_recent() {
  [ -f "${MANUAL_WIFI_FLAG}" ] || return 1

  local now mtime age
  now="$(date +%s)"
  mtime="$(stat -c %Y "${MANUAL_WIFI_FLAG}" 2>/dev/null || echo 0)"
  age=$((now - mtime))

  [ "${age}" -ge 0 ] && [ "${age}" -le 60 ]
}

delay_for_manual_wifi_if_needed() {
  if manual_wifi_flag_recent; then
    sleep "${MANUAL_WIFI_DELAY}"
    rm -f "${MANUAL_WIFI_FLAG}" >/dev/null 2>&1 || true
  fi
}

set_ap_channel_if_needed() {
  local uplink_ch uplink_freq current_ap_ch target_ap_ch

  if manual_wifi_flag_recent; then
    return 0
  fi

  uplink_ch="$(get_uplink_channel)"
  uplink_freq="$(get_uplink_freq)"
  current_ap_ch="$(get_ap_channel)"
  target_ap_ch="$(pick_best_ap_channel "${uplink_ch}" "${uplink_freq}")"

  if [ -z "${target_ap_ch}" ]; then
    return 0
  fi
  if [ "${current_ap_ch}" = "${target_ap_ch}" ]; then
    return 0
  fi

  nmcli connection modify "${AP_CONN}" 802-11-wireless.band bg
  nmcli connection modify "${AP_CONN}" 802-11-wireless.channel "${target_ap_ch}"
  nmcli connection down "${AP_CONN}" >/dev/null 2>&1 || true
  sleep 1
  nmcli connection up "${AP_CONN}" >/dev/null 2>&1 || true
}

# --- Direktverbindung ohne WLAN-Stick ---
# Wenn /var/lib/autodarts/wifi_connect_no_stick.json existiert, versucht der Pi
# sich direkt mit dem gespeicherten Heimnetz zu verbinden (AP-Interface als Client).
# Erfolg: AP bleibt deaktiviert, Script endet mit exit 0.
# Fehler: JSON löschen, AP-Autoconnect wiederherstellen, AP hochfahren, normal weiter.
_try_direct_wifi_connect() {
    [ -f "${WIFI_CONNECT_JSON}" ] || return 0

    local ssid password
    ssid=$(python3 - <<PY
import json
try:
    with open("${WIFI_CONNECT_JSON}") as f:
        d = json.load(f)
    print(d.get("ssid", ""))
except Exception:
    print("")
PY
)
    password=$(python3 - <<PY
import json
try:
    with open("${WIFI_CONNECT_JSON}") as f:
        d = json.load(f)
    print(d.get("password", ""))
except Exception:
    print("")
PY
)

    if [ -z "${ssid}" ]; then
        echo "[webpanel_AP] wifi_connect_no_stick.json leer/ungültig – wird gelöscht"
        rm -f "${WIFI_CONNECT_JSON}"
        return 0
    fi

    echo "[webpanel_AP] Direktverbindung: Versuche '${ssid}' über ${AP_IF} ..."

    # AP-Autoconnect deaktivieren – sonst verdrängt NM die NOSTICK-Verbindung nach dem Script-Exit
    nmcli connection modify "${AP_CONN}" connection.autoconnect no >/dev/null 2>&1 || true
    nmcli connection down "${AP_CONN}" >/dev/null 2>&1 || true

    nmcli connection delete "${NOSTICK_CONN}" >/dev/null 2>&1 || true

    nmcli connection add type wifi ifname "${AP_IF}" con-name "${NOSTICK_CONN}" \
        ssid "${ssid}" connection.autoconnect no >/dev/null 2>&1 || true

    if [ -n "${password}" ]; then
        nmcli connection modify "${NOSTICK_CONN}" \
            wifi-sec.key-mgmt wpa-psk wifi-sec.psk "${password}" >/dev/null 2>&1 || true
    fi
    nmcli connection modify "${NOSTICK_CONN}" \
        ipv4.method auto ipv6.method ignore >/dev/null 2>&1 || true

    if nmcli -w 20 connection up "${NOSTICK_CONN}" >/dev/null 2>&1; then
        echo "[webpanel_AP] Direktverbindung erfolgreich: ${ssid}"
        rm -f "${WIFI_CONNECT_JSON}"
        write_status "green" "true" "false" "false" "[]" \
            "$(json_escape "Direkt mit Heimnetz verbunden: ${ssid}")"
        exit 0
    fi

    echo "[webpanel_AP] Direktverbindung fehlgeschlagen – AP wird wiederhergestellt"
    nmcli connection delete "${NOSTICK_CONN}" >/dev/null 2>&1 || true
    rm -f "${WIFI_CONNECT_JSON}"
    nmcli connection modify "${AP_CONN}" connection.autoconnect yes >/dev/null 2>&1 || true
    nmcli connection up "${AP_CONN}" >/dev/null 2>&1 || true
    sleep 3
}

# Aufräumen: eventuelle NOSTICK-Verbindung vom unterbrochenen Vorversuch entfernen
nmcli connection delete "${NOSTICK_CONN}" >/dev/null 2>&1 || true
# Kein JSON-Auftrag → AP-Autoconnect wiederherstellen (Schutz gegen unterbrochene Tests)
if [ ! -f "${WIFI_CONNECT_JSON}" ]; then
    nmcli connection modify "${AP_CONN}" connection.autoconnect yes >/dev/null 2>&1 || true
fi

_try_direct_wifi_connect || true
# --- Ende Direktverbindung ---

pi_online=false
if timeout 4 ping -c 1 -W 2 1.1.1.1 >/dev/null 2>&1 || timeout 4 ping -c 1 -W 2 8.8.8.8 >/dev/null 2>&1; then
  pi_online=true
fi

ap_active=false
if nmcli -t -f NAME connection show --active 2>/dev/null | grep -Fxq "${AP_CONN}"; then
  ap_active=true
fi

if ! command -v nft >/dev/null 2>&1; then
  write_status "red" "${pi_online}" "false" "${ap_active}" "[]" "$(json_escape 'nft fehlt')"
  exit 0
fi

sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || true

set_ap_channel_if_needed
ensure_base_table

arr=()
applied=1
for ifc in ${UPLINK_IF} ${EXTRA_UPLINK_IFS}; do
  if ip link show "${ifc}" >/dev/null 2>&1; then
    arr+=("\"${ifc}\"")
    add_rule_if_missing "${NFT_FORWARD_CHAIN}" "iifname \"${AP_IF}\" oifname \"${ifc}\" accept" iifname "${AP_IF}" oifname "${ifc}" accept
    add_rule_if_missing "${NFT_FORWARD_CHAIN}" "iifname \"${ifc}\" oifname \"${AP_IF}\" ct state established,related accept" iifname "${ifc}" oifname "${AP_IF}" ct state established,related accept
    add_rule_if_missing "${NFT_POSTROUTING_CHAIN}" "oifname \"${ifc}\" masquerade" oifname "${ifc}" masquerade
    applied=0
  fi
done

uplinks_json="[]"
if [ "${#arr[@]}" -gt 0 ]; then
  uplinks_json="[$(IFS=,; echo "${arr[*]}")]"
fi

forwarding_ready=false
if [ "${applied}" -eq 0 ]; then
  forwarding_ready=true
fi

status="red"
note="AP-Weitergabe noch nicht bereit"
if [ "${pi_online}" = true ] && [ "${ap_active}" = true ] && [ "${forwarding_ready}" = true ]; then
  status="green"
  note="Verbundenen AP-Geräten steht Internet zur Verfügung."
elif [ "${pi_online}" = true ] && [ "${ap_active}" = true ] && [ "${forwarding_ready}" = false ]; then
  note="Pi online, aber AP-Weitergabe nicht bereit."
elif [ "${pi_online}" = false ]; then
  note="Pi hat selbst kein Internet."
elif [ "${ap_active}" = false ]; then
  note="Access Point ist nicht aktiv."
fi

write_status "${status}" "${pi_online}" "${forwarding_ready}" "${ap_active}" "${uplinks_json}" "$(json_escape "${note}")"
