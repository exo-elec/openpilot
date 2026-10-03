"""Regression test for common/transformations/camera.py's DEVICE_CAMERAS
entries.

The failure this guards is silent: a platform with no entry in DEVICE_CAMERAS
misses the dict and falls back to stock comma-3's _ar_ox_config (1928x1208,
focal 2648.0/567.0) -- the wrong resolution and the wrong focal length, not
merely a less precise version of the right ones. Nothing raises; the geometry
is just quietly wrong.
"""
import os
import subprocess
import sys



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


def test_get_device_camera_config_end_to_end_on_rk3588():
  w, h, f = _get_device_camera_config_for('rk3588')
  assert (w, h) == (1920, 1280)
  assert f == 2667.0
