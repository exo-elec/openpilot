"""Tier B build-profile, RK3576 CI/adapter, and wide C++ Qt UI snapshot verification for 02M (RK3576 1600x600)."""

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


def test_02m_build_profile_and_ci_target_cpp_rk3576_1600x600():
  assert build_profile.UI_BACKEND == "cpp"
  assert build_profile.DEFAULT_SOC == "rk3576"
  assert build_profile.DISPLAY_SIZE == (1600, 600)

  jenkins = (ROOT / "Jenkinsfile").read_text()
  assert "rk3576" in jenkins.lower()

  from openpilot.system.hardware.rk3576.hardware import RK3576Hardware
  assert RK3576Hardware().get_device_type() == "rk3576"


def test_02m_cpp_ui_snapshot_renders_wide_1600x600_panels_and_hides_advanced(tmp_path):
  if not UI_SNAPSHOT.exists():
    pytest.skip("ui_snapshot binary not built (run `scons selfdrive/ui/tests/ui_snapshot`)")

  env = os.environ.copy()
  env["QT_QPA_PLATFORM"] = "offscreen"
  env["OPENPILOT_PREFIX"] = f"sim_02m_ui_{os.getpid()}"

  for panel in ("Device", "WiFi", "Bluetooth", "Driving", "Safety", "Display"):
    out = tmp_path / f"{panel}.png"
    proc = subprocess.run([str(UI_SNAPSHOT), "--panel", panel, "--out", str(out)],
                          cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"ui_snapshot failed on {panel}: {proc.stderr}"
    with Image.open(out) as im:
      assert im.size == (1600, 600), f"{panel} rendered at {im.size}, expected (1600, 600)"
      assert float(np.asarray(im).std()) > 10.0, f"{panel} rendered blank"

  # Driving panel Drive Mode row & full-height render at 1600px wide
  out_full = tmp_path / "Driving_full.png"
  proc_full = subprocess.run([str(UI_SNAPSHOT), "--panel", "Driving", "--full", "--out", str(out_full)],
                             cwd=ROOT, env=env, capture_output=True, text=True, check=False)
  assert proc_full.returncode == 0
  with Image.open(out_full) as im_full:
    assert im_full.size[0] == 1600 and im_full.size[1] > 1200

  # Advanced panel stays hidden until unlocked
  out_adv = tmp_path / "Advanced.png"
  proc_adv = subprocess.run([str(UI_SNAPSHOT), "--panel", "Advanced", "--out", str(out_adv)],
                            cwd=ROOT, env=env, capture_output=True, text=True, check=False)
  assert proc_adv.returncode != 0 and 'no panel named "Advanced"' in proc_adv.stderr


@pytest.mark.slow
def test_02m_metadrive_4cam_radar4d_costmap_and_dlon_modes():
  """02M MetaDrive 3D verification:
  1. 02M 4-camera/5-stream CameraSim (road + wide + tele + stereo L/R) OpenCL NV12 VisionIPC streaming.
  2. Real 3D MetaDrive adjacent-lane vehicle -> capnp radar4d Wi-Fi point cloud & cluster -> GridD._fuse_radar4d FusionCostmap.
  3. 02M Settings -> Driving -> Drive Mode (DlonMode: Highway / Urban / Dynamic) closed-loop verification."""
  pytest.importorskip("metadrive")
  pytest.importorskip("pyopencl")
  import math
  import cereal.messaging as messaging
  from metadrive.envs.metadrive_env import MetaDriveEnv
  from openpilot.common.realtime import DT_MDL
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  from openpilot.tools.sim.lib.camera_sim import STEREO_H, STEREO_W, CameraSim
  from openpilot.tools.sim.lib.common import H, W

  from exopilot.selfdrive.controls.lib.dlon import DLON, DLONMode
  from exopilot.selfdrive.gridd.fusion_costmap import COST_OBSTACLE, COST_ROAD, FusionCostmap
  from exopilot.selfdrive.gridd.occupancy import GridD
  from exopilot.tests.test_eop10_metadrive_loop import (
    _PedalPI,
    _clear_3d_vehicles,
    _drive_3d_actor,
    _lane_center_steer,
    _make_cp,
    _make_sm,
    _spawn_3d_vehicle,
    _straight_env_config,
    _warmup_to_speed,
  )

  # 1. Verify 02M CameraSim defaults tele_camera=True on RK3576 and streams all 5 NV12 channels
  cs = CameraSim(dual_camera=True, stereo_camera=True)
  try:
    assert cs.tele_camera is True and cs.stereo_camera is True
    rgb_road = np.full((H, W, 3), 96, dtype=np.uint8)
    rgb_stereo = np.full((STEREO_H, STEREO_W, 3), 128, dtype=np.uint8)
    yuv_road = cs.rgb_to_yuv(rgb_road)
    yuv_stereo = cs.rgb_to_yuv_stereo(rgb_stereo)
    assert len(yuv_road) == W * H * 3 // 2 and len(yuv_stereo) == STEREO_W * STEREO_H * 3 // 2
    cs.cam_send_yuv_road(yuv_road)
    cs.cam_send_yuv_wide_road(yuv_road)
    cs.cam_send_yuv_tele_road(yuv_road)
    cs.cam_send_yuv_stereo_left(yuv_stereo)
    cs.cam_send_yuv_stereo_right(yuv_stereo)
    assert (cs.frame_road_id, cs.frame_wide_id, cs.frame_tele_id, cs.frame_stereo_left_id, cs.frame_stereo_right_id) == (1, 1, 1, 1, 1)
  finally:
    cs.close()

  # 2. Real 3D MetaDrive vehicle -> capnp radar4d Wi-Fi point cloud + cluster -> GridD._fuse_radar4d FusionCostmap
  env = MetaDriveEnv(_straight_env_config())
  spawned: list = []
  try:
    env.reset()
    _warmup_to_speed(env, 14.0)
    ego_lane_idx = env.vehicle.lane_index[2]
    adj_lat = 3.6 if ego_lane_idx == 0 else -3.6
    adj_car = _spawn_3d_vehicle(env, long_offset_m=14.0, lat_offset_m=adj_lat)
    spawned.append(adj_car)
    for _ in range(10):
      steer, _, _ = _lane_center_steer(env)
      _drive_3d_actor(adj_car, target_v=12.0, target_lat_offset=adj_lat, ref_lane=env.vehicle.lane)
      env.step([steer, 0.1])

    ego_long, _ = env.vehicle.lane.local_coordinates(env.vehicle.position)
    adj_long, adj_lat_now = env.vehicle.lane.local_coordinates(adj_car.position)
    d_rel = float(adj_long - ego_long)
    y_rel = -float(adj_lat_now)  # vehicle frame +left
    rng = math.hypot(d_rel, y_rel)
    az_deg = math.degrees(math.atan2(y_rel, d_rel))

    r4d = messaging.new_message("radar4d").radar4d
    pts = r4d.init("points", 2)
    # Moving return on the 3D adjacent-lane car
    pts[0].rangM = float(rng)
    pts[0].azimuth = float(az_deg)
    pts[0].vRel = float(adj_car.speed - env.vehicle.speed)
    pts[0].snrDb = 24.0
    pts[0].isStatic = False
    pts[0].corner = 0
    # Static clutter return at a separate point (20m ahead, 4m opposite side)
    static_d, static_y = 20.0, (-4.0 if y_rel > 0 else 4.0)
    pts[1].rangM = float(math.hypot(static_d, static_y))
    pts[1].azimuth = float(math.degrees(math.atan2(static_y, static_d)))
    pts[1].vRel = float(-env.vehicle.speed)
    pts[1].snrDb = 18.0
    pts[1].isStatic = True
    pts[1].corner = 1
    objs = r4d.init("objects", 1)
    objs[0].trackId = 501
    objs[0].rangM = float(rng)
    objs[0].azimuth = float(az_deg)
    objs[0].vRel = float(adj_car.speed - env.vehicle.speed)
    objs[0].existenceProb = 92.0
    objs[0].corner = 0

    class _R4DHost:
      _fuse_radar4d = GridD._fuse_radar4d

    host = _R4DHost()
    host._active_costmap = FusionCostmap()
    fused_r4d = host._fuse_radar4d(r4d, [])
    assert len(fused_r4d) == 1 and abs(fused_r4d[0]["dRel"] - d_rel) < 0.2 and abs(fused_r4d[0]["yRel"] - y_rel) < 0.2
    cost_mov = host._active_costmap.get_cost_at(d_rel, y_rel)
    cost_sta = host._active_costmap.get_cost_at(static_d, static_y)
    assert cost_mov >= int(round(0.9 * COST_OBSTACLE)) and cost_mov > cost_sta > COST_ROAD

    # 3. 02M DlonMode (Highway/Urban/Dynamic) verification in MetaDrive physics
    pedal = _PedalPI()
    prev_v = float(env.vehicle.speed)
    p_hwy = LongitudinalPlanner(_make_cp(), init_v=prev_v)
    p_hwy.dlon = DLON(get_bool=lambda k: False, get=lambda k: 0 if k == "DlonMode" else None)
    p_hwy.dlon.last_param_update = -1e9
    p_urb = LongitudinalPlanner(_make_cp(), init_v=prev_v)
    p_urb.dlon = DLON(get_bool=lambda k: False, get=lambda k: 1 if k == "DlonMode" else None)
    p_urb.dlon.last_param_update = -1e9
    p_dyn = LongitudinalPlanner(_make_cp(), init_v=prev_v)
    p_dyn.dlon = DLON(get_bool=lambda k: True, get=lambda k: 2 if k == "DlonMode" else None)
    p_dyn.dlon.last_param_update = -1e9
    hwy_e2e = urb_e2e = dyn_curve_e2e = False
    for _ in range(45):
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      steer, _, on_lane = _lane_center_steer(env)
      assert on_lane
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=65.0, a_ego=a_ego, road_kappa=1.0 / 35.0)
      p_hwy.update(sm)
      p_urb.update(sm)
      p_dyn.dlon._enter_ts -= DT_MDL
      p_dyn.update(sm)
      hwy_e2e |= bool(p_hwy.dlon_e2e_enabled)
      urb_e2e |= bool(p_urb.dlon_e2e_enabled)
      dyn_curve_e2e |= bool(p_dyn.dlon_e2e_enabled)
      env.step([steer, pedal.step(float(p_dyn.output_a_target), a_ego)])
    assert p_hwy.dlon.mode is DLONMode.CHILL and not hwy_e2e
    assert p_urb.dlon.mode is DLONMode.EXPERIMENTAL and urb_e2e
    assert p_dyn.dlon.mode is DLONMode.AUTO and dyn_curve_e2e
    print(
      f"METADRIVE_REPORT 02M 4CAM_RADAR4D_DLON: streams=5/5 r4d_dRel={d_rel:.1f}m r4d_cost_mov={cost_mov} r4d_cost_sta={cost_sta}",
      f"hwy_e2e={hwy_e2e} urb_e2e={urb_e2e} dyn_e2e={dyn_curve_e2e}",
    )
  finally:
    _clear_3d_vehicles(env, spawned)
    env.close()
