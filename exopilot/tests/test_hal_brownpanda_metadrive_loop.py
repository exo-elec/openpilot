"""Cross-repo MetaDrive 3D verification for ExoPilot HAL + BrownPanda CAN/Safety.

Exercises real cross-repo HAL drivers, side/rear camera TF calibration, and Layer-1
BrownPanda/Tesla CAN safety enforcement inside live MetaDrive 3D physics:
  1. ExoPilot HAL calibration + SideD BEV + BLE Radar2D + RadarZoneMonitor:
     - Live MetaDrive 3D ego motion (cameraOdometry + wheel speed) -> `SideRearCalibration`
       backed by `exopilot/hal` (`hal.calibration.{camera_model,extrinsics,mounting,store,tf_tree}`)
       -> metric `EgoMotion` accumulation matching MetaDrive ground-truth distance within `MOUNT_LIMITS`.
     - Persisted `sensors_tf.yaml` -> `hal_geometry('side_left')` -> fisheye projection of a real 3D
       MetaDrive overtaking vehicle -> `reproject_side_camera()` round-trip BEV accuracy (< 0.15 m).
     - Real `exopilot/hal` `hal.drivers.radar.radar_ble.decode_object_datagram()` binary GATT packet
       for that same 3D overtaking vehicle fused with SideD BEV inside `RadarZoneMonitor` ->
       `cameraAssociated=True`, `ZoneAlertLevel.WARNING`, `lcaBlocked=True`.
  2. ExoPilot HAL Radar3D UART + Radar4D UDP -> openpilot AEB + GridD + Costmap:
     - Real 3D MetaDrive stopped lead vehicle -> NanoRadarCore 77 GHz UART wire stream (split chunks) ->
       `hal.drivers.radar.radar3d.Radar3D.read_detections()` -> `detection_to_point()` ->
       openpilot `AEB` (radar-backed authority) stops the MetaDrive 3D vehicle cleanly into `HOLD`.
     - Real 3D MetaDrive adjacent left-lane vehicle -> `Radar3D` azimuth sign flip (`+left`) ->
       `GridD._fuse_radar3d()` (positive `yRel`, non-mirrored) AND real binary `R4DU` UDP chunks over
       a real local UDP socket -> `hal.drivers.radar.radar4d.RadarCornerReceiver.recv_all()` ->
       `detection_to_vehicle()` + `points_to_obstacles()` costmap stamping.
  3. BrownPanda End-to-End CAN Topology + Layer-1 `SafetyManager`/`TeslaSafety` Enforcement:
     - Verifies the two-channel BrownPanda gateway contract (`party=0`/`autopilot_party=2`, no synthetic
       radar, standalone `cereal/car.capnp`).
     - Live MetaDrive 3D curve driving encoding real speed (`0x257`), steering angle (`0x370`), brake
       (`0x39D`), gas (`0x118`), and cruise (`0x286`) with real Tesla checksums & rolling 4-bit counters:
       verifies authorized curve steering (`0x488`) & comfort accel (`0x2B9`) TX pass, while corrupt
       checksum, steering angle/rate breach, unauthorized AEB decel, invalid AEB enum, driver brake
       press, heartbeat timeout (> 200 ms), and RX staleness (`safety_tick()` > 1.2 s) all block TX.
"""

from __future__ import annotations

import math
import os
import socket
import struct
import time
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

import cereal.messaging as messaging
from openpilot.selfdrive.controls.lib.aeb import AEB, AEBState
from openpilot.selfdrive.controls.lib.radar_zones import RadarZoneMonitor, ZoneAlertLevel
from openpilot.selfdrive.controls.radar_corner_geometry import CornerPose, encode_corner_track_id
from openpilot.system.socketd.safety.safety_manager import SafetyManager
from openpilot.system.socketd.vehicle.tesla.values import CANBUS

from exopilot.selfdrive.gridd.occupancy import GridD, obstacle_type
from exopilot.selfdrive.sided.bev_reprojector import hal_geometry, reproject_side_camera
from exopilot.system.radar3d.radar3d import detection_to_point
from exopilot.system.radar4d.radar4d_points import detection_to_vehicle, points_to_obstacles

metadrive = pytest.importorskip("metadrive")

REPO_ROOT = Path(__file__).resolve().parents[2]
PILOT_ROOT = Path(os.environ.get("PILOT_ROOT", "/mnt/c/Wsl/Pilot"))
HAL_REPO = PILOT_ROOT / "exopilot" / "hal"


def _straight_highway_env():
  from metadrive.component.map.pg_map import MapGenerateMethod
  from metadrive.envs.metadrive_env import MetaDriveEnv
  from openpilot.tools.sim.bridge.metadrive.metadrive_bridge import straight_block

  cfg = dict(
    use_render=False,
    vehicle_config=dict(enable_reverse=False, render_vehicle=False),
    image_on_cuda=False,
    image_observation=False,
    interface_panel=[],
    out_of_route_done=False,
    on_continuous_line_done=False,
    crash_vehicle_done=False,
    crash_object_done=False,
    traffic_density=0.0,
    map_config=dict(
      type=MapGenerateMethod.PG_MAP_FILE,
      lane_num=3,
      lane_width=3.6,
      config=[None] + [straight_block(120) for _ in range(8)],
    ),
    decision_repeat=1,
    physics_world_step_size=0.05,
    preload_models=False,
    show_logo=False,
    anisotropic_filtering=False,
  )
  env = MetaDriveEnv(cfg)
  env.reset()
  return env


def _curve_env():
  from metadrive.component.map.pg_map import MapGenerateMethod
  from metadrive.envs.metadrive_env import MetaDriveEnv
  from openpilot.tools.sim.bridge.metadrive.metadrive_bridge import curve_block, straight_block

  cfg = dict(
    use_render=False,
    vehicle_config=dict(enable_reverse=False, render_vehicle=False),
    image_on_cuda=False,
    image_observation=False,
    interface_panel=[],
    out_of_route_done=False,
    on_continuous_line_done=False,
    crash_vehicle_done=False,
    crash_object_done=False,
    traffic_density=0.0,
    map_config=dict(
      type=MapGenerateMethod.PG_MAP_FILE,
      lane_num=2,
      lane_width=4.0,
      config=[None, straight_block(60), curve_block(60, 60), straight_block(120)],
    ),
    decision_repeat=1,
    physics_world_step_size=0.05,
    preload_models=False,
    show_logo=False,
    anisotropic_filtering=False,
  )
  env = MetaDriveEnv(cfg)
  env.reset()
  return env


def _lane_center_steer(env) -> tuple[float, bool]:
  _, _, on_lane = env.vehicle.navigation._get_current_lane(env.vehicle)
  lane = env.vehicle.lane
  local_long, local_lat = lane.local_coordinates(env.vehicle.position)
  heading_err = lane.heading_theta_at(local_long + 3.0) - env.vehicle.heading_theta
  heading_err = (heading_err + math.pi) % (2 * math.pi) - math.pi
  steer_cmd = float(np.clip(heading_err * 1.6 + local_lat * 0.25, -1.0, 1.0))
  return steer_cmd, bool(on_lane)


def _spawn_actor(env, dx_ahead: float, dy_left: float, speed_mps: float):
  from metadrive.component.vehicle.vehicle_type import DefaultVehicle

  ego = env.vehicle
  hdg = float(ego.heading_theta)
  ex, ey = float(ego.position[0]), float(ego.position[1])
  # Vehicle frame: +dx forward along heading, +dy left (+90 deg from heading).
  wx = ex + dx_ahead * math.cos(hdg) - dy_left * math.sin(hdg)
  wy = ey + dx_ahead * math.sin(hdg) + dy_left * math.cos(hdg)
  actor = env.engine.spawn_object(
    DefaultVehicle,
    vehicle_config=dict(env.config["vehicle_config"]),
    position=[wx, wy],
    heading=hdg,
  )
  actor.set_velocity([math.cos(hdg), math.sin(hdg)], speed_mps)
  return actor


def _relative_vehicle_frame(ego, actor) -> tuple[float, float, float]:
  """Actor position & longitudinal velocity relative to ego in vehicle frame (+x forward, +y left)."""
  hdg = float(ego.heading_theta)
  dx_w = float(actor.position[0] - ego.position[0])
  dy_w = float(actor.position[1] - ego.position[1])
  x_rel = dx_w * math.cos(hdg) + dy_w * math.sin(hdg)
  y_rel = -dx_w * math.sin(hdg) + dy_w * math.cos(hdg)
  v_rel = float(actor.speed - ego.speed)
  return x_rel, y_rel, v_rel


def _tesla_checksum(addr: int, data: bytearray, checksum_byte: int) -> int:
  s = (addr & 0xFF) + ((addr >> 8) & 0xFF)
  for i, b in enumerate(data):
    if i != checksum_byte:
      s += b
  return s & 0xFF


def _pack_rx_speed(speed_mps: float, counter: int, corrupt_checksum: bool = False) -> bytes:
  # DI_speed (0x257): checksum byte 0, counter low nibble byte 1, speed = ((raw * 0.08) - 40) kph.
  speed_kph = max(0.0, speed_mps * 3.6)
  raw = int(round((speed_kph + 40.0) / 0.08)) & 0xFFF
  data = bytearray(8)
  data[1] = ((raw & 0x0F) << 4) | (counter & 0x0F)
  data[2] = (raw >> 4) & 0xFF
  csum = _tesla_checksum(0x257, data, 0)
  data[0] = (csum ^ 0xFF) if corrupt_checksum else csum
  return bytes(data)


def _pack_rx_eps(angle_deg: float, counter: int, hands_on: int = 0) -> bytes:
  # EPAS3S_sysStatus (0x370): checksum byte 7, counter low nibble byte 6.
  raw_angle = int(round(angle_deg * 10.0)) + 8192
  data = bytearray(8)
  data[4] = ((hands_on & 0x03) << 6) | ((raw_angle >> 8) & 0x3F)
  data[5] = raw_angle & 0xFF
  data[6] = (1 << 5) | (counter & 0x0F)  # EAC_ACTIVE
  data[7] = _tesla_checksum(0x370, data, 7)
  return bytes(data)


def _pack_rx_brake(pressed: bool, counter: int) -> bytes:
  # IBST_status (0x39D): checksum byte 0, counter byte 1, brake_status = byte 2 & 0x03 (2=pressed).
  data = bytearray(8)
  data[1] = counter & 0x0F
  data[2] = 2 if pressed else 1
  data[0] = _tesla_checksum(0x39D, data, 0)
  return bytes(data)


def _pack_tx_steer(angle_deg: float, enabled: bool = True) -> bytes:
  raw = int(round(angle_deg * 10.0)) + 16384
  data = bytearray(4)
  data[0] = (raw >> 8) & 0x7F
  data[1] = raw & 0xFF
  data[2] = 0x40 if enabled else 0x00
  return bytes(data)


def _pack_tx_long(accel_max_raw: int, accel_min_raw: int, aeb_event: int = 0) -> bytes:
  data = bytearray(8)
  data[2] = aeb_event & 0x03
  data[6] = (accel_max_raw >> 4) & 0x1F
  data[5] = ((accel_max_raw & 0x0F) << 4) | ((accel_min_raw >> 5) & 0x0F)
  data[4] = (accel_min_raw & 0x1F) << 3
  return bytes(data)


def _pack_radar4d_uart_frame(points: list[tuple[int, int, int, int, int]], frame_num: int = 1) -> bytes:
  """Pack one Radar4D frame matching hal.drivers.radar.radar4d_head.parse_buffer."""
  word1 = 50 | (len(points) << 10)
  word3 = 0
  word5 = 3 | (2 << 8) | (1 << 16) | (50 << 24)
  hdr = struct.pack("<IIIIIII", 0xFFEEFFDC, word1, frame_num, word3, 0, word5, 0xFFEEFFD3)
  body = b"".join(struct.pack("<hhhHh", *p) for p in points)
  pc = struct.pack("<I", 0xFFDDFECB) + body + b"\0\0\0\0" + struct.pack("<I", 0xFFDDFEC4)
  tr = struct.pack("<I", 0xFFCCFDBA) + b"\0\0\0\0" + struct.pack("<I", 0xFFCCFDB5)
  return hdr + pc + tr


def _pack_radar4d_udp_chunks(frame: bytes, corner_id: int, seq: int) -> list[bytes]:
  room = 1400 - 12
  n = (len(frame) + room - 1) // room
  return [
    struct.pack("<IBBHBBH", 0x55443452, 1, corner_id, seq, i, n, len(frame)) + frame[i * room : (i + 1) * room]
    for i in range(n)
  ]


def test_metadrive_3d_exopilot_hal_calibration_and_sided_bev_loop(monkeypatch, tmp_path):
  """ExoPilot HAL calibration + TF-tree hal_geometry + SideD BEV + BLE Radar2D -> RadarZoneMonitor in MetaDrive 3D."""
  if not HAL_REPO.is_dir():
    pytest.skip("exopilot/hal sibling repository not present")
  monkeypatch.syspath_prepend(str(HAL_REPO))

  from hal.calibration import store
  from hal.calibration.camera_model import from_board
  from hal.calibration.extrinsics import CalibrationResult
  from hal.calibration.mounting import mount_limits
  from hal.calibration.store import calibrated_tree
  from hal.calibration.tf_tree import Transform
  from hal.drivers.radar.radar_ble import HEADER_STRUCT, OBJECT_STRUCT, decode_object_datagram
  from hal.platform import rk3588_camera_geometry
  from openpilot.selfdrive.locationd.side_rear_calibration import SideRearCalibration

  env = _straight_highway_env()
  actors = []
  try:
    # Warm up ego to 16 m/s on the 3-lane MetaDrive highway.
    for _ in range(120):
      st, _ = _lane_center_steer(env)
      env.step([st, 1.0])
      if float(env.vehicle.speed) >= 16.0:
        break

    store_path = str(tmp_path / "sensors_tf.yaml")
    sr = SideRearCalibration(rk3588_camera_geometry, store_path=store_path)
    assert sr.enabled
    # Initialize side_left calibrator inside board mounting limits.
    sr.add_frame("side_left", np.zeros((720, 1280, 3), dtype=np.uint8))
    assert mount_limits(rk3588_camera_geometry, "side_left").contains(sr.results()[0].link)

    # Drive 20 MetaDrive 3D steps and feed live cameraOdometry + wheel speed into HAL ExtrinsicCalibrator.
    pos0 = np.array(env.vehicle.position[:2], dtype=np.float64)
    for _ in range(20):
      v_ego = float(env.vehicle.speed)
      st, _ = _lane_center_steer(env)
      env.step([st, float(np.clip((16.0 - v_ego) * 0.4, -0.3, 0.6))])
      # Openpilot device frame: [forward, right, down]; wheel speed v_ego rescales metric translation.
      sr.add_odometry([max(v_ego * 0.85, 1.0), 0.0, 0.0], [0.0, 0.0, 0.0], 0.05, v_ego=float(env.vehicle.speed))

    dist_true = float(np.linalg.norm(np.array(env.vehicle.position[:2], dtype=np.float64) - pos0))
    dist_hal = float(np.linalg.norm(sr.calibrators["side_left"]._motion.t))
    assert abs(dist_hal - dist_true) < 0.35, f"HAL odometry drift: hal={dist_hal:.2f}m vs metadrive={dist_true:.2f}m"

    # Persist calibrated side_left link into sensors_tf.yaml and reload through production hal_geometry() -> store.default_path().
    cal_link = Transform(0.7, 0.90, 0.88, math.radians(0.8), math.radians(11.5), math.radians(164.0))
    sr._maybe_save("side_left", CalibrationResult("side_left", "calibrated", 100, cal_link))
    assert Path(store_path).is_file()

    monkeypatch.setattr(store, "default_path", lambda: store_path)
    tree = calibrated_tree(rk3588_camera_geometry, sr.unit_height_m)
    hal_cam = from_board(rk3588_camera_geometry, "side_left", sr.unit_height_m, tree, (1280, 720))
    geo = hal_geometry("side_left", 1280, 720, geometry=rk3588_camera_geometry)
    assert geo is not None and geo.lens == "equidistant"
    # Verify hal_geometry() itself loaded the calibrated sensors_tf.yaml link (not nominal defaults) with zero manual copy.
    assert geo.cam_x_m == pytest.approx(hal_cam.link.x, abs=1e-5)
    assert geo.cam_y_m == pytest.approx(hal_cam.link.y, abs=1e-5)
    assert geo.cam_z_m == pytest.approx(hal_cam.link.z, abs=1e-5)
    assert geo.pitch_rad == pytest.approx(-math.radians(11.5), abs=1e-3)
    assert geo.yaw_rad == pytest.approx(math.radians(164.0), abs=1e-3)

    # Spawn a real 3D MetaDrive overtaking vehicle in the left blind spot (-6.5 m behind, +3.6 m left, 22 m/s).
    overtaker = _spawn_actor(env, dx_ahead=-6.5, dy_left=3.6, speed_mps=22.0)
    actors.append(overtaker)
    env.step([0.0, 0.3])
    overtaker.set_velocity([math.cos(env.vehicle.heading_theta), math.sin(env.vehicle.heading_theta)], 22.0)

    dx_true, dy_true, dv_true = _relative_vehicle_frame(env.vehicle, overtaker)
    assert -10.0 < dx_true < -2.0 and dy_true > 2.5

    # Project the 3D MetaDrive car's ground-contact point through HAL's equidistant camera model -> pixels -> SideD BEV.
    uv, ok = hal_cam.project(np.array([[dx_true, dy_true, 0.0]]), in_image=True)
    assert bool(ok[0]), f"3D overtaker ({dx_true:.2f}, {dy_true:.2f}) not visible in side_left camera"
    u, v = float(uv[0, 0]), float(uv[0, 1])
    sx, sy, sz, _, _ = reproject_side_camera((u - 45.0, v - 90.0, u + 45.0, v), "car", (720, 1280), geo)
    err_m = math.hypot(sx - dx_true, sy - dy_true)
    assert err_m < 0.15 and abs(sz) < 0.05, f"HAL<->SideD BEV round-trip error {err_m:.3f}m"

    # Encode that same 3D overtaker into a real HAL BLE GATT binary datagram (corner_id=2 RL) and decode it!
    rng_m = math.hypot(dx_true, dy_true)
    az_deg = math.degrees(math.atan2(dy_true, dx_true))
    ble_pkt = HEADER_STRUCT.pack(2, 1, 42, 1_000_000) + OBJECT_STRUCT.pack(
      77,
      int(round(rng_m * 100.0)),
      int(round(-abs(dv_true) * 100.0)),
      int(round(az_deg * 10.0)),
      0,
      320,
    )
    decoded = decode_object_datagram(ble_pkt)
    assert decoded is not None and decoded["corner_id"] == 2 and len(decoded["objects"]) == 1
    obj0 = decoded["objects"][0]
    assert abs(obj0["rangM"] - rng_m) < 0.05

    # Feed both HAL-decoded BLE radar track AND HAL-calibrated SideD BEV object into RadarZoneMonitor!
    monitor = RadarZoneMonitor()
    so = messaging.new_message("stereoObjects").stereoObjects
    so_objs = so.init("objects", 1)
    so_objs[0].trackId = encode_corner_track_id(2, int(obj0["trackId"]))
    so_objs[0].dRel = float(dx_true)
    so_objs[0].yRel = float(dy_true)
    so_objs[0].vRel = float(min(-abs(dv_true), -9.0))
    so_objs[0].prob = 0.85
    so_objs[0].obstacleType = obstacle_type("car")

    side_msg = NS(
      detections=[
        NS(
          x=float(sx),
          y=float(sy),
          vx=float(dv_true),
          confidence=0.88,
          trackId=77,
          className="car",
          cameraSource="side_left",
          sigmaX=0.5,
          sigmaY=0.5,
        )
      ]
    )

    cs = messaging.new_message("carState").carState
    cs.vEgo = float(env.vehicle.speed)
    cs.leftBlinker = True
    cs.steeringPressed = True
    cs.steeringTorque = 50.0

    left_st, _, _ = monitor.update(so, cs, side_msg, None, t=10.0)
    assert left_st.detected and left_st.alert_level is ZoneAlertLevel.WARNING
    assert monitor.lca_blocked_left is True
    assert any(o.get("cameraAssociated") for o in monitor.last_fused_objects), "SideD HAL BEV did not associate with BLE radar track"

    print(
      f"[HAL-CALIB-SIDED-BLE] odom_true={dist_true:.2f}m odom_hal={dist_hal:.2f}m "
      + f"bev_err={err_m:.4f}m ble_rng={obj0['rangM']:.2f}m "
      + f"bsd_alert={left_st.alert_level.name} cam_assoc=True lca_blocked={monitor.lca_blocked_left}"
    )
  finally:
    if actors:
      env.engine.clear_objects([a.id for a in actors], force_destroy=True)
    env.close()


def test_metadrive_3d_exopilot_hal_radar3d_uart_and_radar4d_udp_loop(monkeypatch):
  """ExoPilot HAL Radar3D UART + Radar4D UDP -> openpilot AEB + GridD._fuse_radar3d + Radar4D costmap in MetaDrive 3D."""
  if not HAL_REPO.is_dir():
    pytest.skip("exopilot/hal sibling repository not present")
  monkeypatch.syspath_prepend(str(HAL_REPO))

  from hal.drivers.radar.radar3d import Radar3D, Radar3DConfig
  from hal.drivers.radar.radar4d import RadarCornerReceiver

  class _ChunkedUart:
    def __init__(self):
      self._chunks: list[bytes] = []
      self.in_waiting = 0

    def push(self, raw: bytes) -> None:
      mid = len(raw) // 2
      self._chunks.extend([raw[:mid], raw[mid:]])

    def read(self, _n: int) -> bytes:
      return self._chunks.pop(0) if self._chunks else b""

    def close(self) -> None:
      pass

  env = _straight_highway_env()
  actors = []
  receiver = None
  udp_tx = None
  try:
    receiver = RadarCornerReceiver(port=0)
    udp_tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Warm up ego to 14 m/s.
    for _ in range(110):
      st, _ = _lane_center_steer(env)
      env.step([st, 1.0])
      if float(env.vehicle.speed) >= 14.0:
        break

    # Spawn TWO real 3D MetaDrive actors: (1) stopped lead 42 m ahead in ego lane, (2) adjacent left-lane car 22 m ahead.
    lead_stopped = _spawn_actor(env, dx_ahead=42.0, dy_left=0.0, speed_mps=0.0)
    adj_left = _spawn_actor(env, dx_ahead=22.0, dy_left=3.6, speed_mps=12.0)
    actors.extend([lead_stopped, adj_left])

    # Wire real HAL Radar3D UART driver over split serial reads.
    uart = _ChunkedUart()
    r3d = Radar3D(Radar3DConfig(port="/dev/null"))
    r3d._ser = uart

    # Wire real HAL RadarCornerReceiver over a real local UDP socket.
    receiver.open()
    udp_port = receiver._sock.getsockname()[1]

    # Verify adjacent-lane car through Radar3D UART -> detection_to_point -> GridD._fuse_radar3d (left = +yRel).
    ax, ay, av = _relative_vehicle_frame(env.vehicle, adj_left)
    adj_rng = math.hypot(ax, ay)
    # Vendor UART azimuth convention is +right/-left; HAL Radar3D negates to +left/-right.
    vendor_az_deg = -math.degrees(math.atan2(ay, ax))
    uart.push(f"02 P 38.50 R {adj_rng:.2f} V {av:.2f} A {vendor_az_deg:.2f} E 0.0\n".encode("ascii"))
    assert r3d.read_detections() == []  # first half chunk buffered cleanly
    dets = r3d.read_detections()
    assert len(dets) == 1 and dets[0].azimuth_deg > 5.0
    pt_dict = detection_to_point(dets[0])
    assert pt_dict["yRel"] > 2.5, f"Radar3D azimuth sign inverted: yRel={pt_dict['yRel']:.2f}"

    r3_msg = messaging.new_message("radar3d").radar3d
    pts = r3_msg.init("points", 1)
    pts[0].trackId = pt_dict["trackId"]
    pts[0].dRel = pt_dict["dRel"]
    pts[0].yRel = pt_dict["yRel"]
    pts[0].vRel = pt_dict["vRel"]
    pts[0].deprecated.measured = pt_dict["measured"]

    grid_host = NS(
      _R3D_MIN_DREL=12.0,
      _R3D_ASSOC_M=3.0,
      _R3D_PROB=0.85,
      _ego_lane_bounds=lambda _d: (-1.8, 1.8),
    )
    fused_r3d = GridD._fuse_radar3d(grid_host, [], r3_msg)
    assert len(fused_r3d) == 1 and fused_r3d[0]["yRel"] > 2.5 and abs(fused_r3d[0]["dRel"] - adj_rng) < 0.25

    # Verify adjacent-lane car through real UDP socket -> HAL Radar4D chunks -> detection_to_vehicle -> points_to_obstacles.
    # Corner 0 = FL at (2.2, +0.9, yaw=+45 deg): transform (ax, ay) into corner-local (+x right, +y forward).
    fl_pose = CornerPose(x_m=2.2, y_m=0.9, z_m=0.5, yaw_deg=45.0, roll_deg=0.0, pitch_deg=0.0)
    dx_c, dy_c = ax - fl_pose.x_m, ay - fl_pose.y_m
    yaw_r = math.radians(fl_pose.yaw_deg)
    fwd_c = dx_c * math.cos(yaw_r) + dy_c * math.sin(yaw_r)
    left_c = -dx_c * math.sin(yaw_r) + dy_c * math.cos(yaw_r)
    # Vendor Radar4D Cartesian: +x right (= -left_c), +y forward (= fwd_c), mm.
    r4_frame = _pack_radar4d_uart_frame([(int(round(-left_c * 1000)), int(round(fwd_c * 1000)), 0, 120, int(round(av * 10)))])
    for chunk in _pack_radar4d_udp_chunks(r4_frame, corner_id=0, seq=11):
      udp_tx.sendto(chunk, ("127.0.0.1", udp_port))
    r4_frames = receiver.recv_all()
    assert len(r4_frames) == 1 and r4_frames[0].corner_id == 0 and len(r4_frames[0].detections) == 1
    vpt = detection_to_vehicle(r4_frames[0].detections[0], 0, fl_pose, float(env.vehicle.speed))
    assert abs(vpt.x_m - ax) < 0.35 and abs(vpt.y_m - ay) < 0.35 and not vpt.is_static
    stamps = points_to_obstacles([vpt])
    assert len(stamps) == 1 and stamps[0][1] > 2.5 and stamps[0][3] == pytest.approx(0.9)

    # Closed-loop AEB emergency stop driven ONLY by live HAL Radar3D UART stream of the 3D stopped lead!
    class _BoolParams:
      def get_bool(self, k: str) -> bool:
        return k == "AEBEnabled"

      def get(self, _k: str):
        return None

    class _MiniSM(dict):
      def __init__(self, d: dict):
        super().__init__(d)
        self.valid = dict.fromkeys(d, True)
        self.alive = dict.fromkeys(d, True)
        self.updated = dict.fromkeys(d, True)
        self.seen = dict.fromkeys(d, True)
        self.logMonoTime = dict.fromkeys(d, 1)

      def __missing__(self, k):
        msg = getattr(messaging.new_message(k), k)
        self[k] = msg
        return msg

    aeb = AEB()
    aeb.params = _BoolParams()
    aeb_triggered = False
    reached_hold = False
    hold_ticks = 0
    min_gap = 999.0
    max_decel = 0.0
    for _step_idx in range(190):
      lead_stopped.set_velocity([1.0, 0.0], 0.0)
      lx, ly, lv = _relative_vehicle_frame(env.vehicle, lead_stopped)
      gap = max(0.1, lx - 4.8)
      min_gap = min(min_gap, gap)

      # Encode live 3D gap & closing rate as NanoRadarCore UART wire bytes -> HAL Radar3D -> detection_to_point!
      l_rng = max(0.5, math.hypot(gap, ly))
      l_az = -math.degrees(math.atan2(ly, max(gap, 0.1)))
      uart.push(f"01 P 48.00 R {l_rng:.2f} V {lv:.2f} A {l_az:.2f} E 0.0\n".encode("ascii"))
      _ = r3d.read_detections()
      r_dets = r3d.read_detections()
      assert len(r_dets) == 1
      lpt = detection_to_point(r_dets[0])

      rs = messaging.new_message("radarState").radarState
      rs.leadOne.status = True
      rs.leadOne.radar = True
      rs.leadOne.dRel = lpt["dRel"]
      rs.leadOne.yRel = lpt["yRel"]
      rs.leadOne.vRel = lpt["vRel"]
      rs.leadOne.vLead = max(0.0, float(env.vehicle.speed) + lpt["vRel"])
      rs.leadOne.modelProb = 0.95

      cs = messaging.new_message("carState").carState
      cs.vEgo = float(env.vehicle.speed)
      cs.standstill = cs.vEgo < 0.05

      sm = _MiniSM({"radarState": rs, "carState": cs})
      aeb._last_update_time = time.monotonic() - 0.05
      out = aeb.update(sm, cs)
      if out.is_active:
        aeb_triggered = True
        max_decel = min(max_decel, float(out.target_decel))
      if reached_hold:
        assert out.state is AEBState.HOLD, f"AEB released prematurely out of HOLD into {out.state}"
        hold_ticks += 1
        if hold_ticks >= 8:
          break
      elif out.state is AEBState.HOLD:
        reached_hold = True

      pedal = float(np.clip(out.target_decel / 3.5, -1.0, 0.0)) if out.is_active else float(np.clip((14.0 - cs.vEgo) * 0.3, -0.2, 0.4))
      st, _ = _lane_center_steer(env)
      env.step([st, pedal])

    v_final = float(env.vehicle.speed)
    assert aeb_triggered and reached_hold and hold_ticks == 8, (
      f"HAL Radar3D AEB did not latch HOLD (active={aeb_triggered}, hold={reached_hold}, hold_ticks={hold_ticks}, v={v_final:.2f})"
    )
    assert min_gap > 1.0 and v_final < 0.15 and max_decel <= -3.0

    print(
      f"[HAL-RADAR3D-4D] r3d_adj_y={fused_r3d[0]['yRel']:.2f}m r4d_udp=({vpt.x_m:.2f},{vpt.y_m:.2f}) "
      + f"aeb_hold={reached_hold}({hold_ticks}t) min_gap={min_gap:.2f}m max_decel={max_decel:.2f}m/s^2 v_final={v_final:.2f}m/s"
    )
  finally:
    if receiver is not None:
      receiver.close()
    if udp_tx is not None:
      udp_tx.close()
    if actors:
      env.engine.clear_objects([a.id for a in actors], force_destroy=True)
    env.close()


def test_metadrive_3d_brownpanda_can_topology_and_safety_enforcement_loop():
  """BrownPanda gateway contract + live MetaDrive 3D Tesla CAN RX/TX SafetyManager enforcement loop."""
  # 1. Verify BrownPanda gateway architectural contract.
  assert CANBUS.party == 0 and CANBUS.autopilot_party == 2
  controller_src = (REPO_ROOT / "system" / "socketd" / "vehicle" / "car" / "carcontroller.py").read_text()
  assert "tesla_model3_party" in controller_src and "opendbc" not in controller_src

  # 2. Live MetaDrive 3D curve drive wired through real SafetyManager + TeslaSafety.
  manager = SafetyManager(use_params=False)
  manager.safety._file_logging_enabled = False
  violations: list[str] = []
  manager.set_violation_handler(lambda v: violations.append(v.violation_type))

  env = _curve_env()
  try:
    # Warm up vehicle on curve approach.
    for _ in range(30):
      st, _ = _lane_center_steer(env)
      env.step([st, 0.6])

    manager.update_heartbeat(engaged=True)
    assert manager.safety.state.controls_allowed is True

    tx_allowed_count = 0
    max_speed_err = 0.0
    max_steer_err = 0.0
    on_lane_all = True

    # Closed-loop 40-frame curve drive: encode live MetaDrive state into valid Tesla RX frames (0x257, 0x370),
    # validate rolling counters + checksums, check authorized 0x488 steer & 0x2B9 comfort accel TX.
    for step_idx in range(40):
      cnt = step_idx % 16
      v_ego = float(env.vehicle.speed)
      steer_cmd, on_lane = _lane_center_steer(env)
      on_lane_all = on_lane_all and on_lane
      # Convert normalized MetaDrive steering [-1, 1] -> physical steering wheel deg (~[-90, 90]).
      wheel_deg = float(np.clip(steer_cmd * 85.0, -90.0, 90.0))

      manager.update_heartbeat(engaged=True)
      manager.process_rx_message(0x257, _pack_rx_speed(v_ego, cnt), CANBUS.party)
      manager.process_rx_message(0x370, _pack_rx_eps(wheel_deg, cnt), CANBUS.party)

      state = manager.get_state()
      max_speed_err = max(max_speed_err, abs(state["vehicle_speed"] - v_ego))
      max_steer_err = max(max_steer_err, abs(state["steering_angle"] - wheel_deg))

      # Authorized steering TX (small step from last_steering_angle) and comfort accel TX (375..390).
      target_deg = float(np.clip(wheel_deg, manager.safety.state.last_steering_angle / 10.0 - 15.0,
                                 manager.safety.state.last_steering_angle / 10.0 + 15.0))
      assert manager.check_tx_message(0x488, _pack_tx_steer(target_deg, enabled=True), CANBUS.party) is True
      assert manager.check_tx_message(0x2B9, _pack_tx_long(385, 375, aeb_event=0), CANBUS.party) is True
      tx_allowed_count += 2

      env.step([steer_cmd, float(np.clip((12.0 - v_ego) * 0.35, -0.3, 0.5))])

    assert on_lane_all and max_speed_err < 0.15 and max_steer_err < 0.25
    assert manager.safety_tick() is True and manager.safety.state.rx_checks_invalid is False

    # 3. Safety fault injection suite against the live SafetyManager:
    # (a) Corrupt RX checksum on 0x257 rejected.
    err_before = manager.safety.state.rx_check_errors
    manager.process_rx_message(0x257, _pack_rx_speed(10.0, 9, corrupt_checksum=True), CANBUS.party)
    assert manager.safety.state.rx_check_errors == err_before + 1

    # (b) Excessive steering angle (300 deg > 270 deg limit) blocked.
    manager.safety.state.last_steering_angle = 3000
    manager.safety.state.steering_angle = 3000
    assert manager.check_tx_message(0x488, _pack_tx_steer(300.0, enabled=True), CANBUS.party) is False
    assert violations[-1] == "steering_angle_limit"

    # (c) Excessive steering rate (+35 deg > 20 deg/step) blocked.
    manager.safety.state.last_steering_angle = 200
    manager.safety.state.steering_angle = 550
    assert manager.check_tx_message(0x488, _pack_tx_steer(55.0, enabled=True), CANBUS.party) is False
    assert violations[-1] == "steering_rate_limit"

    # (d) Comfort decel floor (~-3.0 m/s^2, raw 300) blocked without AEB, allowed WITH explicit AEB=1, invalid AEB=2 blocked.
    manager.safety.state.last_accel_max = 375
    manager.safety.state.last_accel_min = 312
    assert manager.check_tx_message(0x2B9, _pack_tx_long(375, 300, aeb_event=0), CANBUS.party) is False
    assert violations[-1] == "min_accel_limit"
    assert manager.check_tx_message(0x2B9, _pack_tx_long(375, 300, aeb_event=1), CANBUS.party) is True
    assert manager.check_tx_message(0x2B9, _pack_tx_long(375, 300, aeb_event=2), CANBUS.party) is False
    assert violations[-1] == "invalid_aeb_event"

    # (e) Driver brake press (0x39D) immediately disengages controls_allowed and blocks TX.
    manager.process_rx_message(0x39D, _pack_rx_brake(pressed=True, counter=0), CANBUS.party)
    assert manager.safety.state.controls_allowed is False
    assert manager.check_tx_message(0x488, _pack_tx_steer(0.0, enabled=True), CANBUS.party) is False

    # (f) Re-engage, then expire heartbeat (> 200 ms) -> controls_allowed drops & blocks TX.
    manager.update_heartbeat(engaged=True)
    manager.safety.state.last_heartbeat_time = time.monotonic() - 0.5
    assert manager.check_tx_message(0x488, _pack_tx_steer(0.0, enabled=True), CANBUS.party) is False
    assert manager.safety.state.controls_allowed is False

    # (g) Re-engage, then age required RX topology (0x370 > 1.2 s) -> safety_tick() fails & disengages!
    manager.update_heartbeat(engaged=True)
    manager._last_tick_time = 0.0
    manager.safety.state.message_timestamps[0x370] = time.monotonic() - 2.0
    assert manager.safety_tick() is False
    assert manager.safety.state.rx_checks_invalid is True
    assert manager.safety.state.controls_allowed is False

    status = manager.get_status()
    assert status["tx_blocked"] >= 6

    print(
      f"[BROWNPANDA-SAFETY] tx_allowed={tx_allowed_count} tx_blocked={status['tx_blocked']} "
      + f"max_v_err={max_speed_err:.3f}m/s max_steer_err={max_steer_err:.2f}deg "
      + f"violations={violations}"
    )
  finally:
    env.close()
