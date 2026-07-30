#!/bin/bash
# RK3588 NPU Power Management
# Call during suspend/resume to power-gate NPU and save ~500mW.
#
# Derived from vendor SDK debian/overlay/etc/Powermanager/01npu
#
# Usage:
#   npu_powerctrl.sh suspend   # power down NPU
#   npu_powerctrl.sh resume    # power up NPU
#
# WARNING: This script is for RK3588 hardware only.

set -e

# Enforce RK3588 hardware
if ! grep -q "rk3588" /proc/device-tree/compatible 2>/dev/null; then
  echo "ERROR: RK3588 hardware not detected. This script is for RK3588 only."
  exit 1
fi

CMD="${1:-status}"

_do_npu_powerctrl() {
  if command -v npu_powerctrl >/dev/null 2>&1; then
    # Use system npu_powerctrl binary if available
    command npu_powerctrl "$1"
  else
    # Fallback: toggle via sysfs if available
    PWR=/sys/devices/platform/npu/power
    if [ -f "$PWR" ]; then
      echo "$1" > "$PWR"
    fi
  fi
}

case "$CMD" in
  suspend|hibernate|sleep)
    _do_npu_powerctrl -s
    echo "NPU suspended"
    ;;
  resume|thaw|wake)
    _do_npu_powerctrl -r
    echo "NPU resumed"
    ;;
  status)
    if [ -f /sys/devices/platform/npu/power ]; then
      cat /sys/devices/platform/npu/power
    else
      echo "unknown"
    fi
    ;;
  *)
    echo "Usage: $0 {suspend|resume|status}"
    exit 1
    ;;
esac
