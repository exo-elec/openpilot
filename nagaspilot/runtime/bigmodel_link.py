"""The phone link's USB facts, read from sysfs, and the offroad-alert monitor hardwared runs.

Standard library only (hardwared and the resident gadget owner import it). `cable_connected` is upstream's
cable_connected() for a gadget: the host is there before it has enumerated us. The speed is what the device controller
negotiated; the frame budget needs SuperSpeed (about 1 ms a frame on USB 3, 10 ms on USB 2).
"""
import time
from pathlib import Path

from nagaspilot.controls.ngp_bigmodel_status import ALERTS, PhoneLinkStatus
from nagaspilot.runtime import bigmodel_platform as platform

UDC_ROOT = Path('/sys/class/udc')
# /sys/class/udc/<name>/current_speed, in Mbps
SPEED_MBPS = {'low-speed': 1.5, 'full-speed': 12.0, 'high-speed': 480.0, 'super-speed': 5000.0, 'super-speed-plus': 10000.0}


def cable_connected(root: Path = UDC_ROOT) -> bool:
  """Is a USB host attached to the gadget? Any device controller state but 'not attached' counts ('powered', 'default',
  'addressed', 'configured'); no controller at all does not."""
  try:
    for udc in root.iterdir():
      if (udc / 'state').read_text().strip() not in ('', 'not attached'):
        return True
  except OSError:
    pass
  return False


def udc_speed_mbps(root: Path = UDC_ROOT) -> float | None:
  """The negotiated link speed in Mbps, or None when no controller says (not configured yet, or no controller)."""
  try:
    for udc in sorted(root.iterdir()):
      speed = SPEED_MBPS.get((udc / 'current_speed').read_text().strip())
      if speed is not None:
        return speed
  except OSError:
    pass
  return None


class PhoneLinkMonitor:
  """hardwared's side: reads the setting, the flags modeld/the gate use and the USB facts, and sets the offroad alerts."""

  def __init__(self, clock=time.monotonic, root: Path = UDC_ROOT):
    self._clock = clock
    self._root = root
    self._status = PhoneLinkStatus(clock())

  def update(self, params, offroad: bool, set_alert) -> None:
    try:
      enabled = int(params.get(platform.LINK, return_default=True) or 0) != 0
      alerts = self._status.update(self._clock(), offroad, enabled, cable_connected(self._root), udc_speed_mbps(self._root),
                                   params.get_bool(platform.LOADING), params.get(platform.ACTIVE))
    except Exception:
      return   # a monitor must never take hardwared down; the alerts simply keep their last state
    for name in ALERTS:
      show, extra = alerts[name]
      set_alert(name, show, extra)
