"""Driving modes: one choice that sets acceleration profile, following style and adaptive gap together.

Pure presets, no Params access. Personality values are the `LongitudinalPersonality` enum numbers
(aggressive 0, standard 1, relaxed 2). Speed offset is a speed choice, not a style, so it is not part of a mode.
"""
import time
from dataclasses import dataclass

CUSTOM = "custom"
AGGRESSIVE, STANDARD, RELAXED = 0, 1, 2


@dataclass(frozen=True)
class DriveModeSettings:
  accel_profile: str
  personality: int
  adaptive_gap: bool


MODES = {
  "eco": DriveModeSettings("eco", RELAXED, False),
  "normal": DriveModeSettings("normal", STANDARD, False),
  "sport": DriveModeSettings("sport", AGGRESSIVE, False),
}


def settings_for(mode: str) -> DriveModeSettings | None:
  """Preset for a mode name; None for `custom` or anything unknown (nothing is written)."""
  return MODES.get(mode)


def detect(accel_profile: str, personality: int, adaptive_gap: bool) -> str:
  """Name of the mode the live settings match, else `custom` (e.g. after the steering-wheel distance button)."""
  live = DriveModeSettings(accel_profile, int(personality), bool(adaptive_gap))
  for name, preset in MODES.items():
    if preset == live:
      return name
  return CUSTOM


POLL_S = 1.0


class DriveModeApplier:
  def __init__(self, params, keys=None):
    keys = keys or {"mode": "DriveMode", "accel": "AccelerationProfile", "personality": "LongitudinalPersonality", "gap": "AdaptiveGapEnabled"}
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
