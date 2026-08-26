"""Regression tests for system/hardware/__init__.py's platform flags.

HARDWARE/ROCKCHIP/TICI/etc. are computed once at import time from
PlatformRegistry.create(), so exercising a different platform means a fresh
interpreter per case (subprocess), not just re-calling a function -- reload()
would risk stale submodule state (e.g. PlatformRegistry's class-level
_platforms dict persisting across reloads).
"""

from __future__ import annotations

import os
import subprocess
import sys


def _flags_for(hardware_env: str | None) -> dict[str, bool]:
  env = dict(os.environ)
  if hardware_env is None:
    env.pop('HARDWARE', None)
  else:
    env['HARDWARE'] = hardware_env
  code = (
    "from openpilot.system.hardware import RK3588, RK3576, ROCKCHIP, TICI, PC\n" +
    "print(RK3588, RK3576, ROCKCHIP, TICI, PC)"
  )
  result = subprocess.run(
    [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30,
    cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
  )
  assert result.returncode == 0, result.stderr
  values = result.stdout.strip().split()
  keys = ['RK3588', 'RK3576', 'ROCKCHIP', 'TICI', 'PC']
  return dict(zip(keys, (v == 'True' for v in values), strict=True))


def test_rockchip_and_tici_are_true_on_rk3588():
  flags = _flags_for('rk3588')
  assert flags == {'RK3588': True, 'RK3576': False, 'ROCKCHIP': True, 'TICI': True, 'PC': False}


def test_rockchip_and_tici_are_true_on_rk3576():
  """Regression test: before the 2026-08-26 fix, ROCKCHIP/TICI were defined
  as `ROCKCHIP = RK3588` only, so this would have incorrectly been False on
  real RK3576 hardware -- silently disabling EGL rendering
  (cameraview.py), the recordd.py encoding path, and updated.py/conftest.py
  hardware-gated behavior on that platform."""
  flags = _flags_for('rk3576')
  assert flags == {'RK3588': False, 'RK3576': True, 'ROCKCHIP': True, 'TICI': True, 'PC': False}


def test_pc_fallback_when_no_hardware_env():
  flags = _flags_for(None)
  assert flags == {'RK3588': False, 'RK3576': False, 'ROCKCHIP': False, 'TICI': False, 'PC': True}
