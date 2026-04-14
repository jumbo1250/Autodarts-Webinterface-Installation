#!/usr/bin/env bash
set -euo pipefail

AP_IF="${AP_IF:-wlan_ap}"
AP_CONN="${AP_CONN:-Autodarts-AP}"
UPLINK_IFS="${UPLINK_IFS:-wlan0 eth0}"

STATE_DIR="/var/lib/autodarts"
STATUS_FILE="${STATE_DIR}/ap-internet-status.json"

LIB_DIR="/usr/local/lib/autodarts"
RULES_SCRIPT="${LIB_DIR}/apply-ap-internet-rules.sh"

SERVICE_FILE="/etc/systemd/system/autodarts-ap-internet.service"
DISPATCHER_DIR="/etc/NetworkManager/dispatcher.d"
DISPATCHER_SCRIPT="${DISPATCHER_DIR}/90-autodarts-ap-internet"

SYSCTL_FILE="/etc/sysctl.d/99-autodarts-ap-forward.conf"

NFT_TABLE="autodarts_ap"
NFT_TABLE_FAMILY="inet"
NFT_FORWARD_CHAIN="autodarts_forward"
NFT_POSTROUTING_CHAIN="autodarts_postrouting"

log() {
  echo "[autodarts-ap-fix-nft] $*"
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
  local note="$6"

  mkdir -p "${STATE_DIR}"
  cat > "${STATUS_FILE}" <<JSON
{
  "status": "${status}",
  "pi_online": ${pi_online},
  "forwarding_ready": ${forwarding_ready},
  "ap_active": ${ap_active},
  "uplinks": ${uplinks_json},
  "note": ${note}
}
JSON
}

ensure_nft_present() {
  if ! have_cmd nft; then
    local note
    note="$(json_escape 'nft fehlt')"
    write_status "red" "false" "false" "false" "[]" "${note}"
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
  for ifc in ${UPLINK_IFS}; do
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
    nft add chain "${NFT_TABLE_FAMILY}" "${NFT_TABLE}" "${NFT_FORWARD_CHAIN}"       "{ type filter hook forward priority 0; policy accept; }"
  fi

  if ! nft list chain "${NFT_TABLE_FAMILY}" "${NFT_TABLE}" "${NFT_POSTROUTING_CHAIN}" >/dev/null 2>&1; then
    nft add chain "${NFT_TABLE_FAMILY}" "${NFT_TABLE}" "${NFT_POSTROUTING_CHAIN}"       "{ type nat hook postrouting priority 100; policy accept; }"
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

apply_rules() {
  local applied=1
  local ifc

  ensure_base_table

  for ifc in ${UPLINK_IFS}; do
    if ! ip link show "${ifc}" >/dev/null 2>&1; then
      continue
    fi

    add_rule_if_missing "${NFT_FORWARD_CHAIN}"       "iifname \"${AP_IF}\" oifname \"${ifc}\" accept"       iifname "${AP_IF}" oifname "${ifc}" accept

    add_rule_if_missing "${NFT_FORWARD_CHAIN}"       "iifname \"${ifc}\" oifname \"${AP_IF}\" ct state established,related accept"       iifname "${ifc}" oifname "${AP_IF}" ct state established,related accept

    add_rule_if_missing "${NFT_POSTROUTING_CHAIN}"       "oifname \"${ifc}\" masquerade"       oifname "${ifc}" masquerade

    applied=0
  done

  return "${applied}"
}

rules_ready() {
  local ifc
  for ifc in ${UPLINK_IFS}; do
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

install_rules_script() {
  mkdir -p "${LIB_DIR}"
  cat > "${RULES_SCRIPT}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

AP_IF="${AP_IF:-wlan_ap}"
AP_CONN="${AP_CONN:-Autodarts-AP}"
UPLINK_IFS="${UPLINK_IFS:-wlan0 eth0}"
STATUS_FILE="/var/lib/autodarts/ap-internet-status.json"

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
  local note="$6"
  mkdir -p /var/lib/autodarts
  cat > "${STATUS_FILE}" <<JSON
{
  "status": "${status}",
  "pi_online": ${pi_online},
  "forwarding_ready": ${forwarding_ready},
  "ap_active": ${ap_active},
  "uplinks": ${uplinks_json},
  "note": ${note}
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
    nft add chain "${NFT_TABLE_FAMILY}" "${NFT_TABLE}" "${NFT_FORWARD_CHAIN}"       "{ type filter hook forward priority 0; policy accept; }"
  fi

  if ! nft list chain "${NFT_TABLE_FAMILY}" "${NFT_TABLE}" "${NFT_POSTROUTING_CHAIN}" >/dev/null 2>&1; then
    nft add chain "${NFT_TABLE_FAMILY}" "${NFT_TABLE}" "${NFT_POSTROUTING_CHAIN}"       "{ type nat hook postrouting priority 100; policy accept; }"
  fi
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
ensure_base_table

arr=()
applied=1
for ifc in ${UPLINK_IFS}; do
  if ip link show "${ifc}" >/dev/null 2>&1; then
    arr+=("\"${ifc}\"")
    add_rule_if_missing "${NFT_FORWARD_CHAIN}"       "iifname \"${AP_IF}\" oifname \"${ifc}\" accept"       iifname "${AP_IF}" oifname "${ifc}" accept
    add_rule_if_missing "${NFT_FORWARD_CHAIN}"       "iifname \"${ifc}\" oifname \"${AP_IF}\" ct state established,related accept"       iifname "${ifc}" oifname "${AP_IF}" ct state established,related accept
    add_rule_if_missing "${NFT_POSTROUTING_CHAIN}"       "oifname \"${ifc}\" masquerade"       oifname "${ifc}" masquerade
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
  chmod 755 "${RULES_SCRIPT}"
}

install_service() {
  cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=Autodarts AP Internet Sharing
After=network-online.target NetworkManager.service
Wants=network-online.target

[Service]
Type=oneshot
Environment=AP_IF=${AP_IF}
Environment=AP_CONN=${AP_CONN}
Environment=UPLINK_IFS=${UPLINK_IFS}
ExecStart=${RULES_SCRIPT}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
}

install_dispatcher() {
  mkdir -p "${DISPATCHER_DIR}"
  cat > "${DISPATCHER_SCRIPT}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export AP_IF="${AP_IF}"
export AP_CONN="${AP_CONN}"
export UPLINK_IFS="${UPLINK_IFS}"
${RULES_SCRIPT} >/dev/null 2>&1 || true
EOF
  chmod 755 "${DISPATCHER_SCRIPT}"
}

main() {
  ensure_root
  ensure_nft_present
  ensure_ip_forward
  install_rules_script
  install_service
  install_dispatcher

  systemctl daemon-reload
  systemctl enable autodarts-ap-internet.service >/dev/null 2>&1 || true
  systemctl restart autodarts-ap-internet.service >/dev/null 2>&1 || true

  "${RULES_SCRIPT}" >/dev/null 2>&1 || true

  local pi_online="false"
  local ap_active="false"
  local forwarding_ready="false"
  local status="red"
  local note="Unbekannt"
  local uplinks_json

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

  write_status "${status}" "${pi_online}" "${forwarding_ready}" "${ap_active}" "${uplinks_json}" "$(json_escape "${note}")"

  log "Fertig."
  log "Statusdatei: ${STATUS_FILE}"
  cat "${STATUS_FILE}"
}

main "$@"
