"""Cached longitudinal settings shared by NGP and EOP parameter adapters."""
import time

from nagaspilot.controls.longitudinal_policy import ACCELERATION_PROFILES
from nagaspilot.runtime.feature_keys import NGP_KEYS

CACHE_S = 2.0


def _params():
  from openpilot.common.params import Params
  return Params()


class LongitudinalSettings:
  def __init__(self, keys=NGP_KEYS, params_factory=None, clock=None):
    self.keys = dict(keys)
    self.params_factory = params_factory
    self.clock = clock
    self._cache = {}

  def _read(self, name, default, decode):
    now = (self.clock or time.monotonic)()
    cached = self._cache.get(name)
    if cached is not None and 0 <= now - cached[0] < CACHE_S:
      return cached[1]
    raw = (self.params_factory or _params)().get(self.keys[name])
    value = decode(raw) if raw is not None else default
    self._cache[name] = (now, value)
    return value

  def load_accel_profile(self):
    def decode(raw):
      value = raw.decode("utf-8") if isinstance(raw, bytes) else raw
      return value if value in ACCELERATION_PROFILES else "normal"
    return self._read("accel", "normal", decode)

  def load_adaptive_gap_enabled(self):
    return self._read("gap", False, lambda raw: raw in (True, b"1", "1"))


_default_settings = LongitudinalSettings()


def load_accel_profile():
  return _default_settings.load_accel_profile()


def load_adaptive_gap_enabled():
  return _default_settings.load_adaptive_gap_enabled()
