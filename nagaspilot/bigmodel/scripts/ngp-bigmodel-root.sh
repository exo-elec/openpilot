#!/usr/bin/env bash
# Root side of the phone big-model link: a CDC-NCM USB gadget on the comma's USB-C port, 192.168.60.1/24 on its netdev, and
# DHCP for the phone. Run as `sudo -n bash ngp-bigmodel-root.sh <command>` by nagaspilot.bigmodel.gadget; nothing else is root.
#
#   gadget [--bulk]  create the gadget (idempotent) without binding it: CDC-NCM for an iPhone, or with --bulk a FunctionFS
#             vendor interface for an Android phone (the owner then writes the descriptors and binds the controller itself)
#   bind      bind the gadget to the device controller (the phone enumerates us)
#   net       address the netdev and start DHCP (the netdev exists only after bind)
#   unbind    release the controller, keep the gadget
#   teardown  unbind, stop DHCP, remove the gadget
#   status    one line: gadget/bound/net
#
# Test hooks (never set on a device): NGP_CONFIGFS, NGP_UDC_DIR, NGP_RUN_DIR redirect the filesystem roots, and
# NGP_TEST=1 skips the root check and every command that needs a real kernel (mount, ip, nmcli, dnsmasq).
set -euo pipefail

CONFIGFS=${NGP_CONFIGFS:-/sys/kernel/config}
UDC_DIR=${NGP_UDC_DIR:-/sys/class/udc}
RUN=${NGP_RUN_DIR:-/dev/shm}
TEST=${NGP_TEST:-0}
GADGET=$CONFIGFS/usb_gadget/ngpbigmodel
NET_FN=ncm.usb0
FFS_NAME=ngpbigmodel
FFS_FN=ffs.$FFS_NAME
FFS_MOUNT=${NGP_FFS_MOUNT:-/dev/ffs-ngpbigmodel}
OPENPILOT_USER=${NGP_USER:-comma}
ADDR=192.168.60.1
PREFIX=24
DHCP_RANGE=192.168.60.2,192.168.60.254,10m
# Linux Foundation test IDs: bench only. An own VID/PID must be allocated before this ships (docs/NAVPILOT_BIGMODEL.md)
VID=0x1d6b
PID=0x0104
STATUS=$RUN/ngp-bigmodel-net
DNSMASQ_PID=$RUN/ngp-bigmodel-dnsmasq.pid
DNSMASQ_IF=$RUN/ngp-bigmodel-dnsmasq.if
LEASES=$RUN/ngp-bigmodel-dnsmasq.leases

fail() { echo "ngp-bigmodel: $*" >&2; echo "error: $*" > "$STATUS" 2>/dev/null || true; exit 1; }
need_root() { [[ $TEST == 1 || $EUID -eq 0 ]] || fail "must run as root"; }

# the netdev the NCM function created, from its ifname attribute (the kernel names it usb0 unless the modem took that)
netdev() {
  local n
  n=$(cat "$GADGET/functions/$NET_FN/ifname" 2>/dev/null) || return 1
  [[ -n $n ]] || return 1
  echo "$n"
}

bound_udc() { cat "$GADGET/UDC" 2>/dev/null || true; }

first_udc() {
  local u
  for u in "$UDC_DIR"/*; do [[ -e $u ]] && { basename "$u"; return 0; }; done
  return 1
}

cmd_gadget() {
  local bulk=0
  case "${1:-}" in
    '') ;;
    --bulk) bulk=1 ;;
    *) echo "usage: $0 gadget [--bulk]" >&2; exit 2 ;;
  esac
  need_root
  if [[ $TEST != 1 ]]; then
    mountpoint -q "$CONFIGFS" || mount -t configfs none "$CONFIGFS" 2>/dev/null || true
    mountpoint -q "$CONFIGFS" || fail "no configfs at $CONFIGFS; this kernel cannot configure a USB gadget"
  fi
  [[ -d $CONFIGFS/usb_gadget ]] || fail "kernel has no USB gadget support (CONFIG_USB_LIBCOMPOSITE)"
  first_udc >/dev/null || fail "no USB device controller in $UDC_DIR; this device cannot act as a USB gadget"

  # never fight another gadget for the controller (a Chestnut host does not use one, but adb/ffs may)
  local other owner bound
  for other in "$CONFIGFS"/usb_gadget/*/UDC; do
    [[ -e $other ]] || continue
    owner=$(basename "$(dirname "$other")")
    bound=$(cat "$other" 2>/dev/null || true)
    [[ $owner != ngpbigmodel && -n $bound ]] && fail "USB gadget '$owner' already holds the controller ($bound)"
  done

  mkdir -p "$GADGET/strings/0x409" "$GADGET/configs/c.1/strings/0x409"
  echo $VID > "$GADGET/idVendor"
  echo $PID > "$GADGET/idProduct"
  echo 0x0320 > "$GADGET/bcdUSB"             # advertise SuperSpeed
  echo 0x0100 > "$GADGET/bcdDevice"
  echo exopilot > "$GADGET/strings/0x409/manufacturer"
  echo "navpilot big model" > "$GADGET/strings/0x409/product"
  local serial
  serial=$(tr -d '\0' < /proc/device-tree/serial-number 2>/dev/null || true)
  echo "${serial:-0001}" > "$GADGET/strings/0x409/serialnumber"   # any stable string will do
  echo "NCM link" > "$GADGET/configs/c.1/strings/0x409/configuration"
  echo 0xC0 > "$GADGET/configs/c.1/bmAttributes"   # self-powered
  echo 8 > "$GADGET/configs/c.1/MaxPower"
  # one function at a time: switching between iPhone and Android is a teardown first, because unlinking a function from a
  # bound config force-unbinds the controller
  if [[ $bulk -eq 1 ]]; then
    [[ -L $GADGET/configs/c.1/$NET_FN ]] && fail "the gadget is in network mode; run teardown before switching to bulk"
    # the mkdir instantiates the function and registers functionfs, so a kernel without it fails here
    mkdir -p "$GADGET/functions/$FFS_FN" || fail "kernel has no ffs gadget function (CONFIG_USB_CONFIGFS_F_FS)"
    [[ -L $GADGET/configs/c.1/$FFS_FN ]] || ln -s "$GADGET/functions/$FFS_FN" "$GADGET/configs/c.1/$FFS_FN"
    mkdir -p "$FFS_MOUNT"
    if [[ $TEST != 1 ]]; then
      # owned by the user openpilot runs as: bigmodeld writes ep0 and modeld reads ep1/ep2
      mountpoint -q "$FFS_MOUNT" || mount -t functionfs -o "uid=$(id -u "$OPENPILOT_USER"),gid=$(id -g "$OPENPILOT_USER")" \
        "$FFS_NAME" "$FFS_MOUNT" || fail "could not mount functionfs at $FFS_MOUNT"
      [[ -e $FFS_MOUNT/ep0 ]] || fail "functionfs mounted at $FFS_MOUNT but has no ep0"
      # the owner binds the controller as the openpilot user, so hand it that one attribute
      chown "$OPENPILOT_USER" "$GADGET/UDC" 2>/dev/null || true
    fi
    echo "gadget ready at $GADGET (bulk), functionfs at $FFS_MOUNT"
    return 0
  fi
  [[ -L $GADGET/configs/c.1/$FFS_FN ]] && fail "the gadget is in bulk mode; run teardown before switching to network"
  mkdir -p "$GADGET/functions/$NET_FN"
  # link once: unlinking from a bound config force-unbinds the controller, so an unconditional ln -sf would drop a live link
  [[ -L $GADGET/configs/c.1/$NET_FN ]] || ln -s "$GADGET/functions/$NET_FN" "$GADGET/configs/c.1/$NET_FN"
  echo "gadget ready at $GADGET"
}

cmd_bind() {
  need_root
  [[ -d $GADGET ]] || fail "no gadget; run gadget first"
  local u
  u=$(first_udc) || fail "no USB device controller"
  [[ $(bound_udc) == "$u" ]] || echo "$u" > "$GADGET/UDC" || fail "could not bind $u"
  echo "bound to $u"
}

cmd_net() {
  need_root
  local dev
  if ! dev=$(netdev); then
    echo "error: no netdev yet; it appears when the gadget is bound" > "$STATUS" 2>/dev/null || true
    echo "no netdev yet" >&2
    return 0
  fi
  if [[ $TEST == 1 ]]; then echo "ok $ADDR $dev" > "$STATUS"; echo "test: would address $dev"; return 0; fi
  # NetworkManager manages every device on AGNOS and would DHCP on it itself
  nmcli dev set "$dev" managed no >/dev/null 2>&1 || true
  ip addr replace "$ADDR/$PREFIX" dev "$dev" || fail "could not set $ADDR/$PREFIX on $dev"
  ip link set "$dev" up || fail "could not bring $dev up"
  # dnsmasq binds the interface by name: a netdev that came back under a new name needs a fresh one
  if [[ -f $DNSMASQ_PID ]] && kill -0 "$(cat "$DNSMASQ_PID")" 2>/dev/null; then
    if [[ $(cat "$DNSMASQ_IF" 2>/dev/null || true) != "$dev" ]]; then kill "$(cat "$DNSMASQ_PID")" 2>/dev/null || true; sleep 0.2; fi
  fi
  if ! { [[ -f $DNSMASQ_PID ]] && kill -0 "$(cat "$DNSMASQ_PID")" 2>/dev/null; }; then
    rm -f "$DNSMASQ_PID"
    # DHCP for the phone and only that: no router (option 3) and no DNS (option 6), so the phone keeps its default route
    # over Wi-Fi. No DNS service (--port=0).
    dnsmasq --conf-file=/dev/null --bind-interfaces --interface="$dev" --except-interface=lo --port=0 \
      --dhcp-range="$DHCP_RANGE" --dhcp-option=3 --dhcp-option=6 --dhcp-leasefile="$LEASES" --pid-file="$DNSMASQ_PID" \
      || fail "dnsmasq would not start on $dev"
    echo "$dev" > "$DNSMASQ_IF"
  fi
  echo "ok $ADDR $dev" > "$STATUS"
  echo "$dev at $ADDR/$PREFIX, DHCP $DHCP_RANGE"
}

cmd_unbind() {
  need_root
  [[ -e $GADGET/UDC ]] && echo "" > "$GADGET/UDC" || true
}

cmd_teardown() {
  need_root
  cmd_unbind
  if [[ $TEST != 1 && -f $DNSMASQ_PID ]]; then kill "$(cat "$DNSMASQ_PID")" 2>/dev/null || true; fi
  rm -f "$DNSMASQ_PID" "$DNSMASQ_IF" "$STATUS"
  if [[ -d $GADGET ]]; then
    rm -f "$GADGET/configs/c.1/$NET_FN" "$GADGET/configs/c.1/$FFS_FN"
    if [[ $TEST != 1 ]] && mountpoint -q "$FFS_MOUNT" 2>/dev/null; then umount "$FFS_MOUNT" 2>/dev/null || true; fi
    if [[ $TEST == 1 ]]; then rm -rf "$GADGET"; else
      rmdir "$GADGET/configs/c.1/strings/0x409" "$GADGET/configs/c.1" "$GADGET/functions/$NET_FN" "$GADGET/functions/$FFS_FN" "$GADGET/strings/0x409" "$GADGET" 2>/dev/null || true
    fi
  fi
  echo "torn down"
}

cmd_status() {
  local g=no b=no n=no
  [[ -d $GADGET ]] && g=yes
  [[ -n $(bound_udc) ]] && b=yes
  [[ -f $STATUS ]] && n=$(cat "$STATUS")
  echo "gadget=$g bound=$b net=$n"
}

case "${1:-}" in
  gadget) shift; cmd_gadget "$@" ;;
  bind) cmd_bind ;;
  net) cmd_net ;;
  unbind) cmd_unbind ;;
  teardown) cmd_teardown ;;
  status) cmd_status ;;
  *) echo "usage: $0 gadget|bind|net|unbind|teardown|status" >&2; exit 2 ;;
esac
