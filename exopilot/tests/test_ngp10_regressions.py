"""Minimal regression reproducers for real NGP10 product/test bugs found during simulation testing."""

from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.test.longitudinal_maneuvers.plant import Plant

from exopilot.selfdrive.controls.lib.map_speed import MapSpeed
from exopilot.tests.test_ngp10_closed_loop import DictParams, _make_cp, _make_sm


def test_regression_longitudinal_planner_accepts_dict_sm_from_plant():
  """NGP10 regression: selfdrive/controls/lib/longitudinal_planner.py:184,258,271,315
  accesses `sm.valid` / `sm.alive` unconditionally, crashing upstream `Plant`
  (`selfdrive/test/longitudinal_maneuvers/plant.py:137`) and breaking all 62
  `test_longitudinal.py` subtests + all 18 `test_following_distance.py` tests.
  """
  plant = Plant(lead_relevancy=False, speed=15.0, distance_lead=50.0)
  plant.step(15.0, 0.0, 20.0, 0.0, 1.0)


def test_regression_adaptive_accel_test_imports_cleanly():
  """NGP10 regression: selfdrive/controls/tests/test_longitudinal_planner_adaptive_accel.py:1
  imports deleted symbol `ADAPTIVE_ACCEL_CITY_SPEED_LIMIT` from longitudinal_planner.py
  (moved to exopilot.selfdrive.controls.lib.longitudinal_policy), failing pytest collection.
  """
  import importlib
  importlib.import_module("selfdrive.controls.tests.test_longitudinal_planner_adaptive_accel")


def test_regression_tlsc_closed_loop_fails_to_stop_and_runs_red_light():
  """CRITICAL SAFETY BUG (NGP10 + EOP10 + 01M + 02M):
  exopilot/selfdrive/controls/lib/tlsc.py:54-66 sets TLSC_MIN_DIST = 3.0m and
  v_target = sqrt(2 * 1.5 * d). At d = 3.0m, v_target is still 3.0 m/s (10.8 km/h);
  once the vehicle moves closer than 3.0m, TLSC ignores the red light (returns None),
  MapSpeed expires its 0.5s hold, and LongitudinalPlanner accelerates back to cruise
  speed straight through the red light!
  """
  now = [0.0]
  params = DictParams({'TLSCEnabled': True})
  planner = LongitudinalPlanner(_make_cp(), init_v=14.0, init_a=0.0, dt=DT_MDL)
  planner.map_speed = MapSpeed(params.get_bool, params.get, clock=lambda: now[0])

  v_ego, a_ego, x_ego = 14.0, 0.0, 0.0
  stop_line_x = 55.0
  for _ in range(160):  # 8 seconds
    now[0] += DT_MDL
    dist = max(0.5, stop_line_x - x_ego)
    sm = _make_sm(v_ego=v_ego, a_ego=a_ego, v_cruise_kph=60.0, traffic_light=('red', 0.92, dist))
    planner.update(sm)
    a_ego = 0.65 * a_ego + 0.35 * planner.output_a_target
    v_ego = max(0.0, v_ego + a_ego * DT_MDL)
    x_ego += v_ego * DT_MDL

  # Expected safe behavior: vehicle must stop before the red light (x_ego <= stop_line_x, v_ego < 0.5 m/s).
  # Actual buggy behavior: vehicle crosses stop_line_x (x_ego ~ 95m) and accelerates back up to > 10 m/s!
  assert x_ego <= stop_line_x + 1.0 and v_ego < 0.5, (
    f"TLSC RAN THE RED LIGHT: x_ego={x_ego:.1f}m (stop line {stop_line_x:.1f}m), v_ego={v_ego:.2f}m/s"
  )


def test_regression_tinygrad_float16_output_crashes_fill_model_msg(tmp_path):
  """P0 BLOCKER (NGP10 — real Comma 3 hardware + MetaDrive sim):
  Both driving_vision.onnx and driving_policy.onnx output TensorProto.FLOAT16.
  exopilot/selfdrive/modeld/tinygrad_model.py:59 returns raw float16 numpy arrays
  instead of casting to float32, crashing modeld on frame #1 at
  selfdrive/modeld/fill_model_msg.py:47 (`np.polynomial.polynomial.polyfit` ->
  `TypeError: array type float16 is unsupported in linalg`).
  """
  import os
  import subprocess
  import sys
  from pathlib import Path
  import numpy as np
  import pytest
  import cereal.messaging as messaging
  from openpilot.selfdrive.modeld.constants import ModelConstants
  from openpilot.selfdrive.modeld.fill_model_msg import fill_xyz_poly
  from exopilot.selfdrive.modeld.tinygrad_model import TinygradModel

  onnx = pytest.importorskip("onnx")
  tinygrad = pytest.importorskip("tinygrad")
  compiler = Path(tinygrad.__file__).resolve().parents[1] / "examples/openpilot/compile_onnx.py"
  src, dst = tmp_path / "fp16.onnx", tmp_path / "fp16.pkl"
  t_in = onnx.helper.make_tensor_value_info("input", onnx.TensorProto.FLOAT16, [1, ModelConstants.IDX_N, 3])
  t_out = onnx.helper.make_tensor_value_info("outputs", onnx.TensorProto.FLOAT16, [1, ModelConstants.IDX_N, 3])
  graph = onnx.helper.make_graph([onnx.helper.make_node("Identity", ["input"], ["outputs"])], "fp16_id", [t_in], [t_out])
  onnx.save(onnx.helper.make_model(graph, opset_imports=[onnx.helper.make_opsetid("", 17)]), src)
  root = str(compiler.parents[2])
  pythonpath = os.pathsep.join(p for p in (root, os.environ.get("PYTHONPATH", "")) if p)
  subprocess.run([sys.executable, str(compiler), str(src), str(dst)],
                 env={**os.environ, "DEV": "CPU:CLANG", "PYTHONPATH": pythonpath}, check=True, capture_output=True)

  model = TinygradModel(dst, device="CPU:CLANG")
  pts = np.zeros((1, ModelConstants.IDX_N, 3), dtype=np.float16)
  pts[0, :, 0] = np.linspace(0, 50, ModelConstants.IDX_N, dtype=np.float16)
  out = model(input=pts)
  # fill_xyz_poly crashes with `TypeError: array type float16 is unsupported in linalg`
  builder = messaging.new_message("drivingModelData").drivingModelData.path
  fill_xyz_poly(builder, ModelConstants.POLY_PATH_DEGREE, out[0, :, 0], out[0, :, 1], out[0, :, 2])
  assert out.dtype == np.float32, f"TinygradModel returned {out.dtype} instead of float32"


def test_regression_sim_sensors_driver_monitoring_collides_with_driveractivityd():
  """MAJOR SIM BUG (NGP10):
  tools/sim/lib/simulated_sensors.py:16,92 publishes `driverMonitoringState`, while
  system/manager/process_config.py:127 runs `driveractivityd` (which also publishes
  `driverMonitoringState`) and tools/sim/launch_openpilot.sh:9 does not block it.
  In MetaDrive sim this immediately crashes the bridge with
  `msgq.ipc_pyx.MultiplePublishersError: Messaging failure : Address already in use`.
  """
  from pathlib import Path
  root = Path(__file__).resolve().parents[2]
  sim_sensors = (root / "tools/sim/lib/simulated_sensors.py").read_text()
  launch_sh = (root / "tools/sim/launch_openpilot.sh").read_text()
  proc_cfg = (root / "system/manager/process_config.py").read_text()

  has_driveractivityd = '"driveractivityd"' in proc_cfg
  blocked_in_launch = "driveractivityd" in launch_sh
  sim_publishes_dmon = "'driverMonitoringState'" in sim_sensors or '"driverMonitoringState"' in sim_sensors
  assert not (has_driveractivityd and not blocked_in_launch and sim_publishes_dmon), (
    "Conflict: both driveractivityd (unblocked in launch_openpilot.sh) and SimulatedSensors publish driverMonitoringState"
  )
