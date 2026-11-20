"""Regression tests for system/hardware/__init__.py's platform flags, and for
both independent Rockchip adapters.

HARDWARE/ROCKCHIP/TICI/etc. are computed once at import time from
PlatformRegistry.create(), so exercising a different platform means a fresh
interpreter per case (subprocess), not just re-calling a function -- reload()
would risk stale submodule state (e.g. PlatformRegistry's class-level
_platforms dict persisting across reloads).
"""

from __future__ import annotations

import os
import subprocess

import pytest
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _device_tree_soc() -> str | None:
  try:
    with open('/proc/device-tree/compatible') as f:
      compatible = f.read().lower()
  except OSError:
    return None
  return next((soc for soc in ('rk3588', 'rk3576') if soc in compatible), None)


# Detection reads the device tree first; HARDWARE only applies where there is none (a PC), so a board can never
# be mislabelled. The HARDWARE-driven cases below therefore run off-device only.
ON_BOARD = _device_tree_soc() is not None
off_device = pytest.mark.skipif(ON_BOARD, reason="HARDWARE only applies without a Rockchip device tree")


def _run(code: str, hardware_env: str | None):
  env = dict(os.environ)
  if hardware_env is None:
    env.pop('HARDWARE', None)
  else:
    env['HARDWARE'] = hardware_env
  return subprocess.run([sys.executable, "-c", code], env=env,
                        capture_output=True, text=True, timeout=30, cwd=REPO)


def _flags_for(hardware_env: str | None) -> dict[str, bool]:
  code = ("from openpilot.system.hardware import RK3576, ROCKCHIP, TICI, PC\n" +
          "print(RK3576, ROCKCHIP, TICI, PC)")
  result = _run(code, hardware_env)
  assert result.returncode == 0, result.stderr
  values = result.stdout.strip().split()
  keys = ['RK3576', 'ROCKCHIP', 'TICI', 'PC']
  return dict(zip(keys, (v == 'True' for v in values), strict=True))


@off_device
def test_rockchip_and_tici_are_true_on_rk3576():
  assert _flags_for('rk3576') == {'RK3576': True, 'ROCKCHIP': True,
                                  'TICI': True, 'PC': False}


@off_device
def test_pc_fallback_when_no_hardware_env():
  assert _flags_for(None) == {'RK3576': False, 'ROCKCHIP': False,
                              'TICI': False, 'PC': True}


def test_both_independent_adapters_are_registered():
  result = _run("from openpilot.system.hardware.registry import PlatformRegistry\n"
                "print(sorted(PlatformRegistry._platforms))", None)
  assert result.returncode == 0, result.stderr
  assert all(name in result.stdout for name in ('pc', 'rk3588', 'rk3576'))


@off_device
def test_unknown_hardware_is_rejected_instead_of_mislabeled():
  result = _run("from openpilot.system.hardware import HARDWARE\nprint(HARDWARE)", 'future_soc')
  assert result.returncode != 0
  assert 'Unknown platform: future_soc' in result.stderr


@off_device
def test_rk3588_identity_and_flags_remain_independent():
  result = _run("from openpilot.system.hardware import RK3588, RK3576, ROCKCHIP, HARDWARE\n"
                "print(RK3588, RK3576, ROCKCHIP, HARDWARE.get_device_type())", 'rk3588')
  assert result.returncode == 0, result.stderr
  assert result.stdout.strip() == 'True False True rk3588'


def test_board_classes_are_siblings_not_subclasses_of_each_other():
  from openpilot.system.hardware.rk3576.hardware import RK3576Hardware
  from openpilot.system.hardware.rk3588.hardware import RK3588Hardware
  from openpilot.system.hardware.rockchip_base import RockchipHardware
  assert issubclass(RK3576Hardware, RockchipHardware)
  assert issubclass(RK3588Hardware, RockchipHardware)
  assert not issubclass(RK3576Hardware, RK3588Hardware)
  assert not issubclass(RK3588Hardware, RK3576Hardware)


@pytest.mark.skipif(not ON_BOARD, reason="needs a Rockchip device tree")
def test_on_a_board_the_device_tree_wins_over_hardware_env():
  other = {'rk3588': 'rk3576', 'rk3576': 'rk3588'}[_device_tree_soc()]
  result = _run("from openpilot.system.hardware import HARDWARE\nprint(HARDWARE.get_device_type())", other)
  assert result.returncode == 0, result.stderr
  assert result.stdout.strip() == _device_tree_soc()
