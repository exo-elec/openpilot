"""Regression tests for system/hardware/__init__.py's platform flags, and for
this branch's hardware scope (ExoPilot 02M / RK3576 only).

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

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


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


def test_rockchip_and_tici_are_true_on_rk3576():
  assert _flags_for('rk3576') == {'RK3576': True, 'ROCKCHIP': True,
                                  'TICI': True, 'PC': False}


def test_pc_fallback_when_no_hardware_env():
  assert _flags_for(None) == {'RK3576': False, 'ROCKCHIP': False,
                              'TICI': False, 'PC': True}


class TestBranchHardwareScope:
  """This branch supports ExoPilot 02M (RK3576) hardware only.

  01M lives on dev/01M and dev/EOP10 -- see the branch model in CLAUDE.md.
  These tests are the guard against 01M support drifting back in through a
  rebase or a well-meaning import, which would quietly recreate the coupling
  the branch split exists to remove.
  """

  def test_rk3588_is_not_a_known_platform(self):
    result = _run("from openpilot.system.hardware.registry import PlatformRegistry\n" +
                  "print(sorted(PlatformRegistry._platforms))", None)
    assert result.returncode == 0, result.stderr
    registered = result.stdout.strip()
    assert "rk3576" in registered
    assert "rk3588" not in registered

  def test_asking_for_rk3588_fails_loudly(self):
    # Not silently falling back to pc or rk3576: a board this branch cannot
    # drive must refuse rather than mislabel itself.
    result = _run("from openpilot.system.hardware import HARDWARE\nprint(HARDWARE)", 'rk3588')
    assert result.returncode != 0
    assert "Unknown platform: rk3588" in result.stderr

  def test_the_rk3588_package_is_gone(self):
    result = _run("import openpilot.system.hardware.rk3588.hardware", None)
    assert result.returncode != 0
    assert "ModuleNotFoundError" in result.stderr

  def test_the_board_independent_base_survives(self):
    """RockchipHardware stays even with one board.

    RK3576Hardware used to subclass RK3576Hardware, which made 01M's class
    load-bearing for 02M -- neither board's support could be removed without
    breaking the other's. The base is what broke that tie, so it is what lets
    a board be added back later without recreating it. Collapsing it into
    RK3576Hardware because there is only one board today would undo that.
    """
    from openpilot.system.hardware.rk3576.hardware import RK3576Hardware
    from openpilot.system.hardware.rockchip_base import RockchipHardware
    assert issubclass(RK3576Hardware, RockchipHardware)
    assert RK3576Hardware is not RockchipHardware

  def test_the_shared_half_stays_on_the_base(self):
    # If one of these reappeared on the board class, a second board added
    # later would inherit nothing and start duplicating it again.
    from openpilot.system.hardware.rk3576.hardware import RK3576Hardware
    from openpilot.system.hardware.rockchip_base import RockchipHardware
    shared = ("reboot", "shutdown", "get_serial", "get_dongle_id",
              "get_camera_array_config", "get_stereo_baseline_mm",
              "has_side_cameras", "has_rear_camera", "get_cellular_interface",
              "get_modem_type", "get_rga", "get_mpp", "get_rknn")
    for name in shared:
      assert name not in vars(RK3576Hardware), f"{name} should live on the base"
      assert name in vars(RockchipHardware), f"{name} missing from the base"

  def test_rk3576_still_owns_what_is_board_specific(self):
    from openpilot.system.hardware.rk3576.hardware import RK3576Hardware
    # Modem power control is the real electrical difference between boards:
    # 02M bit-bangs the EC25 enable line directly.
    for name in ("modem_power_on", "modem_power_off", "detect",
                 "get_device_type", "get_platform", "get_capabilities"):
      assert name in vars(RK3576Hardware), f"RK3576 should own {name}"

  def test_the_camera_array_is_02m_shaped(self):
    from openpilot.system.hardware.rk3576.hardware import RK3576Hardware
    assert RK3576Hardware.MIPI_CAMERA_NAMES == ("mono_narrow", "mono_wide",
                                                "mono_tele", "stereo_left",
                                                "stereo_right")
    assert RK3576Hardware.HAS_TELE_ROAD        # 02M has a telephoto
