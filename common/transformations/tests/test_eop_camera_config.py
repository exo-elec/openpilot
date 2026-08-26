"""Regression test for common/transformations/camera.py's RK3576 entries,
added 2026-08-26.

Before this fix, DEVICE_CAMERAS had no ("rk3576", ...) keys at all, so
get_device_camera_config() on RK3576 would miss the dict and silently fall
back to stock comma-3's _ar_ox_config (1928x1208, focal 2648.0/567.0) --
wrong resolution and focal length, not just "less precise than RK3588's".
"""
import os
import subprocess
import sys

from openpilot.common.transformations.camera import DEVICE_CAMERAS, _ar_ox_config


def test_rk3576_keys_exist_in_device_cameras():
  assert ("rk3576", "ox03c10") in DEVICE_CAMERAS
  assert ("rk3576", "gc4653") in DEVICE_CAMERAS
  assert ("rk3576", "unknown") in DEVICE_CAMERAS


def test_rk3576_fallback_config_is_not_stock_comma_ar_ox():
  cfg = DEVICE_CAMERAS[("rk3576", "ox03c10")]
  assert cfg.fcam.size != _ar_ox_config.fcam.size or cfg.fcam.focal_length != _ar_ox_config.fcam.focal_length


def test_rk3576_matches_rk3588_for_shared_lens_sensor_specs():
  """mono_narrow/mono_wide use the identical lens specs as RK3588's
  road/wide_road (8.0mm/1.7mm OX03C10) -- confirmed directly against
  hal.platform.rk3576_camera_geometry.py, not assumed. Same physical
  camera modules, so the numbers should match exactly."""
  rk3588_cfg = DEVICE_CAMERAS[("rk3588", "ox03c10")]
  rk3576_cfg = DEVICE_CAMERAS[("rk3576", "ox03c10")]
  assert rk3576_cfg.fcam.size == rk3588_cfg.fcam.size
  assert rk3576_cfg.fcam.focal_length == rk3588_cfg.fcam.focal_length
  assert rk3576_cfg.ecam.size == rk3588_cfg.ecam.size
  assert rk3576_cfg.ecam.focal_length == rk3588_cfg.ecam.focal_length


def _get_device_camera_config_for(hardware_env: str) -> tuple:
  env = dict(os.environ)
  env['HARDWARE'] = hardware_env
  code = (
    "from openpilot.common.transformations.camera import get_device_camera_config\n" +
    "cfg = get_device_camera_config('ox03c10')\n" +
    "print(cfg.fcam.width, cfg.fcam.height, cfg.fcam.focal_length)"
  )
  repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
  result = subprocess.run(
    [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30, cwd=repo_root,
  )
  assert result.returncode == 0, result.stderr
  w, h, f = result.stdout.strip().split()
  return int(w), int(h), float(f)


def test_get_device_camera_config_end_to_end_on_rk3576():
  """HARDWARE/PlatformRegistry.create() are singletons computed at import
  time, so this needs a fresh interpreter per platform, not just changing
  os.environ after the fact (which silently has no effect -- exactly the
  kind of thing that could hide this bug from a less careful test)."""
  w, h, f = _get_device_camera_config_for('rk3576')
  assert (w, h) == (1920, 1280)
  assert f == 2667.0


def test_get_device_camera_config_end_to_end_on_rk3588():
  w, h, f = _get_device_camera_config_for('rk3588')
  assert (w, h) == (1920, 1280)
  assert f == 2667.0
