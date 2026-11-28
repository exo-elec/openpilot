"""Cross-repo MetaDrive 3D closed-loop verification for Big-Model + NavPilot + JetLink + PLP v5.

Exercises real cross-process and cross-repo wire protocols against live MetaDrive 3D camera frames
and vehicle physics:
  1. Real ONNX Big-Model (big_driving_vision.onnx + big_driving_policy.onnx) via server_ref.OnnxBackend
     dialing openpilot's Listener -> Client -> RemoteModelState -> Joining -> BigModelFacade(ONE_WAY)
     + BigModelGate + Parser in MetaDrive 3D:
       - Warm-up on local small model -> swap to remote phone big model -> prove past PROVING_FRAMES
         (BigModelLoading True -> False, BigModelActive True) -> live MetaDrive 3D curve drive (lane-keeper
         tracks the curve with bounded 0.04 blend from the live parsed ONNX (33, 15) plan, because MetaDrive
         supplies resized RGB rather than comma's exact calibrated OpenCL warp homography)
       - Mid-drive USB cable unplug fault injection -> immediate hand-back to local small model,
         BigModelGate emits bigModelFailed + soft-disable + 5s settling window, vehicle stays safely
         on-lane, and ONE_WAY policy refuses re-join onroad even if the server redials.
  2. Live NavPilot Dart VM NPBM server (`navpilot/packages/npbm/tool/serve_stub.dart dial`) over
     a real TCP loopback socket receiving live MetaDrive 3D warped camera frames, asserting bit-exact
     parity with Python `server_ref.step` and clean fallback when the Dart process dies mid-drive.
  3. Live NavPilot Dart VM PLP v5 client (`package:plp/plp_tcp.dart` `PlpTcpLink.connect`) dialing
     openpilot's real `plpd` (`CablePLP` + `LocateSink`) over real TCP, streaming live MetaDrive 3D
     GNSS fixes and curve `MapHorizon` payloads, feeding `HorizonTracker` -> `MTSC` curve slowdown
     in closed-loop MetaDrive 3D driving.
  4. Live `jetlink` v3 wire protocol (`jetlink.protocol` MAGIC='JLNK' v3, `TcpTransport`,
     `JetlinkClient`, `ModelSpec`) carrying live MetaDrive 3D warped `(2, 6, 128, 256)` uint8 frames
     over a real TCP socket pair, verifying `HELLO`, `RESET_QUEUES`, multi-frame inference + `drain()`,
     and clean `LEAVE_LOST` / `LinkError` on mid-drive link drop.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from openpilot.selfdrive.modeld.parse_model_outputs import Parser

from exopilot.selfdrive.controls.lib.mtsc import MTSC
from exopilot.selfdrive.modeld.bigmodel import metadata
from exopilot.selfdrive.modeld.bigmodel import protocol as P
from exopilot.selfdrive.modeld.bigmodel import server_ref as S
from exopilot.selfdrive.modeld.bigmodel.client import Client
from exopilot.selfdrive.modeld.bigmodel.face import make_action_function
from exopilot.selfdrive.modeld.bigmodel.gate import BigModelGate
from exopilot.selfdrive.modeld.bigmodel.hook import ONE_WAY, BigModelFacade, LocalModel
from exopilot.selfdrive.modeld.bigmodel.joining import SMALL_WARMUP_FRAMES, Joining
from exopilot.selfdrive.modeld.bigmodel.listener import Listener
from exopilot.selfdrive.modeld.bigmodel.remote_state import PROVING_FRAMES, RemoteModelState
from exopilot.system.plp import plpd
from exopilot.system.plp.locate import LocateSink

metadrive = pytest.importorskip("metadrive")

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "selfdrive" / "modeld" / "models"
PILOT_ROOT = Path(os.environ.get("PILOT_ROOT", "/mnt/c/Wsl/Pilot"))
NAVPILOT_NPBM = PILOT_ROOT / "navpilot" / "packages" / "npbm"
NAVPILOT_PLP = PILOT_ROOT / "navpilot" / "packages" / "plp"
JETLINK_REPO = PILOT_ROOT / "jetlink"
DART_BIN = shutil.which("dart") or (
  "/home/vcar/dev/flutter/bin/dart" if Path("/home/vcar/dev/flutter/bin/dart").is_file() else None
)


class _MemoryParams:
  """Thread-safe in-memory Params stand-in matching BigModelFacade & BigModelGate calls."""

  def __init__(self):
    self._d: dict[str, object] = {}

  def get(self, key: str, return_default: bool = False):
    return self._d.get(key)

  def get_bool(self, key: str) -> bool:
    return bool(self._d.get(key, False))

  def put_bool_nonblocking(self, key: str, val: bool) -> None:
    self._d[key] = bool(val)

  def remove(self, key: str) -> None:
    self._d.pop(key, None)


def _rgb_to_yuv6(rgb: np.ndarray) -> np.ndarray:
  """Convert MetaDrive HxWx3 RGB uint8 frame to openpilot's (6, 128, 256) uint8 YUV420 planar layout."""
  if rgb.dtype != np.uint8:
    rgb = np.clip(rgb * 255.0, 0, 255).astype(np.uint8)
  resized = cv2.resize(rgb, (512, 256), interpolation=cv2.INTER_AREA)
  yuv = cv2.cvtColor(resized, cv2.COLOR_RGB2YUV_I420)
  y = yuv[:256, :512]
  uv_flat = yuv[256:, :].reshape(-1)
  u = uv_flat[: 128 * 256].reshape(128, 256)
  v = uv_flat[128 * 256 : 2 * 128 * 256].reshape(128, 256)
  out = np.empty((6, 128, 256), dtype=np.uint8)
  out[0] = y[0::2, 0::2]
  out[1] = y[0::2, 1::2]
  out[2] = y[1::2, 0::2]
  out[3] = y[1::2, 1::2]
  out[4] = u
  out[5] = v
  return out


class _MetaDriveWarp:
  """ModelWarp-compatible adapter packing live MetaDrive road + wide camera frames into P.FRAME_BYTES."""

  def __init__(self):
    self.output = memoryview(bytearray(P.FRAME_BYTES))
    self._stacked = np.zeros(P.FRAME_SHAPE, dtype=np.uint8)
    self.frames_packed = 0

  def start_buffers(self, bufs, transforms) -> None:
    road_rgb = bufs["road"]
    wide_rgb = bufs["wide"]
    self._stacked[0] = _rgb_to_yuv6(road_rgb)
    self._stacked[1] = _rgb_to_yuv6(wide_rgb)
    self.output[:] = self._stacked.tobytes()
    self.frames_packed += 1

  def wait(self) -> None:
    pass


class _LocalOnnxSmallModel:
  """Always-warm on-board local driving model using real driving_vision/policy.onnx."""

  def __init__(self, backend: S.Backend, contract: metadata.Contract, parser: Parser, warp: _MetaDriveWarp):
    self.backend = backend
    self.contract = contract
    self.parser = parser
    self.warp = warp
    self.state = S.State()
    self.numpy_inputs: dict[str, np.ndarray] = {}
    self.calls = 0

  def reset_history(self) -> None:
    self.state.reset()

  def run(self, bufs, transforms, inputs, prepare_only=False):
    self.warp.start_buffers(bufs, transforms)
    self.warp.wait()
    if prepare_only:
      return None
    self.calls += 1
    frame = np.frombuffer(bytes(self.warp.output), np.uint8).reshape(P.FRAME_SHAPE)
    out = S.step(self.state, self.backend, inputs["desire"], inputs["traffic_convention"], frame)
    assert out is not None
    v = self.contract.vision_floats
    vision, policy = out[:v], out[v:]
    vd = self.parser.parse_vision_outputs({k: vision[np.newaxis, s] for k, s in self.contract.vision_slices.items()})
    pd = self.parser.parse_policy_outputs({k: policy[np.newaxis, s] for k, s in self.contract.policy_slices.items()})
    return {**vd, **pd}


def _dual_camera_curve_env():
  from metadrive.component.map.pg_map import MapGenerateMethod
  from metadrive.envs.metadrive_env import MetaDriveEnv
  from openpilot.tools.sim.bridge.metadrive.metadrive_bridge import curve_block, straight_block
  from openpilot.tools.sim.bridge.metadrive.metadrive_common import RGBCameraRoad, RGBCameraWide

  cfg = dict(
    use_render=False,
    vehicle_config=dict(enable_reverse=False, render_vehicle=False, image_source="rgb_road"),
    sensors={"rgb_road": (RGBCameraRoad, 640, 400), "rgb_wide": (RGBCameraWide, 640, 400)},
    image_on_cuda=False,
    image_observation=True,
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


def _capture_rgb_pair(env) -> dict[str, np.ndarray]:
  road = env.engine.get_sensor("rgb_road").perceive(to_float=False)
  wide = env.engine.get_sensor("rgb_wide").perceive(to_float=False)
  # MetaDrive returns BGRA/RGBA or RGB; drop alpha channel.
  road_rgb = np.ascontiguousarray(road[:, :, :3])
  wide_rgb = np.ascontiguousarray(wide[:, :, :3])
  return {"road": road_rgb, "wide": wide_rgb}


def _lane_steer_and_metrics(env) -> tuple[float, float, float, bool]:
  _, _, on_lane = env.vehicle.navigation._get_current_lane(env.vehicle)
  lane = env.vehicle.lane
  local_long, local_lat = lane.local_coordinates(env.vehicle.position)
  heading_err = lane.heading_theta_at(local_long + 3.0) - env.vehicle.heading_theta
  heading_err = (heading_err + math.pi) % (2 * math.pi) - math.pi
  steer_cmd = float(np.clip(heading_err * 1.6 + local_lat * 0.25, -1.0, 1.0))
  dh = lane.heading_theta_at(local_long + 10.0) - lane.heading_theta_at(local_long)
  dh = (dh + math.pi) % (2 * math.pi) - math.pi
  return steer_cmd, abs(float(local_lat)), abs(dh) / 10.0, bool(on_lane)


def _model_inputs() -> dict[str, np.ndarray]:
  return {
    "desire": np.zeros(P.DESIRE_LEN, dtype=np.float32),
    "traffic_convention": np.array([[1.0, 0.0]], dtype=np.float32),
  }


def test_metadrive_3d_real_onnx_bigmodel_and_one_way_unplug_fallback(monkeypatch):
  """Real big_driving_{vision,policy}.onnx over TCP loopback in MetaDrive 3D + mid-drive unplug fallback."""
  big_v = MODELS_DIR / "big_driving_vision.onnx"
  big_p = MODELS_DIR / "big_driving_policy.onnx"
  small_v = MODELS_DIR / "driving_vision.onnx"
  small_p = MODELS_DIR / "driving_policy.onnx"
  if not all(p.is_file() for p in (big_v, big_p, small_v, small_p)):
    pytest.skip("real driving ONNX models not present in selfdrive/modeld/models/")

  # Verify the default metadata path resolves the repo's real pkl files without passing models_dir.
  contract = metadata.load()
  assert contract is not None, "metadata.load() failed to locate selfdrive/modeld/models/*_metadata.pkl"
  assert contract.vision_floats == 1576 and contract.policy_floats == 1000

  # Relax per-frame hold threshold for host CPU ORT + xdist jitter while unplug still raises LinkError immediately.
  import exopilot.selfdrive.modeld.bigmodel.remote_state as rs_mod
  monkeypatch.setattr(rs_mod, "HOLD_FRAME", 0.50)
  monkeypatch.setattr(rs_mod, "LOST_AFTER", 2.0)

  parser = Parser()
  big_backend = S.OnnxBackend(
    str(big_v), str(big_p), contract.vision_slices["hidden_state"], contract.vision_floats, contract.policy_floats
  )
  small_backend = S.OnnxBackend(
    str(small_v), str(small_p), contract.vision_slices["hidden_state"], contract.vision_floats, contract.policy_floats
  )

  params = _MemoryParams()
  gate = BigModelGate()
  warp = _MetaDriveWarp()
  small_inner = _LocalOnnxSmallModel(small_backend, contract, parser, warp)
  local_shim = LocalModel(small_inner)
  in_control = {"active": False}

  def fallback_action(out, prev, lat_t, lon_t, v):
    return (0.0, 0.0)

  action_fn = make_action_function(fallback_action)

  listener = Listener("127.0.0.1", 0).start()
  stop_phone = threading.Event()
  env = None
  facade = None
  try:
    deadline = time.monotonic() + 5.0
    while not listener.bound and time.monotonic() < deadline:
      time.sleep(0.01)
    assert listener.bound

    # Phone dials openpilot's cable listener just like NavPilot in direct-USB mode.
    phone_thread = threading.Thread(
      target=S.dial,
      args=(big_backend, "127.0.0.1", listener.port),
      kwargs=dict(retry=0.05, stop=stop_phone),
      daemon=True,
    )
    phone_thread.start()

    def connect():
      sock = listener.take(0.5)
      if sock is None:
        raise OSError("no phone connected")
      c = Client(sock=sock)
      return c, c.connect()

    def build(client, info):
      return RemoteModelState(
        client,
        info,
        warp,
        parser=parser,
        vision_slices=contract.vision_slices,
        policy_slices=contract.policy_slices,
        get_action_from_model=action_fn,
        log=logging.getLogger("bigmodel.metadrive"),
      )

    joining = Joining(
      local_shim,
      connect,
      build,
      reset_small=small_inner.reset_history,
      log=logging.getLogger("bigmodel.metadrive"),
      in_control=lambda: in_control["active"],
    )
    facade = BigModelFacade(
      joining,
      local_shim,
      params,
      fallback_action,
      policy=ONE_WAY,
      cable=lambda: True,
      enumerated=lambda: True,
      runnable=lambda: True,
    )
    facade.delays = (0.15, 0.15)

    # Wait for background join thread to complete HELLO / HELLO_ACK with real ONNX phone server.
    deadline = time.monotonic() + 10.0
    while joining.big_model_state != "ready" and time.monotonic() < deadline:
      time.sleep(0.02)
    assert joining.big_model_state == "ready", f"phone never reached ready state ({joining.big_model_state})"

    env = _dual_camera_curve_env()
    # Warm up vehicle motion cleanly before starting the big-model drive.
    for _ in range(30):
      st, _, _, _ = _lane_steer_and_metrics(env)
      env.step([st, 0.5])

    big_frames = 0
    post_fallback_small_frames = 0
    saw_loading_during_proving = False
    saw_loading_cleared_after_proving = False
    saw_soft_disable_on_unplug = False
    saw_settling_after_unplug = False
    max_lat_err = 0.0
    on_lane_all = True
    plan_finite_frames = 0
    trips_summary: dict = {}

    sim_t = 100.0
    # Phase 1: small warmup + swap to remote ONNX big model + prove past PROVING_FRAMES while driving MetaDrive 3D.
    total_phase1 = SMALL_WARMUP_FRAMES + PROVING_FRAMES + 8
    for step_idx in range(total_phase1):
      sim_t += 0.05
      bufs = _capture_rgb_pair(env)
      out = facade.run(bufs, {}, _model_inputs())
      assert out is not None and "plan" in out and np.isfinite(out["plan"]).all()
      plan_finite_frames += 1

      loading = bool(params.get("BigModelLoading"))
      active = params.get("BigModelActive")

      if step_idx < SMALL_WARMUP_FRAMES:
        assert not facade.is_big, "must stay on small model during SMALL_WARMUP_FRAMES"
      else:
        assert facade.is_big, f"expected big model active at frame {step_idx}"
        big_frames += 1
        if big_frames <= PROVING_FRAMES:
          assert loading, f"BigModelLoading must stay True during proving frame {big_frames}"
          saw_loading_during_proving = True
        else:
          assert not loading and active is True, "BigModelLoading must clear after PROVING_FRAMES"
          saw_loading_cleared_after_proving = True
          # Engage once proving finishes.
          in_control["active"] = True

      events = gate.events(params.get, enabled=in_control["active"], now=sim_t)
      if saw_loading_during_proving and loading:
        assert "bigModelLoading" in events or not gate.events(params.get, enabled=False, now=sim_t + 0.3)

      # Blend remote model's parsed lateral plan (position y at t~1.0s) sanity-checked into MetaDrive drive.
      plan_xy = out["plan"][0]  # (33, 15) -> [x, y, z, ...]
      assert plan_xy.shape == (33, 15)
      steer_lane, lat_err, _, on_lane = _lane_steer_and_metrics(env)
      # Use small bounded nudge from live ONNX plan heading while keeping car centered on curve.
      model_y = float(np.clip(plan_xy[10, 1], -2.0, 2.0))
      steer_cmd = float(np.clip(steer_lane - 0.04 * model_y, -1.0, 1.0))
      v = float(env.vehicle.speed)
      throttle = float(np.clip((14.0 - v) * 0.35, -0.4, 0.6))
      env.step([steer_cmd, throttle])
      max_lat_err = max(max_lat_err, lat_err)
      on_lane_all = on_lane_all and on_lane

    assert saw_loading_during_proving and saw_loading_cleared_after_proving
    assert in_control["active"] and facade.is_big
    assert joining._big is not None
    trips_summary = joining._big.trips.summary()
    assert trips_summary["frames"] == big_frames

    # Phase 2: Mid-drive USB cable unplug while engaged!
    # Keep phone_thread alive (trying to redial) to prove ONE_WAY never rejoins onroad!
    handovers_before = joining.handovers
    joining._big.client.close()

    for post_idx in range(15):
      sim_t += 0.30
      bufs = _capture_rgb_pair(env)
      out = facade.run(bufs, {}, _model_inputs())
      assert out is not None and "plan" in out and np.isfinite(out["plan"]).all()
      assert not facade.is_big, f"ONE_WAY policy must never rejoin onroad (post frame {post_idx})"
      assert params.get("BigModelActive") is False
      post_fallback_small_frames += 1

      g_out = gate.update(
        loading=bool(params.get("BigModelLoading")),
        active=params.get("BigModelActive"),
        enabled=in_control["active"],
        now=sim_t,
      )
      if post_idx == 0:
        assert g_out.failed and g_out.announce_failed and g_out.soft_disable
        saw_soft_disable_on_unplug = True
        saw_settling_after_unplug = gate.settling(now=sim_t + 1.0)
        # Driver disengages after soft-disable; small model stays available cleanly.
        in_control["active"] = False
      elif post_idx >= 2:
        assert not g_out.soft_disable

      steer_lane, lat_err, _, on_lane = _lane_steer_and_metrics(env)
      v = float(env.vehicle.speed)
      env.step([steer_lane, float(np.clip((12.0 - v) * 0.35, -0.4, 0.5))])
      max_lat_err = max(max_lat_err, lat_err)
      on_lane_all = on_lane_all and on_lane

    assert joining.handovers > handovers_before
    assert facade._given_up is True
    assert saw_soft_disable_on_unplug and saw_settling_after_unplug
    assert not gate.settling(now=sim_t + 10.0)
    assert on_lane_all and max_lat_err < 1.2
    assert small_inner.calls == SMALL_WARMUP_FRAMES + post_fallback_small_frames

    print(
      f"[BIGMODEL-ONNX] big_frames={big_frames} post_fallback_small={post_fallback_small_frames} "
      + f"p50={trips_summary.get('p50_ms')}ms p99={trips_summary.get('p99_ms')}ms "
      + f"server_avg={trips_summary.get('server_ms')}ms max_lat_err={max_lat_err:.3f}m "
      + f"soft_disable={saw_soft_disable_on_unplug} one_way_locked={facade._given_up}"
    )
  finally:
    stop_phone.set()
    if facade is not None:
      facade.close()
    listener.close()
    if env is not None:
      env.close()


def test_metadrive_3d_navpilot_dart_npbm_live_tcp_loop(monkeypatch):
  """Spawn NavPilot's real Dart VM NPBM server (`packages/npbm/tool/serve_stub.dart`) against MetaDrive 3D."""
  serve_dart = NAVPILOT_NPBM / "tool" / "serve_stub.dart"
  if DART_BIN is None or not serve_dart.is_file():
    pytest.skip("NavPilot Dart packages/npbm or dart binary not available")

  import exopilot.selfdrive.modeld.bigmodel.remote_state as rs_mod
  monkeypatch.setattr(rs_mod, "HOLD_FRAME", 0.25)
  monkeypatch.setattr(rs_mod, "LOST_AFTER", 0.80)

  listener = Listener("127.0.0.1", 0).start()
  proc = None
  env = None
  joining = None
  try:
    deadline = time.monotonic() + 5.0
    while not listener.bound and time.monotonic() < deadline:
      time.sleep(0.01)
    assert listener.bound

    proc = subprocess.Popen(
      [DART_BIN, "run", "tool/serve_stub.dart", "dial", "127.0.0.1", str(listener.port)],
      cwd=str(NAVPILOT_NPBM),
      stdout=subprocess.PIPE,
      stderr=subprocess.PIPE,
      text=True,
    )

    warp = _MetaDriveWarp()

    class _RawParser:
      def parse_vision_outputs(self, d):
        return {"vision": d["v"].copy()}

      def parse_policy_outputs(self, d):
        return {"policy": d["p"].copy()}

    class _SmallStub:
      desire_key = "desire"
      numpy_inputs: dict = {}
      lat_delay = 0.0
      calls = 0

      def run(self, *a, **k):
        self.calls += 1
        return {"small": True}

    small = _SmallStub()

    def connect():
      sock = listener.take(5.0)
      if sock is None:
        raise OSError("NavPilot Dart server never dialed in")
      c = Client(sock=sock)
      return c, c.connect()

    def build(client, info):
      return RemoteModelState(
        client,
        info,
        warp,
        parser=_RawParser(),
        vision_slices={"v": slice(0, info.vision_floats)},
        policy_slices={"p": slice(0, info.policy_floats)},
        get_action_from_model=lambda *a: (0.0, 0.0),
        log=logging.getLogger("navpilot.dart.npbm"),
      )

    joining = Joining(
      small,
      connect,
      build,
      None,
      log=logging.getLogger("navpilot.dart.npbm"),
      in_control=lambda: False,
    )

    deadline = time.monotonic() + 20.0
    while joining.big_model_state != "ready" and time.monotonic() < deadline:
      assert proc.poll() is None, f"Dart serve_stub exited early: {proc.stderr.read() if proc.stderr else ''}"
      time.sleep(0.05)
    assert joining.big_model_state == "ready", "openpilot did not complete NPBM HELLO with Dart server"

    env = _dual_camera_curve_env()
    for _ in range(10):
      st, _, _, _ = _lane_steer_and_metrics(env)
      env.step([st, 0.5])

    # Local Python reference state to prove bit-exact parity with the live Dart process!
    ref_state = S.State()
    ref_backend = S.StubBackend(1024, 600)

    dart_frames = 0
    for idx in range(SMALL_WARMUP_FRAMES + 12):
      bufs = _capture_rgb_pair(env)
      out = joining.run(bufs, {}, _model_inputs())
      if idx < SMALL_WARMUP_FRAMES:
        assert not joining.is_big and out == {"small": True}
      else:
        assert joining.is_big
        frame_np = np.frombuffer(bytes(warp.output), np.uint8).reshape(P.FRAME_SHAPE)
        expected = S.step(ref_state, ref_backend, np.zeros(8, np.float32), (1.0, 0.0), frame_np)
        assert expected is not None
        assert np.array_equal(out["vision"][0], expected[:1024]), f"Dart vision mismatch on frame {idx}"
        assert np.array_equal(out["policy"][0], expected[1024:1624]), f"Dart policy mismatch on frame {idx}"
        dart_frames += 1
      st, _, _, _ = _lane_steer_and_metrics(env)
      env.step([st, 0.4])

    trips = joining._big.trips.summary()
    assert dart_frames == 12 and trips["frames"] == 12

    # Kill the Dart phone process mid-drive -> openpilot immediately hands back to local small model.
    proc.kill()
    proc.wait(timeout=5)
    fallback_out = joining.run(_capture_rgb_pair(env), {}, _model_inputs())
    assert fallback_out == {"small": True} and not joining.is_big

    print(
      f"[NAVPILOT-DART-NPBM] dart_frames={dart_frames} bit_exact=True "
      + f"p50={trips.get('p50_ms')}ms p99={trips.get('p99_ms')}ms "
      + f"server_avg={trips.get('server_ms')}ms fallback_on_sigkill=True"
    )
  finally:
    if joining is not None:
      joining.close()
    listener.close()
    if proc is not None:
      if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5)
      if proc.stdout is not None:
        proc.stdout.close()
      if proc.stderr is not None:
        proc.stderr.close()
    if env is not None:
      env.close()


def test_metadrive_3d_navpilot_dart_plp_v5_locate_and_horizon_loop(tmp_path):
  """Real NavPilot Dart `PlpTcpLink` -> openpilot `plpd` (`CablePLP` + `LocateSink`) -> MetaDrive 3D MTSC."""
  pkg_cfg = NAVPILOT_PLP / ".dart_tool" / "package_config.json"
  if DART_BIN is None or not pkg_cfg.is_file():
    pytest.skip("NavPilot Dart packages/plp or dart binary not available")

  # Start real openpilot plpd CablePLP server on loopback.
  sink = LocateSink()
  srv = plpd.build(sink, bind_addr="127.0.0.1", port=0, allowed_prefix="127.")
  srv.start()

  env = None
  try:
    deadline = time.monotonic() + 5.0
    while not srv.bound_port and time.monotonic() < deadline:
      time.sleep(0.02)
    assert srv.bound_port, "plpd CablePLP never bound"

    # Build real MetaDrive 3D curve track & capture live ego pose + ahead-curve polyline.
    env = _dual_camera_curve_env()
    for _ in range(25):
      st, _, _, _ = _lane_steer_and_metrics(env)
      env.step([st, 0.7])

    ego_v = float(env.vehicle.speed)
    ego_x, ego_y = float(env.vehicle.position[0]), float(env.vehicle.position[1])
    lat0, lon0 = 13.7563, 100.5018
    ego_lat = lat0 + ego_x / 111111.0
    ego_lon = lon0 + ego_y / (111111.0 * math.cos(math.radians(lat0)))

    # Construct an R=60m curve MapHorizon (>= 200m total length) matching MetaDrive's PG curve_block(60, 60) ahead of ego.
    polyline = []
    # First 50m straight, then R=60m quarter-circle curve (90 deg arc, ~94.2m), then 120m exit straight (~264.2m total).
    for i in range(6):
      s = i * 10.0
      polyline.append([ego_lat + s / 111111.0, ego_lon])
    cx_m, cy_m = 50.0, 60.0
    for deg in range(6, 91, 6):
      rad = math.radians(deg)
      px = cx_m + 60.0 * math.sin(rad)
      py = cy_m - 60.0 * math.cos(rad)
      polyline.append([ego_lat + px / 111111.0, ego_lon + py / (111111.0 * math.cos(math.radians(lat0)))])
    for i in range(1, 13):
      py = 60.0 + i * 10.0
      polyline.append([ego_lat + 110.0 / 111111.0, ego_lon + py / (111111.0 * math.cos(math.radians(lat0)))])
    total_len_m = 50.0 + (math.pi * 60.0 / 2.0) + 120.0

    payload_file = tmp_path / "plp_payload.json"
    payload_file.write_text(
      json.dumps(
        {
          "port": srv.bound_port,
          "gnss": {
            "t_ms": 1732780000000 + int(time.monotonic() * 1000) % 1000000,
            "lat": ego_lat,
            "lon": ego_lon,
            "h_acc": 2.1,
            "speed": ego_v,
            "bearing": math.degrees(float(env.vehicle.heading_theta)) % 360.0,
          },
          "horizon": {
            "protocolVersion": "5.0.0",
            "msgType": "MapHorizon",
            "horizon_id": 7,
            "route_id": "metadrive-r60",
            "map_version": "osm",
            "source": "phone_osm",
            "start_s": 0.0,
            "length_m": round(total_len_m, 2),
            "end_of_route": False,
            "ttl_s": 30.0,
            "polyline": polyline,
            "speed_limits": [{"s": 0.0, "kph": 80.0}],
          },
        }
      )
    )

    # Real Dart script importing NavPilot's actual `package:plp/plp_tcp.dart`.
    dart_script = tmp_path / "plp_client.dart"
    dart_script.write_text(
      """
import 'dart:convert';
import 'dart:io';
import 'package:plp/plp_tcp.dart';

Future<void> main(List<String> args) async {
  final cfg = jsonDecode(File(args[0]).readAsStringSync()) as Map<String, dynamic>;
  final link = await PlpTcpLink.connect(host: '127.0.0.1', port: cfg['port'] as int);
  if (!link.locateAvailable) {
    stderr.writeln('locate not negotiated');
    exit(2);
  }
  final ok1 = link.sendGnssFix(Map<String, dynamic>.from(cfg['gnss'] as Map));
  final ok2 = link.sendMapHorizon(Map<String, dynamic>.from(cfg['horizon'] as Map));
  await Future<void>.delayed(const Duration(milliseconds: 200));
  stdout.writeln(jsonEncode({
    'ok1': ok1,
    'ok2': ok2,
    'maxPayload': link.agreed?.maxPayload,
    'services': link.agreed?.services.keys.toList(),
  }));
  await link.close();
}
"""
    )

    res = subprocess.run(
      [DART_BIN, "run", f"--packages={pkg_cfg}", str(dart_script), str(payload_file)],
      capture_output=True,
      text=True,
      timeout=25,
      check=False,
    )
    assert res.returncode == 0, f"Dart PlpTcpLink failed: stdout={res.stdout}\nstderr={res.stderr}"
    dart_out = json.loads(res.stdout.strip().splitlines()[-1])
    assert dart_out["ok1"] is True and dart_out["ok2"] is True
    assert "locate" in dart_out["services"] and dart_out["maxPayload"] == 16384

    # Wait briefly for openpilot's LocateSink thread to decode both frames.
    deadline = time.monotonic() + 3.0
    while (sink.latest_fix is None or sink.horizons_taken < 1) and time.monotonic() < deadline:
      time.sleep(0.02)

    assert sink.latest_fix is not None
    assert abs(sink.latest_fix.lat - ego_lat) < 1e-6
    assert abs(sink.latest_fix.lon - ego_lon) < 1e-6
    assert sink.horizons_taken == 1
    horizon = sink.tracker.current
    assert horizon is not None and horizon.route_id == "metadrive-r60"
    assert len(horizon.points) == len(polyline)

    # Compute curvature from the PLP-delivered horizon polyline and verify R=60m curve geometry!
    pts = np.asarray(horizon.points, dtype=np.float64)
    xy = np.column_stack(
      [
        (pts[:, 0] - lat0) * 111111.0,
        (pts[:, 1] - lon0) * 111111.0 * math.cos(math.radians(lat0)),
      ]
    )
    kappas = []
    for i in range(1, len(xy) - 1):
      v1 = xy[i] - xy[i - 1]
      v2 = xy[i + 1] - xy[i]
      n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
      if n1 > 1.0 and n2 > 1.0:
        cross = abs(v1[0] * v2[1] - v1[1] * v2[0])
        kappas.append(2.0 * cross / (n1 * n2 * max(np.linalg.norm(xy[i + 1] - xy[i - 1]), 1e-6)))
    peak_kappa = max(kappas)
    assert 0.012 <= peak_kappa <= 0.022, f"expected R=60m kappa ~0.0167, got {peak_kappa:.5f}"

    # Feed that PLP-delivered curvature into shared MTSC and slow the MetaDrive 3D vehicle closed-loop!
    mtsc = MTSC(enabled=True)
    v_start = float(env.vehicle.speed)
    for _ in range(140):
      v = float(env.vehicle.speed)
      if v >= 16.0:
        break
      st, _, _, _ = _lane_steer_and_metrics(env)
      env.step([st, 1.0])
    v_cruise_start = float(env.vehicle.speed)
    assert v_cruise_start >= 15.0
    min_mtsc_target = 999.0
    for step_idx in range(70):
      v = float(env.vehicle.speed)
      dist_to_curve = max(160.0, 260.0 - step_idx * v * 0.05)
      res = mtsc.update([(dist_to_curve, peak_kappa)], v)
      v_tgt = res["v_target"]
      target_v = float(v_tgt) if (res["is_active"] and v_tgt is not None) else 25.0
      if res["is_active"] and v_tgt is not None:
        min_mtsc_target = min(min_mtsc_target, float(v_tgt))
      pedal = float(np.clip((target_v - v) * 0.45, -0.8, 0.6))
      st, _, _, _ = _lane_steer_and_metrics(env)
      env.step([st, pedal])

    v_after = float(env.vehicle.speed)
    assert min_mtsc_target < 15.0, f"PLP-fed MTSC failed to cap curve speed (min_target={min_mtsc_target:.2f})"
    assert v_after < v_cruise_start - 1.5, f"MetaDrive vehicle did not slow for PLP curve ({v_cruise_start:.2f} -> {v_after:.2f})"

    print(
      f"[NAVPILOT-DART-PLP] dart_negotiated_mtu={dart_out['maxPayload']} "
      + f"gnss=({sink.latest_fix.lat:.5f},{sink.latest_fix.lon:.5f}) "
      + f"peak_kappa={peak_kappa:.4f} mtsc_target={min_mtsc_target:.2f}m/s "
      + f"v_ego={v_cruise_start:.2f}->{v_after:.2f}m/s (init={v_start:.2f})"
    )
  finally:
    srv.stop()
    if env is not None:
      env.close()


def test_metadrive_3d_jetlink_v3_protocol_and_transport_loop():
  """Live JetLink v3 wire protocol + TcpTransport + JetlinkClient carrying MetaDrive 3D camera frames."""
  if not (JETLINK_REPO / "jetlink" / "protocol.py").is_file():
    pytest.skip("jetlink sibling repository not present")

  jetlink_str = str(JETLINK_REPO)
  added = False
  if jetlink_str not in sys.path:
    sys.path.insert(0, jetlink_str)
    added = True

  env = None
  client = None
  peer = None
  srv_sock = None
  stop = threading.Event()
  try:
    from jetlink import protocol as JP
    from jetlink.client import JetlinkClient
    from jetlink.spec import ModelSpec
    from jetlink.transport.base import LinkError, LinkTimeout
    from jetlink.transport.tcp import TcpTransport

    spec = ModelSpec(
      sha256="a" * 64,
      nbytes=42_000_000,
      frame_skip=4,
      input_shapes={
        "img": (1, 12, 128, 256),
        "big_img": (1, 12, 128, 256),
        "features_buffer": (1, 25, 512),
        "desire_pulse": (1, 25, 8),
        "traffic_convention": (1, 2),
        "action_t": (1, 2),
      },
      output_shapes={"outputs": (1, 1000)},
      output_slices={"plan": slice(0, 990), "hidden_state": slice(990, 1000)},
      checkpoint="wsl-metadrive-test",
    )
    assert spec.warped_nbytes == P.FRAME_BYTES

    # Open a real TCP loopback pair via TcpTransport (Jetlink v3 framing).
    srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv_sock.bind(("127.0.0.1", 0))
    srv_sock.listen(1)
    port = srv_sock.getsockname()[1]

    accepted: list[socket.socket] = []

    def accept_one():
      conn, _ = srv_sock.accept()
      accepted.append(conn)

    t_acc = threading.Thread(target=accept_one, daemon=True)
    t_acc.start()
    cli_sock = socket.create_connection(("127.0.0.1", port), timeout=2.0)
    t_acc.join(2.0)
    assert accepted
    peer_sock = accepted[0]

    ours = TcpTransport(cli_sock)
    peer = TcpTransport(peer_sock)
    client = JetlinkClient(ours, deadline=1.0)
    client.spec = spec

    server_stats = {"hello": 0, "resets": 0, "frames": 0, "leaves": []}

    def serve():
      try:
        while not stop.is_set():
          try:
            msg = peer.recv(timeout=0.5)
          except LinkTimeout:
            continue
          if msg.msg_type == JP.Msg.HELLO_REQ:
            server_stats["hello"] += 1
            peer.send_json(JP.Msg.HELLO_RESP, msg.seq, {"device": "wsl-metadrive-phone", "protocol": JP.VERSION})
          elif msg.msg_type == JP.Msg.INFER_REQ:
            frame_id, flags = JP.unpack_infer_req(msg.payload)
            if flags & JP.Flag.RESET_QUEUES:
              server_stats["resets"] += 1
            warped_bytes = memoryview(msg.payload)[JP.INFER_REQ_SIZE : JP.INFER_REQ_SIZE + spec.warped_nbytes]
            assert len(warped_bytes) == spec.warped_nbytes
            mean_val = float(np.frombuffer(warped_bytes, dtype=np.uint8).mean())
            out = np.full(spec.reply_nelem, mean_val, dtype=np.float32)
            out[0] = float(frame_id)
            resp = JP.pack_infer_resp(frame_id, JP.Status.OK, 1200, 100, 1500) + out.tobytes()
            peer.send(JP.Msg.INFER_RESP, msg.seq, (resp,))
            server_stats["frames"] += 1
          elif msg.msg_type == JP.Msg.LEAVE:
            server_stats["leaves"].append(json.loads(bytes(msg.payload)))
          elif msg.msg_type == JP.Msg.PING:
            peer.send(JP.Msg.PONG, msg.seq)
      except LinkError:
        pass

    t_srv = threading.Thread(target=serve, daemon=True)
    t_srv.start()

    hello = client.hello(timeout=2.0)
    assert hello["protocol"] == 3 and server_stats["hello"] == 1

    env = _dual_camera_curve_env()
    warp = _MetaDriveWarp()
    packed = np.zeros(spec.packed_nelem, dtype=np.float32).tobytes()

    rtts_ms = []
    means_seen = []
    for fid in range(1, 16):
      bufs = _capture_rgb_pair(env)
      warp.start_buffers(bufs, {})
      t0 = time.perf_counter()
      seq = client.infer_begin(bytes(warp.output), packed, frame_id=fid, reset=(fid == 1))
      out = client.infer_end(seq)
      rtts_ms.append((time.perf_counter() - t0) * 1000.0)
      assert out is not None and out.size == spec.output_nelem
      assert (out[990:] == 0.0).all(), "JetLink v3 must zero-fill server-held hidden_state slice"
      assert out[0] == float(fid)
      expected_mean = float(np.frombuffer(bytes(warp.output), dtype=np.uint8).mean())
      assert abs(float(out[1]) - expected_mean) < 1e-4
      means_seen.append(expected_mean)
      st, _, _, _ = _lane_steer_and_metrics(env)
      env.step([st, 0.5])

    assert server_stats["frames"] == 15 and server_stats["resets"] == 1
    assert max(means_seen) - min(means_seen) > 0.05, "MetaDrive 3D frames must evolve dynamically across steps"

    # Send LEAVE_LOST, then close peer socket and verify JetlinkClient raises LinkError cleanly.
    client.leave(JP.LEAVE_LOST, frames=15, p50_ms=round(float(np.median(rtts_ms)), 2))
    deadline = time.monotonic() + 2.0
    while not server_stats["leaves"] and time.monotonic() < deadline:
      time.sleep(0.01)
    assert server_stats["leaves"] and server_stats["leaves"][0]["reason"] == JP.LEAVE_LOST

    stop.set()
    peer.close()
    with pytest.raises(LinkError):
      client.ping(timeout=1.0)
    with pytest.raises(LinkError):
      seq = client.infer_begin(bytes(warp.output), packed, frame_id=16)
      client.infer_end(seq)
    assert client.dead is True

    print(
      f"[JETLINK-V3] frames={server_stats['frames']} resets={server_stats['resets']} "
      + f"p50={np.median(rtts_ms):.2f}ms max={max(rtts_ms):.2f}ms "
      + f"frame_mean_span={min(means_seen):.2f}..{max(means_seen):.2f} leave={server_stats['leaves'][0]['reason']}"
    )
  finally:
    stop.set()
    if client is not None:
      client.close()
    if peer is not None:
      peer.close()
    if srv_sock is not None:
      srv_sock.close()
    if env is not None:
      env.close()
    if added and jetlink_str in sys.path:
      sys.path.remove(jetlink_str)
