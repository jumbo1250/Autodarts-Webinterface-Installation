#!/usr/bin/env bash
set -euo pipefail

AP_IF="${AP_IF:-wlan_ap}"
UPLINK_IFS="${UPLINK_IFS:-eth0 wlan0}"
STATE_DIR="/var/lib/autodarts"
STATE_FILE="$STATE_DIR/ap-internet-status.json"
INSTALL_DIR="/usr/local/lib/autodarts"
APPLY_SCRIPT="$INSTALL_DIR/apply-ap-internet-rules.sh"
DISPATCHER_SCRIPT="/etc/NetworkManager/dispatcher.d/90-autodarts-ap-internet"
SYSCTL_FILE="/etc/sysctl.d/99-autodarts-ap-forwarding.conf"
SERVICE_FILE="/etc/systemd/system/autodarts-ap-internet.service"

mkdir -p "$STATE_DIR" "$INSTALL_DIR"
log(){ printf '[autodarts-ap] %s\n' "$*"; }

cat > "$APPLY_SCRIPT" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
AP_IF="${AP_IF:-wlan_ap}"
UPLINK_IFS="${UPLINK_IFS:-eth0 wlan0}"
STATE_DIR="/var/lib/autodarts"
STATE_FILE="$STATE_DIR/ap-internet-status.json"
mkdir -p "$STATE_DIR"

have_cmd(){ command -v "$1" >/dev/null 2>&1; }
iface_exists(){ ip link show "$1" >/dev/null 2>&1; }
is_iface_up(){ ip -o link show "$1" 2>/dev/null | grep -q 'UP'; }

json_escape(){
  python3 - <<'PY' "$1"
import json,sys
print(json.dumps(sys.argv[1]))
PY
}

choose_uplinks(){
  local default_if ifc
  local -a all=() unique=() active=()
  default_if="$(ip route show default 2>/dev/null | awk '/default/ {print $5; exit}')"
  if [[ -n "$default_if" && "$default_if" != "$AP_IF" ]] && iface_exists "$default_if"; then
    all+=("$default_if")
  fi
  for ifc in $UPLINK_IFS; do
    if [[ "$ifc" != "$AP_IF" ]] && iface_exists "$ifc"; then
      all+=("$ifc")
    fi
  done
  if ((${#all[@]}==0)); then
    return 0
  fi
  mapfile -t unique < <(printf '%s\n' "${all[@]}" | awk 'NF && !seen[$0]++')
  for ifc in "${unique[@]}"; do
    is_iface_up "$ifc" && active+=("$ifc")
  done
  if ((${#active[@]} > 0)); then
    printf '%s\n' "${active[@]}"
  else
    printf '%s\n' "${unique[@]}"
  fi
}

write_state(){
  local status="$1" pi_online="$2" forwarding="$3" ap_active="$4" uplinks="$5" note="$6"
  cat > "$STATE_FILE" <<JSON
{
  "status": $(json_escape "$status"),
  "pi_online": $pi_online,
  "forwarding_ready": $forwarding,
  "ap_active": $ap_active,
  "uplinks": $(python3 - <<'PY' "$uplinks"
import json,sys
print(json.dumps([x for x in sys.argv[1].split() if x]))
PY
),
  "note": $(json_escape "$note")
}
JSON
}

AP_ACTIVE=false
iface_exists "$AP_IF" && is_iface_up "$AP_IF" && AP_ACTIVE=true
mapfile -t UPLINKS < <(choose_uplinks)
U_LIST="${UPLINKS[*]:-}"
FORWARDING=false
[[ "$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || echo 0)" = "1" ]] && FORWARDING=true

PI_ONLINE=false
for target in 1.1.1.1 8.8.8.8; do
  if ping -c 1 -W 2 "$target" >/dev/null 2>&1; then
    PI_ONLINE=true
    break
  fi
done

if ! iface_exists "$AP_IF"; then
  write_state red "$PI_ONLINE" "$FORWARDING" false "$U_LIST" "AP-Interface $AP_IF fehlt"
  exit 0
fi

if ! have_cmd iptables; then
  write_state red "$PI_ONLINE" false "$AP_ACTIVE" "$U_LIST" "iptables fehlt"
  exit 0
fi

for uplink in "${UPLINKS[@]:-}"; do
  [[ -z "$uplink" ]] && continue
  iptables -t nat -C POSTROUTING -o "$uplink" -j MASQUERADE 2>/dev/null || \
    iptables -t nat -A POSTROUTING -o "$uplink" -j MASQUERADE
  iptables -C FORWARD -i "$AP_IF" -o "$uplink" -j ACCEPT 2>/dev/null || \
    iptables -A FORWARD -i "$AP_IF" -o "$uplink" -j ACCEPT
  iptables -C FORWARD -i "$uplink" -o "$AP_IF" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
    iptables -A FORWARD -i "$uplink" -o "$AP_IF" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
  FORWARDING=true
done

if $PI_ONLINE && $FORWARDING && $AP_ACTIVE && [[ -n "$U_LIST" ]]; then
  write_state green true true true "$U_LIST" "Pi online und AP-Freigabe aktiv"
elif $PI_ONLINE && [[ -n "$U_LIST" ]]; then
  write_state yellow true false "$AP_ACTIVE" "$U_LIST" "Pi online, aber AP-Freigabe unvollständig"
else
  write_state red false "$FORWARDING" "$AP_ACTIVE" "$U_LIST" "Pi hat gerade keinen Internet-Uplink"
fi
EOS
chmod +x "$APPLY_SCRIPT"

printf 'net.ipv4.ip_forward=1\n' > "$SYSCTL_FILE"
sysctl -q -w net.ipv4.ip_forward=1 >/dev/null

cat > "$DISPATCHER_SCRIPT" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
/usr/local/lib/autodarts/apply-ap-internet-rules.sh >/dev/null 2>&1 || true
EOS
chmod +x "$DISPATCHER_SCRIPT"

cat > "$SERVICE_FILE" <<EOS
[Unit]
Description=Autodarts AP Internet Sharing
After=network-online.target NetworkManager.service
Wants=network-online.target

[Service]
Type=oneshot
Environment=AP_IF=$AP_IF
Environment=UPLINK_IFS=$UPLINK_IFS
ExecStart=$APPLY_SCRIPT
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOS

systemctl daemon-reload
systemctl enable autodarts-ap-internet.service >/dev/null
systemctl start autodarts-ap-internet.service >/dev/null || true

log "Wende Regeln sofort an"
AP_IF="$AP_IF" UPLINK_IFS="$UPLINK_IFS" "$APPLY_SCRIPT"
log "Fertig. Status: $STATE_FILE"
cat "$STATE_FILE"
