"""Tier B build-profile and native C++ Qt UI snapshot verification for 01M (RK3588 1024x600)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from openpilot.common import build_profile

ROOT = Path(__file__).resolve().parents[2]
UI_SNAPSHOT = ROOT / "selfdrive/ui/tests/ui_snapshot"


def test_01m_build_profile_targets_cpp_rk3588_1024x600():
  assert build_profile.UI_BACKEND == "cpp"
  assert build_profile.DEFAULT_SOC == "rk3588"
  assert build_profile.DISPLAY_SIZE == (1024, 600)


def test_01m_cpp_ui_snapshot_renders_panels_and_hides_advanced(tmp_path):
  if not UI_SNAPSHOT.exists():
    pytest.skip("ui_snapshot binary not built (run `scons selfdrive/ui/tests/ui_snapshot`)")

  env = os.environ.copy()
  env["QT_QPA_PLATFORM"] = "offscreen"
  env["OPENPILOT_PREFIX"] = f"sim_01m_ui_{os.getpid()}"

  # Visible settings panels render cleanly at 1024x600 with real non-blank UI content
  for panel in ("Device", "WiFi", "Bluetooth", "Driving", "Safety", "Display"):
    out = tmp_path / f"{panel}.png"
    proc = subprocess.run([str(UI_SNAPSHOT), "--panel", panel, "--out", str(out)],
                          cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"ui_snapshot failed on {panel}: {proc.stderr}"
    im = Image.open(out)
    assert im.size == (1024, 600), f"{panel} rendered at {im.size}, expected (1024, 600)"
    assert float(np.asarray(im).std()) > 10.0, f"{panel} rendered blank"

  # Driving panel contains Drive Mode row (01M Driving settings reorganization)
  out_dm = tmp_path / "Driving_drive_mode.png"
  proc_dm = subprocess.run([str(UI_SNAPSHOT), "--panel", "Driving", "--find", "Drive Mode", "--out", str(out_dm)],
                           cwd=ROOT, env=env, capture_output=True, text=True, check=False)
  assert proc_dm.returncode == 0, f"Drive Mode row missing from 01M Driving panel: {proc_dm.stderr}"

  # Advanced panel is hidden by default until 8 quick taps on Device -> Dongle ID
  out_adv = tmp_path / "Advanced.png"
  proc_adv = subprocess.run([str(UI_SNAPSHOT), "--panel", "Advanced", "--out", str(out_adv)],
                            cwd=ROOT, env=env, capture_output=True, text=True, check=False)
  assert proc_adv.returncode != 0 and 'no panel named "Advanced"' in proc_adv.stderr


@pytest.mark.slow
def test_01m_metadrive_dlon_drive_modes_highway_urban_dynamic():
  """01M closed-loop MetaDrive verification for Settings -> Driving -> Drive Mode (DlonMode):
  Highway (0/CHILL) stays in ACC on sharp curve & stop; Urban (1/EXPERIMENTAL) runs E2E blended;
  Dynamic (2/AUTO) stays ACC on open highway and switches to E2E blended on sharp curve."""
  pytest.importorskip("metadrive")
  from metadrive.envs.metadrive_env import MetaDriveEnv
  from openpilot.common.realtime import DT_MDL
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner

  from exopilot.selfdrive.controls.lib.dlon import DLON, DLONMode
  from exopilot.tests.test_eop10_metadrive_loop import (
    _PedalPI,
    _lane_center_steer,
    _make_cp,
    _make_sm,
    _straight_env_config,
    _warmup_to_speed,
  )

  env = MetaDriveEnv(_straight_env_config())
  try:
    env.reset()
    v0 = _warmup_to_speed(env, 15.0)
    pedal = _PedalPI()
    prev_v = v0

    # 1. DlonMode=0 (Highway / CHILL): remains ACC even on sharp curve curvature
    p_hwy = LongitudinalPlanner(_make_cp(), init_v=v0)
    p_hwy.dlon = DLON(get_bool=lambda k: False, get=lambda k: 0 if k == "DlonMode" else None)
    p_hwy.dlon.last_param_update = -1e9
    hwy_e2e = False
    for _ in range(30):
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      steer, _, on_lane = _lane_center_steer(env)
      assert on_lane
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=65.0, a_ego=a_ego, road_kappa=1.0 / 40.0)
      p_hwy.update(sm)
      hwy_e2e |= bool(p_hwy.dlon_e2e_enabled)
      env.step([steer, pedal.step(float(p_hwy.output_a_target), a_ego)])
    assert p_hwy.dlon.mode is DLONMode.CHILL and not hwy_e2e, "01M Highway (CHILL) falsely entered E2E"

    # 2. DlonMode=1 (Urban / EXPERIMENTAL): engages blended E2E longitudinal mode
    p_urb = LongitudinalPlanner(_make_cp(), init_v=float(env.vehicle.speed))
    p_urb.dlon = DLON(get_bool=lambda k: False, get=lambda k: 1 if k == "DlonMode" else None)
    p_urb.dlon.last_param_update = -1e9
    urb_e2e = False
    for _ in range(40):
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      steer, _, on_lane = _lane_center_steer(env)
      assert on_lane
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=65.0, a_ego=a_ego, road_kappa=0.0)
      p_urb.update(sm)
      urb_e2e |= bool(p_urb.dlon_e2e_enabled)
      env.step([steer, pedal.step(float(p_urb.output_a_target), a_ego)])
    assert p_urb.dlon.mode is DLONMode.EXPERIMENTAL and urb_e2e, "01M Urban (EXPERIMENTAL) failed to engage E2E"

    # 3. DlonMode=2 (Dynamic / AUTO): ACC on straight highway, then sharp_curve trigger switches to E2E
    p_dyn = LongitudinalPlanner(_make_cp(), init_v=float(env.vehicle.speed))
    p_dyn.dlon = DLON(get_bool=lambda k: True, get=lambda k: 2 if k == "DlonMode" else None)
    p_dyn.dlon.last_param_update = -1e9
    dyn_straight_e2e = False
    dyn_curve_trigger = False
    dyn_curve_e2e = False
    for step in range(80):
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      steer, _, on_lane = _lane_center_steer(env)
      assert on_lane
      kappa = 0.0 if step < 25 else 1.0 / 35.0
      if step >= 25:
        # Advance wall-clock enter debounce deterministically by 1 MetaDrive physics tick (DT_MDL)
        p_dyn.dlon._enter_ts -= DT_MDL
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=65.0, a_ego=a_ego, road_kappa=kappa)
      p_dyn.update(sm)
      if step < 25:
        dyn_straight_e2e |= bool(p_dyn.dlon_e2e_enabled)
      else:
        dyn_curve_trigger |= bool(p_dyn.dlon_result.get("triggers", {}).get("sharp_curve"))
        dyn_curve_e2e |= bool(p_dyn.dlon_e2e_enabled)
      env.step([steer, pedal.step(float(p_dyn.output_a_target), a_ego)])
    assert p_dyn.dlon.mode is DLONMode.AUTO and not dyn_straight_e2e and dyn_curve_trigger and dyn_curve_e2e
    print(
      f"METADRIVE_REPORT 01M DLON_MODES: highway_e2e={hwy_e2e} urban_e2e={urb_e2e}",
      f"dynamic_straight_e2e={dyn_straight_e2e} dynamic_curve_trigger={dyn_curve_trigger} dynamic_curve_e2e={dyn_curve_e2e}",
    )
  finally:
    env.close()
