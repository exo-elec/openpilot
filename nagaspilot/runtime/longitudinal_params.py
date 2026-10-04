"""Params-backed longitudinal settings with a short cache (moved out of longitudinal_planner.py, behaviour unchanged)."""
import time

from nagaspilot.controls.longitudinal_policy import ACCELERATION_PROFILES

CACHE_S = 2.0

_accel_profile_cache = {"ts": 0.0, "profile": "normal"}
_adaptive_gap_cache = {"ts": 0.0, "enabled": False}


def _params():
  from openpilot.common.params import Params
  return Params()


def load_accel_profile() -> str:
  global _accel_profile_cache
  now = time.monotonic()
  if now - _accel_profile_cache["ts"] < CACHE_S:
    return _accel_profile_cache["profile"]
  value = _params().get("ngp_lon_accel_profile")
  profile = value.decode("utf-8") if value else "normal"
  if profile not in ACCELERATION_PROFILES:
    profile = "normal"
  _accel_profile_cache = {"ts": now, "profile": profile}
  return profile


def load_adaptive_gap_enabled() -> bool:
  global _adaptive_gap_cache
  now = time.monotonic()
  if now - _adaptive_gap_cache["ts"] < CACHE_S:
    return _adaptive_gap_cache["enabled"]
  enabled = _params().get_bool("ngp_lon_adaptive_gap")
  _adaptive_gap_cache = {"ts": now, "enabled": enabled}
  return enabled
