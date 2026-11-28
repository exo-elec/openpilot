"""Minimal regression reproducers for real EOP10/01M/02M product & test bugs found during simulation testing."""

import importlib
import subprocess
import sys
from pathlib import Path

import cereal.messaging as messaging

ROOT = Path(__file__).resolve().parents[2]


def test_regression_tools_sim_run_tests_imports_deleted_get_custom_weather():
  """EOP10/01M/02M bug: tools/sim/tests/run_tests.py:18,243 imports deleted symbol
  `_get_custom_weather` from tools/sim/lib/weather_controller.py, crashing the
  entire 65-test offline simulation suite on import.
  """
  proc = subprocess.run([sys.executable, str(ROOT / "tools/sim/tests/run_tests.py")],
                        capture_output=True, text=True, check=False)
  assert proc.returncode == 0, f"run_tests.py crashed:\n{proc.stderr}"


def test_regression_compute_fault_policy_test_wrong_root_depth():
  """EOP10/01M/02M bug: exopilot/system/manager/tests/test_compute_fault_policy.py:20
  computes ROOT one level too shallow -> doubles `exopilot/exopilot/...` -> FileNotFoundError.
  """
  importlib.import_module("exopilot.system.manager.tests.test_compute_fault_policy")


def test_regression_surface_quality_db_hardcodes_slash_data_crashing_planner():
  """EOP10/01M/02M BLOCKER on dev PC / simulation:
  selfdrive/controls/lib/surface_quality_db.py:196 hardcodes DB_PATH under /data/shared/exopilot/
  instead of using Paths.persist_root(). Instantiating LongitudinalPlanner(CP) on any dev PC
  without root-created writable /data crashes immediately with PermissionError: '/data'.
  """
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  from exopilot.tests.test_eop10_closed_loop import _make_cp
  LongitudinalPlanner(_make_cp(), init_v=15.0, init_a=0.0)


def test_regression_gridd_fuse_modeld_reads_deprecated_leads_instead_of_leadsV3():
  """EOP10/01M/02M REAL PERCEPTION BUG:
  selfdrive/modeld/fill_model_msg.py:119-121 populates modelV2.leadsV3 (LeadDataV3),
  never the deprecated modelV2.leads (LeadDataV2). Yet exopilot/selfdrive/gridd/occupancy.py:368-377
  iterates over `model_v2.leads` and reads `lead.status/x/y/v`, so GridD._fuse_modeld_detections()
  fuses ZERO leads from every real modelV2 message!
  """
  from exopilot.selfdrive.gridd.occupancy import GridD

  class _Host:
    _fuse_modeld_detections = GridD._fuse_modeld_detections

  mdl = messaging.new_message('modelV2').modelV2
  leads = mdl.init('leadsV3', 1)
  leads[0].prob = 0.95
  leads[0].x = [30.0]
  leads[0].y = [3.5]
  leads[0].v = [0.0]
  fused = _Host()._fuse_modeld_detections(mdl, None)
  assert len(fused) == 1, f"GridD._fuse_modeld_detections ignored real modelV2.leadsV3! fused={fused}"


def test_regression_longitudinal_planner_accepts_dict_sm(tmp_path, monkeypatch):
  """EOP10/01M/02M bug: selfdrive/controls/lib/longitudinal_planner.py:260,317,381,414
  accesses sm.valid/sm.updated directly on dict `sm`.
  """
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  from openpilot.selfdrive.controls.lib.surface_quality_db import SurfaceQualityDB
  from exopilot.tests.test_eop10_closed_loop import _make_cp, _make_sm
  monkeypatch.setattr(SurfaceQualityDB, 'DB_PATH', tmp_path / 'sq.db')
  planner = LongitudinalPlanner(_make_cp(), init_v=15.0, init_a=0.0)
  planner.update(dict(_make_sm(15.0)))


def test_regression_manager_boot_import_migrate_origin_params():
  """P0 BLOCKER (EOP10 + 01M + 02M — crashes manager on boot on all RK3588/RK3576 boards + sim):
  system/manager/manager.py:40 imports `migrate_origin_params` from
  `exopilot.selfdrive.controls.lib.feature_keys`, which was removed during rebase
  -> `ImportError: cannot import name 'migrate_origin_params'`.
  """
  import ast
  src = (ROOT / "system/manager/manager.py").read_text()
  tree = ast.parse(src)
  for node in ast.walk(tree):
    if isinstance(node, ast.ImportFrom) and node.module == "exopilot.selfdrive.controls.lib.feature_keys":
      mod = importlib.import_module(node.module)
      for alias in node.names:
        assert hasattr(mod, alias.name), (
          f"system/manager/manager.py:{node.lineno} imports missing symbol `{alias.name}` from `{node.module}`"
        )


def test_regression_modeld_submaster_omits_modelv2_disabling_desire_safety_gates():
  """MAJOR SAFETY BUG (EOP10 + 01M + 02M):
  selfdrive/modeld/modeld.py:620 reads `sm['modelV2'] if sm.valid.get('modelV2') else None`,
  but `modelV2` is NOT in modeld's SubMaster (`:468`) — modeld IS the publisher of `modelV2`!
  As a result `mdl_v2` is ALWAYS `None`, silently bypassing DesireHelper's DLAT lane-confidence
  gate, road-edge blocker, lane-width check, and gap evaluation on every drive.
  """
  from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper
  src = (ROOT / "selfdrive/modeld/modeld.py").read_text()
  # Verify DesireHelper itself fails open when model_v2=None vs blocking low-confidence lanes
  dh = DesireHelper()
  low_conf_model = messaging.new_message('modelV2').modelV2
  low_conf_model.laneLineProbs = [0.05, 0.05, 0.05, 0.05]
  assert dh._validate_lane_confidence(low_conf_model) is False
  assert dh._validate_lane_confidence(None) is True  # neutral 0.5 bypasses gate!
  # Check that modeld does not read modelV2 from its own SubMaster
  assert "sm['modelV2']" not in src and 'sm["modelV2"]' not in src, (
    "selfdrive/modeld/modeld.py:620 reads `sm['modelV2']` (not subscribed!) instead of `modelv2_send.modelV2`"
  )


def test_regression_simulated_car_tesla_can_fails_carstate_checksums():
  """MAJOR SIM BUG (EOP10 + 01M + 02M):
  tools/sim/lib/simulated_car.py packs Tesla CAN frames without computing Tesla checksums
  or counters (and sends 0x39d IBST_status instead of ESP_status, omits ESP_B, and sends
  0x129 on bus 0 instead of bus 2). EOP10's Tesla CANParser drops every frame, leaving
  CarState vEgoRaw == 0.0 m/s for a 72 km/h simulated vehicle!
  """
  import time
  from cereal import car
  from openpilot.system.socketd.vehicle.car.carstate import CarState
  from openpilot.system.socketd.vehicle.protocol import Bus
  from openpilot.system.socketd.vehicle.tesla.values import CANBUS
  from openpilot.tools.sim.lib.simulated_car import SimulatedCar

  CP = car.CarParams.new_message()
  CP.carFingerprint = "TESLA_MODEL_3"
  cs_obj = CarState(CP)
  sc = SimulatedCar()
  try:
    msgs_party = [
      (0x257, sc._pack_speed(72.0), CANBUS.party),
      (0x118, sc._pack_system_status(gear=4, accel_pedal=0.0), CANBUS.party),
      (0x370, sc._pack_epas(12.5, 0.0), CANBUS.party),
      (0x286, sc._pack_di_state(cruise_state=2, digital_speed=72.0), CANBUS.party),
      (0x311, sc._pack_ui_warning(True, False, seatbelt=True), CANBUS.party),
    ]
    now_ns = time.monotonic_ns()
    for _ in range(3):
      cs_obj.can_parsers[Bus.party].update([(now_ns, msgs_party)])
      ret = cs_obj.decode(cs_obj.can_parsers)
    assert abs(ret.vEgoRaw - 20.0) < 0.5, (
      f"Tesla CANParser rejected SimulatedCar frames (missing checksums): vEgoRaw={ret.vEgoRaw:.2f}m/s, expected 20.0m/s"
    )
  finally:
    sc.close()


def test_regression_rknn_driving_runner_unconditional_desire_rename_for_onnx(tmp_path):
  """MINOR/MAJOR ONNX COMPAT BUG (EOP10 + 01M + 02M):
  selfdrive/modeld/runners/rknn_driving_runner.py:142-143 unconditionally renames
  `desire` -> `desire_pulse` whenever backend == ONNX instead of inspecting the ONNX
  model's actual input names, breaking v0.10.0 ONNX driving_policy models whose input
  is named `desire`.
  """
  import numpy as np
  import pytest
  onnx = pytest.importorskip("onnx")
  pytest.importorskip("onnxruntime")
  from openpilot.system.inferenced import InferenceClient
  from openpilot.selfdrive.modeld.runners.driving_runner import DrivingModelSpec
  from openpilot.selfdrive.modeld.runners.rknn_driving_runner import RKNNDrivingRunner

  vis_path, pol_path = tmp_path / "vis.onnx", tmp_path / "pol.onnx"
  def _save_id(path, in_name, shape):
    ti = onnx.helper.make_tensor_value_info(in_name, onnx.TensorProto.FLOAT, list(shape))
    to = onnx.helper.make_tensor_value_info("outputs", onnx.TensorProto.FLOAT, list(shape))
    g = onnx.helper.make_graph([onnx.helper.make_node("Identity", [in_name], ["outputs"])], "id", [ti], [to])
    onnx.save(onnx.helper.make_model(g, opset_imports=[onnx.helper.make_opsetid("", 17)]), path)

  _save_id(vis_path, "img", (1, 4))
  _save_id(pol_path, "desire", (1, 25, 8))

  client = InferenceClient("modeld")
  backend = client.inference_backend()
  assert backend.backend_type.name == "ONNX"
  runner = RKNNDrivingRunner(
    DrivingModelSpec("vis", vis_path, {"img": (1, 4)}, {"outputs": (1, 4)}),
    DrivingModelSpec("pol", pol_path, {"desire": (1, 25, 8)}, {"outputs": (1, 25, 8)}),
    client=client,
    backend=backend,
  )
  runner.load()
  res = runner.run_policy({"desire": np.zeros((1, 25, 8), dtype=np.float32)})
  assert res.success, f"RKNNDrivingRunner renamed 'desire' -> 'desire_pulse' for ONNX model expecting 'desire': {res.error_message}"


def test_regression_aeb_receding_lead_during_precharge_aborts_to_idle():
  """Scrutinize BLOCKER regression: a lead that accelerates away during PRECHARGE must abort to IDLE,
  never escalate into -3.0 m/s^2 emergency braking."""
  from openpilot.selfdrive.controls.lib.aeb import AEB, AEBState, EgoState, TrackedObject

  aeb = AEB()
  ego = EgoState(v_ego=12.0, a_ego=0.0, brake_pressed=False, throttle_pressed=False, steering_torque=0.0)
  closing = TrackedObject(track_id=1, x=14.0, y=0.0, v_x=2.0, v_y=0.0, width=1.8, length=4.5, obj_type='car', confidence=0.95)
  for _ in range(4):
    t = aeb._confirmed_entry_threat(closing, ego)
    res = aeb.braking.update(ego, t, 0.05)
  assert res.state is AEBState.PRECHARGE

  # Lead suddenly accelerates away faster than ego (closing_speed = -6 m/s, ttc = inf): must abort cleanly to IDLE.
  receding = TrackedObject(track_id=1, x=18.0, y=0.0, v_x=18.0, v_y=0.0, width=1.8, length=4.5, obj_type='car', confidence=0.95)
  t_rec = aeb._confirmed_entry_threat(receding, ego)
  assert t_rec is None, "Active AEB kept tracking a receding lead"
  res_after = aeb.braking.update(ego, t_rec, 0.05)
  assert res_after.state is AEBState.IDLE and res_after.target_decel == 0.0

  # Direct BrakingController safety check: _state_precharge with receding threat also aborts to IDLE.
  aeb.braking.state = AEBState.PRECHARGE
  aeb.braking._precharge_elapsed_s = 1.0
  res_direct = aeb.braking.update(ego, receding, 0.05)
  assert res_direct.state is AEBState.IDLE and res_direct.target_decel == 0.0


def test_regression_gridd_fuse_modeld_converts_leadsV3_absolute_v_to_vRel():
  """Scrutinize BLOCKER regression: modelV2.leadsV3.v[0] is absolute velocity (vLead);
  GridD._fuse_modeld_detections must subtract modelV2.velocity.x[0] to publish true relative vRel."""
  from exopilot.selfdrive.gridd.occupancy import GridD

  class _Host:
    _fuse_modeld_detections = GridD._fuse_modeld_detections

  mdl = messaging.new_message('modelV2').modelV2
  mdl.velocity.x = [25.0]
  leads = mdl.init('leadsV3', 1)
  leads[0].prob = 0.95
  leads[0].x = [30.0]
  leads[0].y = [1.5]
  leads[0].v = [20.0]  # absolute vLead = 20 m/s while ego = 25 m/s -> closing at -5 m/s
  fused = _Host()._fuse_modeld_detections(mdl, None)
  assert len(fused) == 1
  assert abs(fused[0]['vRel'] - (-5.0)) < 1e-3, f"Expected vRel=-5.0 m/s, got {fused[0]['vRel']}"
  assert abs(fused[0]['yRel'] - (-1.5)) < 1e-3


def test_regression_rga_cvtcolor_packed_nv12_and_3d_uv():
  """Scrutinize regression: RGABackend._cvtcolor must handle packed 2D NV12 src and 3D (h/2, w/2, 2) src_uv."""
  import numpy as np
  from openpilot.system.inferenced.rockchip_rga import RGABackend

  rga = RGABackend()
  packed_nv12 = np.full((24, 16), 128, dtype=np.uint8)
  rgb_packed = rga._cvtcolor({'src': packed_nv12, 'src_fmt': 'nv12', 'dst_fmt': 'rgb'})
  assert rgb_packed.shape == (16, 16, 3)

  y = np.full((16, 16), 128, dtype=np.uint8)
  uv_3d = np.full((8, 8, 2), 128, dtype=np.uint8)
  rgb_planar = rga._cvtcolor({'src_y': y, 'src_uv': uv_3d, 'src_fmt': 'nv12', 'dst_fmt': 'rgb'})
  assert rgb_planar.shape == (16, 16, 3)
