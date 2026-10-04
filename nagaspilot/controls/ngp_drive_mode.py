"""Driving modes: one choice that sets acceleration profile, following style and adaptive gap together.

Pure presets, no Params access. Personality values are the `LongitudinalPersonality` enum numbers
(aggressive 0, standard 1, relaxed 2). Speed offset is a speed choice, not a style, so it is not part of a mode.
"""
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
