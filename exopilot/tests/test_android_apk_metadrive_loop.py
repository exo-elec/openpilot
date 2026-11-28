"""Live Android 14 Emulator APK <-> openpilot (PLP v5 + NPBM Big-Model) <-> MetaDrive 3D loop verification.

Exercises real built NavPilot Android APKs (`com.navpilot.app`) running inside a live Android 14
AVD emulator in WSL2 over `adb reverse` TCP sockets into real openpilot daemons + MetaDrive 3D physics:
  1. Production NavPilot APK (`com.navpilot.app/.MainActivity`) boot on Android 14:
     - Verifies process stays alive with zero FATAL crashes, renders non-blank Flutter UI (`screencap`),
       and checks Samsung Galaxy S25/S26 `arm64-v8a` APK packaging when staged.
  2. Full Android 14 APK <-> `adb reverse` <-> openpilot `plpd` + `BigModel` `Listener`/`Joining`
     <-> MetaDrive 3D closed loop:
     - Verifies native Android Kotlin `MethodChannel('com.navpilot.app/hardware')` detects emulator and
       cleanly classifies `unsupportedFallback` / `onboardFallback` (iOS + Snapdragon-only NPU policy).
     - Receives live PLP v5 GNSS + `R=60 m` curve `MapHorizon` from `PlpTcpLink` running inside Android 14
       -> `openpilot` `CablePLP` + `LocateSink` -> `MTSC` decelerates live MetaDrive 3D car.
     - Streams 12 live MetaDrive 3D dual-camera `(2,6,128,256)` uint8 frames from `openpilot` `BigModelFacade`
       over `adb reverse` into Android 14 `NpbmServer` -> asserts bit-exact parity (`np.array_equal`)
       -> kills Android APK via `adb shell am force-stop com.navpilot.app` -> verifies immediate
       one-way fallback to local small model.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from exopilot.selfdrive.controls.lib.mtsc import MTSC
from exopilot.selfdrive.modeld.bigmodel import protocol as P
from exopilot.selfdrive.modeld.bigmodel import server_ref as S
from exopilot.selfdrive.modeld.bigmodel.client import Client
from exopilot.selfdrive.modeld.bigmodel.hook import ONE_WAY, BigModelFacade, LocalModel
from exopilot.selfdrive.modeld.bigmodel.joining import SMALL_WARMUP_FRAMES, Joining
from exopilot.selfdrive.modeld.bigmodel.listener import Listener
from exopilot.selfdrive.modeld.bigmodel.remote_state import RemoteModelState
from exopilot.system.plp import plpd
from exopilot.system.plp.locate import LocateSink

metadrive = pytest.importorskip("metadrive")

ADB_BIN = shutil.which("adb") or (
  "/home/vcar/dev/android-sdk/platform-tools/adb"
  if Path("/home/vcar/dev/android-sdk/platform-tools/adb").is_file()
  else None
)
APK_STAGE_DIR = Path(os.environ.get("NAVPILOT_APK_DIR", "/home/vcar/exo-sim/navpilot-apk"))
PROD_X64_APK = APK_STAGE_DIR / "navpilot-emulator-prod-x64.apk"
LOOP_X64_APK = APK_STAGE_DIR / "navpilot-emulator-loop-x64.apk"
SAMSUNG_ARM64_APK = APK_STAGE_DIR / "navpilot-samsung-s25-s26-arm64.apk"
PKG_NAME = "com.navpilot.app"


def _active_emulator_serial() -> str | None:
  if not ADB_BIN:
    return None
  out = subprocess.run([ADB_BIN, "devices"], capture_output=True, text=True, timeout=10, check=False).stdout
  for line in out.splitlines()[1:]:
    parts = line.split()
    if len(parts) >= 2 and parts[1] == "device":
      return parts[0]
  return None


def _adb(serial: str, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
  assert ADB_BIN is not None
  return subprocess.run([ADB_BIN, "-s", serial, *args], capture_output=True, text=True, timeout=timeout, check=False)


class _MemParams:
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


class _RawParser:
  def parse_vision_outputs(self, d):
    return d

  def parse_policy_outputs(self, d):
    return d


def _rgb_to_yuv6(rgb: np.ndarray) -> np.ndarray:
  if rgb.dtype != np.uint8:
    rgb = np.clip(rgb * 255.0, 0, 255).astype(np.uint8)
  resized = cv2.resize(rgb, (512, 256), interpolation=cv2.INTER_AREA)
  yuv = cv2.cvtColor(resized, cv2.COLOR_RGB2YUV_I420)
  y = yuv[:256, :512]
  uv = yuv[256:, :].reshape(-1)
  out = np.empty((6, 128, 256), dtype=np.uint8)
  out[0] = y[0::2, 0::2]
  out[1] = y[0::2, 1::2]
  out[2] = y[1::2, 0::2]
  out[3] = y[1::2, 1::2]
  out[4] = uv[: 128 * 256].reshape(128, 256)
  out[5] = uv[128 * 256 : 2 * 128 * 256].reshape(128, 256)
  return out


class _MetaDriveWarp:
  def __init__(self):
    self.output = memoryview(bytearray(P.FRAME_BYTES))
    self._stacked = np.zeros(P.FRAME_SHAPE, dtype=np.uint8)

  def start_buffers(self, bufs, transforms) -> None:
    self._stacked[0] = _rgb_to_yuv6(bufs["road"])
    self._stacked[1] = _rgb_to_yuv6(bufs["wide"])
    self.output[:] = self._stacked.tobytes()

  def wait(self) -> None:
    pass


class _SmallStubModel:
  def __init__(self, warp: _MetaDriveWarp):
    self.warp = warp
    self.numpy_inputs: dict[str, np.ndarray] = {}
    self.calls = 0

  def reset_history(self) -> None:
    pass

  def run(self, bufs, transforms, inputs, prepare_only=False):
    self.warp.start_buffers(bufs, transforms)
    self.warp.wait()
    if prepare_only:
      return None
    self.calls += 1
    return {"vision": np.zeros((1, 1024), np.float32), "policy": np.zeros((1, 600), np.float32)}


def _curve_env():
  from metadrive.component.map.pg_map import MapGenerateMethod
  from metadrive.envs.metadrive_env import MetaDriveEnv
  from openpilot.tools.sim.bridge.metadrive.metadrive_bridge import curve_block, straight_block
  from openpilot.tools.sim.bridge.metadrive.metadrive_common import RGBCameraRoad, RGBCameraWide

  env = MetaDriveEnv(
    dict(
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
  )
  env.reset()
  return env


def _launch_navpilot(serial: str) -> subprocess.CompletedProcess:
  _adb(serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
  _adb(serial, "shell", "wm", "dismiss-keyguard")
  return _adb(
    serial,
    "shell",
    "am",
    "start",
    "-S",
    "-W",
    "-a",
    "android.intent.action.MAIN",
    "-c",
    "android.intent.category.LAUNCHER",
    "-n",
    f"{PKG_NAME}/.MainActivity",
    timeout=90.0,
  )


def test_android_14_emulator_production_navpilot_apk_boot_and_samsung_arm64_packaging(tmp_path):
  """Boot real production NavPilot APK on Android 14 AVD + verify Samsung S25/S26 arm64-v8a APK artifact."""
  serial = _active_emulator_serial()
  if serial is None or not PROD_X64_APK.is_file():
    pytest.skip("booted Android AVD emulator or staged production APK not present")

  # Verify Samsung S25/S26 flagship arm64-v8a APK artifact contains real arm64-v8a Flutter engine.
  if SAMSUNG_ARM64_APK.is_file():
    with zipfile.ZipFile(SAMSUNG_ARM64_APK) as zf:
      names = zf.namelist()
    assert any("lib/arm64-v8a/libflutter.so" in n for n in names), "Samsung S25/S26 APK missing arm64-v8a libflutter.so"

  _adb(serial, "shell", "am", "force-stop", PKG_NAME)
  inst = _adb(serial, "install", "-r", str(PROD_X64_APK), timeout=180.0)
  assert inst.returncode == 0 and "Success" in inst.stdout, f"APK install failed: {inst.stdout} {inst.stderr}"
  time.sleep(2.0)

  _adb(serial, "logcat", "-c")
  launch = _launch_navpilot(serial)
  assert launch.returncode == 0, f"APK launch failed: {launch.stdout} {launch.stderr}"

  pid = ""
  deadline = time.monotonic() + 60.0
  while time.monotonic() < deadline:
    pid = _adb(serial, "shell", "pidof", PKG_NAME).stdout.strip()
    if pid.isdigit():
      break
    time.sleep(1.0)
  assert pid.isdigit(), f"Production NavPilot APK not running on Android 14 (pidof='{pid}', launch={launch.stdout})"
  # Software TCG takes ~70-90 s to extract debug kernel_blob.bin & initialize Flutter Impeller on first launch.
  img = None
  std_val = 0.0
  shot_path = tmp_path / "navpilot_android14.png"
  render_deadline = time.monotonic() + 150.0
  while time.monotonic() < render_deadline:
    pid_after = _adb(serial, "shell", "pidof", PKG_NAME).stdout.strip()
    assert pid_after.isdigit(), f"Production NavPilot APK exited during startup (pidof='{pid_after}')"
    fl_logs = _adb(serial, "logcat", "-d", "-s", "flutter:I").stdout
    if "Impeller" in fl_logs or "flutter" in fl_logs.lower():
      time.sleep(5.0)
      raw_png = subprocess.run([ADB_BIN, "-s", serial, "exec-out", "screencap", "-p"], capture_output=True, timeout=90, check=True).stdout
      shot_path.write_bytes(raw_png)
      img = cv2.imdecode(np.frombuffer(raw_png, np.uint8), cv2.IMREAD_COLOR)
      if img is not None and img.shape[0] >= 1920 and img.shape[1] >= 1080:
        std_val = float(img.std())
        if std_val > 5.0:
          break
    time.sleep(3.0)

  pid_after = _adb(serial, "shell", "pidof", PKG_NAME).stdout.strip()
  assert pid_after.isdigit(), f"Production NavPilot APK exited after startup (pidof='{pid_after}')"

  logs = _adb(serial, "logcat", "-d", "-s", "AndroidRuntime:E", "flutter:E").stdout
  assert "FATAL EXCEPTION" not in logs, f"Production APK crashed:\n{logs}"
  assert img is not None and img.shape[0] >= 1920 and img.shape[1] >= 1080
  assert std_val > 5.0, f"Android 14 NavPilot UI rendered blank (std={std_val:.2f})"

  _adb(serial, "shell", "am", "force-stop", PKG_NAME)
  print(
    f"[ANDROID14-PROD-APK] serial={serial} pid={pid_after} screen={img.shape[1]}x{img.shape[0]} "
    + f"std={std_val:.1f} arm64_apk={SAMSUNG_ARM64_APK.is_file()}"
  )


def test_android_14_emulator_apk_plp_v5_and_npbm_metadrive_3d_closed_loop(monkeypatch):
  """Live Android 14 APK -> adb reverse TCP -> openpilot plpd/MTSC + BigModel Listener/Joining -> MetaDrive 3D."""
  serial = _active_emulator_serial()
  if serial is None or not LOOP_X64_APK.is_file():
    pytest.skip("booted Android AVD emulator or staged loop-harness APK not present")

  import exopilot.selfdrive.modeld.bigmodel.remote_state as rs_mod
  # Software TCG emulator has higher frame latency jitter than real Snapdragon 8 Elite; keep LinkError instant on force-stop.
  monkeypatch.setattr(rs_mod, "HOLD_FRAME", 1.5)
  monkeypatch.setattr(rs_mod, "LOST_AFTER", 4.0)

  sink = LocateSink()
  plp_srv = plpd.build(sink, bind_addr="127.0.0.1", port=0, allowed_prefix="127.")
  plp_srv.start()

  listener = Listener("127.0.0.1", 0).start()
  env = None
  facade = None
  try:
    deadline = time.monotonic() + 5.0
    while (plp_srv.bound_port is None or not listener.bound) and time.monotonic() < deadline:
      time.sleep(0.01)
    assert plp_srv.bound_port is not None and listener.bound

    # Reverse-forward Android 14 loopback ports directly into host WSL openpilot servers!
    r1 = _adb(serial, "reverse", "tcp:15601", f"tcp:{plp_srv.bound_port}")
    r2 = _adb(serial, "reverse", "tcp:17600", f"tcp:{listener.port}")
    assert r1.returncode == 0 and r2.returncode == 0, f"adb reverse failed: {r1.stderr} {r2.stderr}"

    _adb(serial, "shell", "am", "force-stop", PKG_NAME)
    inst = _adb(serial, "install", "-r", str(LOOP_X64_APK), timeout=180.0)
    assert inst.returncode == 0 and "Success" in inst.stdout
    time.sleep(2.0)

    warp = _MetaDriveWarp()
    small = _SmallStubModel(warp)
    local_shim = LocalModel(small)
    params = _MemParams()

    def connect():
      sock = listener.take(0.5)
      if sock is None:
        raise OSError("waiting for Android APK")
      c = Client(sock=sock)
      return c, c.connect()

    def build(client, info):
      return RemoteModelState(
        client,
        info,
        warp,
        parser=_RawParser(),
        vision_slices={"vision": slice(0, info.vision_floats)},
        policy_slices={"policy": slice(0, info.policy_floats)},
        get_action_from_model=lambda *a: (0.0, 0.0),
        log=logging.getLogger("bigmodel.android_apk"),
      )

    joining = Joining(
      local_shim,
      connect,
      build,
      reset_small=small.reset_history,
      log=logging.getLogger("bigmodel.android_apk"),
      in_control=lambda: False,
    )
    facade = BigModelFacade(
      joining,
      local_shim,
      params,
      lambda *a: (0.0, 0.0),
      policy=ONE_WAY,
      cable=lambda: True,
      enumerated=lambda: True,
      runnable=lambda: True,
    )
    facade.delays = (0.15, 0.15)

    _adb(serial, "logcat", "-c")
    launch = _launch_navpilot(serial)
    assert launch.returncode == 0

    # Wait for Android 14 APK to complete hardware probe, PLP v5 GNSS+MapHorizon, and NPBM HELLO!
    deadline = time.monotonic() + 210.0
    while (sink.latest_fix is None or sink.horizons_taken < 1 or joining.big_model_state != "ready") and time.monotonic() < deadline:
      time.sleep(0.25)

    logs = _adb(serial, "logcat", "-d", "-s", "flutter:I").stdout
    assert "[NAVPILOT-APK-HW]" in logs and "tier=unsupportedFallback" in logs and "backend=onboardFallback" in logs, (
      f"Android APK MethodChannel hardware probe missing fallback classification:\n{logs}"
    )
    assert sink.latest_fix is not None and sink.horizons_taken >= 1, (
      f"Android APK PLP v5 did not reach openpilot LocateSink (fix={sink.latest_fix}, horizons={sink.horizons_taken}):\n{logs}"
    )
    assert joining.big_model_state == "ready", (
      f"Android APK NPBM did not complete HELLO (state={joining.big_model_state}):\n{logs}"
    )

    # Verify PLP v5 GNSS + MapHorizon decoded by openpilot LocateSink drives MTSC in MetaDrive 3D!
    lat0, lon0 = 13.75636, 100.50184
    assert abs(sink.latest_fix.lat - lat0) < 1e-5
    assert abs(sink.latest_fix.lon - lon0) < 1e-5
    horizon = sink.tracker.current
    assert horizon is not None and horizon.route_id == "android-apk-metadrive-curve"
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
      n1, n2 = float(np.linalg.norm(v1)), float(np.linalg.norm(v2))
      if n1 > 1.0 and n2 > 1.0:
        cross = abs(float(v1[0] * v2[1] - v1[1] * v2[0]))
        kappas.append(2.0 * cross / (n1 * n2 * max(float(np.linalg.norm(xy[i + 1] - xy[i - 1])), 1e-6)))
    peak_kappa = max(kappas)
    assert 0.012 <= peak_kappa <= 0.022, f"expected R=60m kappa ~0.0167, got {peak_kappa:.5f}"

    env = _curve_env()
    for _ in range(140):
      lane = env.vehicle.lane
      s_loc, d_lat = lane.local_coordinates(env.vehicle.position)
      h_err = (lane.heading_theta_at(s_loc + 3.0) - env.vehicle.heading_theta + math.pi) % (2 * math.pi) - math.pi
      st = float(np.clip(h_err * 1.6 + d_lat * 0.25, -1.0, 1.0))
      env.step([st, 1.0])
      if float(env.vehicle.speed) >= 16.0:
        break

    v_initial = float(env.vehicle.speed)
    assert v_initial >= 15.0
    mtsc = MTSC(enabled=True)
    min_mtsc_target = 999.0
    for step_idx in range(70):
      v_now = float(env.vehicle.speed)
      dist_to_curve = max(160.0, 260.0 - step_idx * v_now * 0.05)
      mtsc_res = mtsc.update([(dist_to_curve, peak_kappa)], v_now)
      v_tgt = mtsc_res["v_target"]
      target_v = float(v_tgt) if (mtsc_res["is_active"] and v_tgt is not None) else 25.0
      if mtsc_res["is_active"] and v_tgt is not None:
        min_mtsc_target = min(min_mtsc_target, float(v_tgt))
      lane = env.vehicle.lane
      s_loc, d_lat = lane.local_coordinates(env.vehicle.position)
      h_err = (lane.heading_theta_at(s_loc + 3.0) - env.vehicle.heading_theta + math.pi) % (2 * math.pi) - math.pi
      st = float(np.clip(h_err * 1.6 + d_lat * 0.25, -1.0, 1.0))
      env.step([st, float(np.clip((target_v - v_now) * 0.45, -0.8, 0.6))])
    v_slowed = float(env.vehicle.speed)
    assert min_mtsc_target < 15.0, f"PLP-fed MTSC failed to cap curve speed (min_target={min_mtsc_target:.2f})"
    assert v_slowed < v_initial - 1.5, f"MetaDrive vehicle did not slow for Android APK PLP curve ({v_initial:.2f} -> {v_slowed:.2f})"

    # Stream 12 live MetaDrive 3D dual-camera frames from openpilot into Android 14 APK NPBM server!
    ref_state = S.State()
    ref_backend = S.StubBackend(1024, 600)
    apk_frames = 0
    inputs = {"desire": np.zeros(P.DESIRE_LEN, np.float32), "traffic_convention": np.array([[1.0, 0.0]], np.float32)}
    for step_idx in range(SMALL_WARMUP_FRAMES + 12):
      road = np.ascontiguousarray(env.engine.get_sensor("rgb_road").perceive(to_float=False)[:, :, :3])
      wide = np.ascontiguousarray(env.engine.get_sensor("rgb_wide").perceive(to_float=False)[:, :, :3])
      out = facade.run({"road": road, "wide": wide}, {}, inputs)
      if step_idx >= SMALL_WARMUP_FRAMES:
        assert facade.is_big, f"Android APK NPBM dropped at frame {step_idx}"
        frame_np = np.frombuffer(bytes(warp.output), np.uint8).reshape(P.FRAME_SHAPE)
        want = S.step(ref_state, ref_backend, inputs["desire"], (1.0, 0.0), frame_np)
        got = np.concatenate([out["vision"].reshape(-1), out["policy"].reshape(-1)])
        assert np.array_equal(got, want), f"Bit-exact parity mismatch with Android 14 APK at frame {apk_frames}"
        apk_frames += 1
      lane = env.vehicle.lane
      s_loc, d_lat = lane.local_coordinates(env.vehicle.position)
      h_err = (lane.heading_theta_at(s_loc + 3.0) - env.vehicle.heading_theta + math.pi) % (2 * math.pi) - math.pi
      env.step([float(np.clip(h_err * 1.6 + d_lat * 0.25, -1.0, 1.0)), 0.15])

    assert apk_frames == 12
    trips = joining._big.trips.summary()

    # Kill the real Android 14 app mid-drive and verify immediate one-way fallback to local small model!
    _adb(serial, "shell", "am", "force-stop", PKG_NAME)
    time.sleep(0.5)
    road = np.ascontiguousarray(env.engine.get_sensor("rgb_road").perceive(to_float=False)[:, :, :3])
    wide = np.ascontiguousarray(env.engine.get_sensor("rgb_wide").perceive(to_float=False)[:, :, :3])
    out_fb = facade.run({"road": road, "wide": wide}, {}, inputs)
    assert out_fb is not None and not facade.is_big
    assert params.get("BigModelActive") is False

    print(
      f"[ANDROID14-APK-LOOP] serial={serial} hw_fallback=True "
      + f"plp_peak_k={peak_kappa:.4f} mtsc_v={v_initial:.2f}->{v_slowed:.2f}m/s "
      + f"npbm_frames={apk_frames} bit_exact=True p50={trips['p50_ms']:.1f}ms force_stop_fallback=True"
    )
  finally:
    if facade is not None:
      facade.close()
    if env is not None:
      env.close()
    listener.close()
    plp_srv.stop()
    _adb(serial, "shell", "am", "force-stop", PKG_NAME)
    _adb(serial, "reverse", "--remove", "tcp:15601")
    _adb(serial, "reverse", "--remove", "tcp:17600")
