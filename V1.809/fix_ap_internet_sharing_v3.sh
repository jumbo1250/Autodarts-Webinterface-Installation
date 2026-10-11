#!/usr/bin/env bash
set -euo pipefail

AP_IF="${AP_IF:-wlan_ap}"
AP_CONN="${AP_CONN:-Autodarts-AP}"
UPLINK_IF="${UPLINK_IF:-wlan0}"
EXTRA_UPLINK_IFS="${EXTRA_UPLINK_IFS:-eth0}"

STATE_DIR="/var/lib/autodarts"
STATUS_FILE="${STATE_DIR}/ap-internet-status.json"
MANUAL_WIFI_FLAG="${MANUAL_WIFI_FLAG:-/run/autodarts_manual_wifi_connect.flag}"
MANUAL_WIFI_DELAY="${MANUAL_WIFI_DELAY:-10}"

LIB_DIR="/usr/local/lib/autodarts"
APPLY_SCRIPT="${LIB_DIR}/webpanel_AP_apply.sh"

SERVICE_FILE="/etc/systemd/system/webpanel_AP.service"
DISPATCHER_DIR="/etc/NetworkManager/dispatcher.d"
DISPATCHER_SCRIPT="${DISPATCHER_DIR}/91-webpanel_AP"

SYSCTL_FILE="/etc/sysctl.d/99-webpanel_AP-forward.conf"

NFT_TABLE="autodarts_ap"
NFT_TABLE_FAMILY="inet"
NFT_FORWARD_CHAIN="autodarts_forward"
NFT_POSTROUTING_CHAIN="autodarts_postrouting"

log() {
  echo "[webpanel_AP] $*"
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

ensure_root() {
  if [ "${EUID}" -ne 0 ]; then
    echo "Bitte mit sudo/root ausführen."
    exit 1
  fi
}

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

  mkdir -p "${STATE_DIR}"
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

ensure_nft_present() {
  if ! have_cmd nft; then
    local note_json
    note_json="$(json_escape 'nft fehlt')"
    write_status "red" "false" "false" "false" "[]" "${note_json}"
    echo "nft fehlt"
    exit 1
  fi
}

ensure_ip_forward() {
  mkdir -p /etc/sysctl.d
  cat > "${SYSCTL_FILE}" <<EOF
net.ipv4.ip_forward=1
EOF
  sysctl -w net.ipv4.ip_forward=1 >/dev/null
}

ap_is_active() {
  nmcli -t -f NAME connection show --active 2>/dev/null | grep -Fxq "${AP_CONN}"
}

pi_is_online() {
  timeout 4 ping -c 1 -W 2 1.1.1.1 >/dev/null 2>&1 ||   timeout 4 ping -c 1 -W 2 8.8.8.8 >/dev/null 2>&1
}

list_present_uplinks_json() {
  local arr=()
  local ifc
  for ifc in ${UPLINK_IF} ${EXTRA_UPLINK_IFS}; do
    if ip link show "${ifc}" >/dev/null 2>&1; then
      arr+=("\"${ifc}\"")
    fi
  done
  if [ "${#arr[@]}" -eq 0 ]; then
    echo "[]"
  else
    local joined
    joined=$(IFS=,; echo "${arr[*]}")
    echo "[${joined}]"
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

apply_nft_rules() {
  local applied=1
  local ifc

  ensure_base_table

  for ifc in ${UPLINK_IF} ${EXTRA_UPLINK_IFS}; do
    if ! ip link show "${ifc}" >/dev/null 2>&1; then
      continue
    fi

    add_rule_if_missing "${NFT_FORWARD_CHAIN}" "iifname \"${AP_IF}\" oifname \"${ifc}\" accept" iifname "${AP_IF}" oifname "${ifc}" accept
    add_rule_if_missing "${NFT_FORWARD_CHAIN}" "iifname \"${ifc}\" oifname \"${AP_IF}\" ct state established,related accept" iifname "${ifc}" oifname "${AP_IF}" ct state established,related accept
    add_rule_if_missing "${NFT_POSTROUTING_CHAIN}" "oifname \"${ifc}\" masquerade" oifname "${ifc}" masquerade

    applied=0
  done

  return "${applied}"
}

rules_ready() {
  local ifc
  for ifc in ${UPLINK_IF} ${EXTRA_UPLINK_IFS}; do
    if ! ip link show "${ifc}" >/dev/null 2>&1; then
      continue
    fi
    rule_exists "iifname \"${AP_IF}\" oifname \"${ifc}\" accept" || continue
    rule_exists "iifname \"${ifc}\" oifname \"${AP_IF}\" ct state established,related accept" || continue
    rule_exists "oifname \"${ifc}\" masquerade" || continue
    return 0
  done
  return 1
}

get_uplink_channel() {
  local info
  info="$(iw dev "${UPLINK_IF}" info 2>/dev/null || true)"
  if [ -z "${info}" ]; then
    echo ""
    return 0
  fi
  awk '/channel /{print $2; exit}' <<<"${info}"
}

get_uplink_freq() {
  local info
  info="$(iw dev "${UPLINK_IF}" info 2>/dev/null || true)"
  if [ -z "${info}" ]; then
    echo ""
    return 0
  fi
  awk -F'[()]' '/channel /{print $2; exit}' <<<"${info}" | awk '{print $1}'
}

get_ap_channel() {
  local info
  info="$(iw dev "${AP_IF}" info 2>/dev/null || true)"
  if [ -z "${info}" ]; then
    echo ""
    return 0
  fi
  awk '/channel /{print $2; exit}' <<<"${info}"
}

pick_best_ap_channel() {
  local uplink_ch="$1"
  local uplink_freq="$2"

  # Wenn wlan0 nicht verbunden ist, keine Frequenz -> Setup-Funk nicht anfassen.
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

  # Nur bei echtem 2.4 GHz optimieren. 5 GHz oder unbekannt: nichts ändern.
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

  # Bei einer manuellen Verbindung über das Webpanel steuert das Webpanel
  # Countdown, Abbrechen, Kanalwechsel und Reboot. Der Dispatcher darf den
  # AP während des Countdowns nicht eigenständig down/up schalten.
  if manual_wifi_flag_recent; then
    log "Manuelle Webpanel-WLAN-Verbindung erkannt: Kanalwechsel wird kontrolliert vom Webpanel ausgeführt."
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

  log "AP-Kanal anpassen: wlan0=${uplink_ch:-unknown} (${uplink_freq:-unknown} MHz) -> AP=${target_ap_ch}"
  nmcli connection modify "${AP_CONN}" 802-11-wireless.band bg
  nmcli connection modify "${AP_CONN}" 802-11-wireless.channel "${target_ap_ch}"
  nmcli connection down "${AP_CONN}" >/dev/null 2>&1 || true
  sleep 1
  nmcli connection up "${AP_CONN}" >/dev/null 2>&1 || true
}

install_apply_script() {
  mkdir -p "${LIB_DIR}"
  cat > "${APPLY_SCRIPT}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

AP_IF="${AP_IF:-wlan_ap}"
AP_CONN="${AP_CONN:-Autodarts-AP}"
UPLINK_IF="${UPLINK_IF:-wlan0}"
EXTRA_UPLINK_IFS="${EXTRA_UPLINK_IFS:-eth0}"
STATUS_FILE="/var/lib/autodarts/ap-internet-status.json"
MANUAL_WIFI_FLAG="${MANUAL_WIFI_FLAG:-/run/autodarts_manual_wifi_connect.flag}"
MANUAL_WIFI_DELAY="${MANUAL_WIFI_DELAY:-10}"

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

  # Wenn wlan0 nicht verbunden ist, keine Frequenz -> Setup-Funk nicht anfassen.
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

  # Nur bei echtem 2.4 GHz optimieren. 5 GHz oder unbekannt: nichts ändern.
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

  # Webpanel-Verbindungen werden absichtlich nicht sofort umgeschaltet.
  # Das Webpanel zeigt zuerst den Countdown und entscheidet dann zwischen
  # Reboot oder Kanalwechsel ohne Reboot.
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
EOF
  chmod 755 "${APPLY_SCRIPT}"
}

install_service() {
  cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=webpanel_AP fix service
After=network-online.target NetworkManager.service
Wants=network-online.target

[Service]
Type=oneshot
Environment=AP_IF=${AP_IF}
Environment=AP_CONN=${AP_CONN}
Environment=UPLINK_IF=${UPLINK_IF}
Environment=EXTRA_UPLINK_IFS=${EXTRA_UPLINK_IFS}
ExecStart=${APPLY_SCRIPT}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
}

install_dispatcher() {
  mkdir -p "${DISPATCHER_DIR}"

  # Self-healing: Falls ein Test/alter Stand den Dispatcher deaktiviert hat,
  # soll ein normales Webpanel-Update ihn wieder sauber aktiv installieren.
  rm -f "${DISPATCHER_SCRIPT}.off" 2>/dev/null || true

  cat > "${DISPATCHER_SCRIPT}" <<EOF
#!/usr/bin/env bash
set -euo pipefail

IFACE="\${1:-}"
ACTION="\${2:-}"

case "\${ACTION}" in
  up|dhcp4-change|connectivity-change|reapply|hostname)
    ;;
  *)
    exit 0
    ;;
esac

if [ "\${IFACE}" != "${UPLINK_IF}" ] && [ "\${IFACE}" != "${AP_IF}" ]; then
  exit 0
fi

export AP_IF="${AP_IF}"
export AP_CONN="${AP_CONN}"
export UPLINK_IF="${UPLINK_IF}"
export EXTRA_UPLINK_IFS="${EXTRA_UPLINK_IFS}"

sleep 2
${APPLY_SCRIPT} >/dev/null 2>&1 || true
EOF
  chmod 755 "${DISPATCHER_SCRIPT}"

  if [ ! -x "${DISPATCHER_SCRIPT}" ]; then
    echo "[webpanel_AP] ERROR: Dispatcher wurde nicht aktiv angelegt: ${DISPATCHER_SCRIPT}"
    return 1
  fi
}

main() {
  ensure_root
  ensure_nft_present
  ensure_ip_forward
  install_apply_script
  install_service
  install_dispatcher

  systemctl daemon-reload
  systemctl enable webpanel_AP.service >/dev/null 2>&1 || true
  systemctl restart webpanel_AP.service >/dev/null 2>&1 || true

  "${APPLY_SCRIPT}" >/dev/null 2>&1 || true

  local pi_online="false"
  local ap_active="false"
  local forwarding_ready="false"
  local status="red"
  local note="Unbekannt"
  local uplinks_json
  local note_json

  uplinks_json="$(list_present_uplinks_json)"

  if pi_is_online; then
    pi_online="true"
  fi

  if ap_is_active; then
    ap_active="true"
  fi

  if rules_ready; then
    forwarding_ready="true"
  fi

  if [ "${pi_online}" = "true" ] && [ "${ap_active}" = "true" ] && [ "${forwarding_ready}" = "true" ]; then
    status="green"
    note="Verbundenen AP-Geräten steht Internet zur Verfügung."
  elif ! have_cmd nft; then
    note="nft fehlt"
  elif [ "${pi_online}" = "true" ] && [ "${ap_active}" = "true" ] && [ "${forwarding_ready}" = "false" ]; then
    note="Pi online, aber AP-Weitergabe nicht bereit."
  elif [ "${pi_online}" = "false" ]; then
    note="Pi hat selbst kein Internet."
  elif [ "${ap_active}" = "false" ]; then
    note="Access Point ist nicht aktiv."
  else
    note="AP-Internetstatus unbekannt."
  fi

  note_json="$(json_escape "${note}")"
  write_status "${status}" "${pi_online}" "${forwarding_ready}" "${ap_active}" "${uplinks_json}" "${note_json}"

  log "Fertig."
  log "Service: webpanel_AP.service"
  log "Dispatcher: ${DISPATCHER_SCRIPT}"
  log "Apply-Script: ${APPLY_SCRIPT}"
  cat "${STATUS_FILE}"
}

main "$@"
