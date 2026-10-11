"""Offroad alerts for the phone-hosted big model: the phone link's counterpart to upstream's ChestnutStatus.

Pure policy. Mirrors the parts of upstream's system/hardware/chestnut/status.py (master 6f4c0faf1) that have a phone
equivalent, with the same thresholds and rules:

- NotDetected: the link is enabled and no USB host is attached to the gadget, 10 s after start while parked
  (`time.monotonic() - started > 10`), or after the cable had been seen and was lost while driving (`usb_failed`).
- UsbSlow: a host is attached but the link is below 5 Gbps (`speedMbps < 5000`), i.e. not USB 3. Offroad only.
- ModelError: a big model was expected, finished loading, and is not active while the cable is still there
  (`model_failed`), unless NotDetected already says why. Cleared at the next start.

Not mirrored, no phone equivalent: PCIe/12 V power, GPU and memory temperature (the phone's own telemetry is not
published), firmware flashing, and the release-branch check. The alerts are warnings (severity 0), as upstream's.
"""
SLOW_MBPS = 5000.0
NOT_DETECTED_AFTER_S = 10.0

NOT_DETECTED = "Offroad_BigModelNotDetected"
USB_SLOW = "Offroad_BigModelUsbSlow"
MODEL_ERROR = "Offroad_BigModelModelError"
ALERTS = (NOT_DETECTED, USB_SLOW, MODEL_ERROR)


class PhoneLinkStatus:
  def __init__(self, started: float):
    self.started = started
    self.offroad = True
    self.usb_seen = False
    self.usb_failed = False
    self.model_loading_seen = False
    self.model_attempted = False
    self.model_failed = False

  def update(self, now: float, offroad: bool, enabled: bool, cable: bool, speed_mbps: float | None,
             loading: bool, active: bool | None) -> dict[str, tuple[bool, str | None]]:
    """Alert name -> (show, extra text) for this cycle. `active` is True/False/None as the gate reads it."""
    if self.offroad and not offroad:       # a new drive: the same reset upstream does
      self.usb_seen = cable
      self.usb_failed = False
      self.model_loading_seen = False
      self.model_attempted = False
      self.model_failed = False

    self.model_loading_seen |= loading
    self.model_attempted |= self.model_loading_seen and not loading and active is not None
    self.model_failed |= not offroad and self.model_attempted and active is False and cable

    if not offroad and self.usb_seen and not cable:
      self.usb_failed = True
    if offroad and cable:
      self.usb_failed = False
    if not offroad and cable:
      self.usb_seen = True
    if self.usb_failed:
      self.model_failed = False

    missing = enabled and (self.usb_failed or (offroad and now - self.started > NOT_DETECTED_AFTER_S and not cable))
    slow = enabled and offroad and cable and speed_mbps is not None and speed_mbps < SLOW_MBPS
    error = enabled and self.model_failed and not missing
    self.offroad = offroad
    return {
      NOT_DETECTED: (bool(missing), None),
      USB_SLOW: (bool(slow), f"{int(speed_mbps)} Mbps" if slow else None),
      MODEL_ERROR: (bool(error), None),
    }
