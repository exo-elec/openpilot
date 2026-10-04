"""Applies a driving mode by writing the underlying params once whenever the mode param changes."""
import time

from nagaspilot.controls.ngp_drive_mode import CUSTOM, detect, settings_for

POLL_S = 1.0

NGP_KEYS = {"mode": "ngp_lon_drive_mode", "accel": "ngp_lon_accel_profile", "personality": "LongitudinalPersonality",
            "gap": "ngp_lon_adaptive_gap"}


class DriveModeApplier:
  def __init__(self, params, keys=NGP_KEYS):
    self.params = params
    self.keys = keys
    self._last_mode: str | None = None
    self._last_poll = 0.0

  def _mode(self) -> str:
    raw = self.params.get(self.keys["mode"])
    mode = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
    return mode or CUSTOM

  def update(self, now: float | None = None) -> bool:
    """Poll about once a second; True when a preset was written this call."""
    now = time.monotonic() if now is None else now
    if now - self._last_poll < POLL_S:
      return False
    self._last_poll = now
    mode = self._mode()
    if mode == self._last_mode:
      return False
    self._last_mode = mode
    preset = settings_for(mode)
    if preset is None:
      return False
    self.params.put(self.keys["accel"], preset.accel_profile)
    self.params.put(self.keys["personality"], preset.personality)
    self.params.put_bool(self.keys["gap"], preset.adaptive_gap)
    return True

  def current(self) -> str:
    """The mode the live settings match (`custom` once the driver changed something by hand)."""
    accel = self.params.get(self.keys["accel"])
    accel = accel.decode() if isinstance(accel, (bytes, bytearray)) else (accel or "normal")
    personality = self.params.get(self.keys["personality"])
    return detect(accel, 1 if personality is None else int(personality), self.params.get_bool(self.keys["gap"]))
