#!/usr/bin/env python3
"""
V4L2 Camera Daemon — ExoPilot 02M's MIPI CSI cameras (road = mono_narrow,
wide_road = mono_wide, tele_road = mono_tele, stereo_left, stereo_right).

All cameras share identical capture/send/restart logic driven by
CameraConfig data.  VisionIPC server is always "v4l2d".

ISP Integration:
  Hardware 3A (AE/AWB) via Rockchip ISP when available.
  Falls back to software AE when ISP not accessible.
"""

import os
import threading
import time
from collections import namedtuple
from dataclasses import dataclass
from collections.abc import Generator

from msgq.visionipc import VisionIpcServer, VisionStreamType
from cereal import messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware import HARDWARE
from openpilot.common.params import Params

_params = Params()

from openpilot.system.v4l2d.occlusion_detector import OcclusionDetector, OcclusionROI
from openpilot.system.hardware.rk_device_id import SUPPORTED_SOCS


# Device types this daemon's hardcoded camera array is valid for: the board
# this branch carries, plus None/'pc' for dev and CI. A whitelist, not a
# blacklist, so a board added later has to opt in deliberately -- and named
# once here rather than inline, so the tests assert against the same tuple
# the guard uses instead of re-spelling board names that then go stale on
# the other branch.
SUPPORTED_DEVICE_TYPES = (None, 'pc', SUPPORTED_SOCS[0])



# MIPI CSI device-path candidates live in the closed HAL package, per board.
_HAL_MIPI_PATHS = getattr(
  HARDWARE.hal_module("camera_paths"), "DEFAULT_MIPI_CAMERA_PATHS", {})


def _mipi_paths(camera: str, fallback: list[str]) -> list[str]:
  """Return HAL device-path candidates for a camera, with a safe fallback."""
  return _HAL_MIPI_PATHS.get(camera, fallback) if _HAL_MIPI_PATHS else fallback

# ---------------------------------------------------------------------------
# Stream type IDs — all cameras in one place
# EOP stereo IDs extend upstream enum (msgq not modified; raw ints are fine
# as long as publisher and all subscribers use these same constants).
# ---------------------------------------------------------------------------
STREAM_ROAD         = VisionStreamType.VISION_STREAM_ROAD
STREAM_WIDE_ROAD    = VisionStreamType.VISION_STREAM_WIDE_ROAD
STREAM_STEREO_LEFT  = VisionStreamType.VISION_STREAM_STEREO_LEFT
STREAM_STEREO_RIGHT = VisionStreamType.VISION_STREAM_STEREO_RIGHT
STREAM_STEREO_DEPTH = VisionStreamType.VISION_STREAM_STEREO_DEPTH
STREAM_TELE_ROAD    = VisionStreamType.VISION_STREAM_TELE_ROAD

# ---------------------------------------------------------------------------
# CameraConfig — single source of truth for each camera
# ---------------------------------------------------------------------------
CameraConfig = namedtuple("CameraConfig", [
  "msg_name",     # cereal message name published each frame
  "stream_type",  # VisionIPC stream ID (int or VisionStreamType)
  "device_path",  # /dev/videoN — may be overridden by HAL or discovery
  "cam_id",       # unique string ID used for health params and logging
  "vipc_server",  # always "v4l2d"
  "sensor",       # messaging.log.FrameData.ImageSensor value
  "sensor_name",  # "ox03c10" or "gc4653" — for driver selection
  "hdr_mode",     # "sdr", "hdr2", "hdr3", "hdr4"
  "fps",          # target framerate
])

CAMERA_RESTART_COOLDOWN_SEC = float(os.getenv("CSI_RESTART_COOLDOWN_SEC", "1.0"))


# ---------------------------------------------------------------------------
# ExoPilot 02M MIPI array (hal boards.py BOARD_DATA["exopilot02m"]["cameras"],
# I2C ids from kernel/dts/rk3576-rpdzkj-exp02.dts):
#   role          stream        sensor   I2C bus-addr
#   mono_narrow   road          OX03C10  3-0x36   (8.0 mm)
#   mono_wide     wide_road     OX03C10  4-0x36   (1.7 mm)
#   mono_tele     tele_road     OX03C10  5-0x36   (16.0 mm, EOPTeleEnabled)
#   stereo_left   stereo_left   GC4653   5-0x29   (EOPStereoEnabled)
#   stereo_right  stereo_right  GC4653   6-0x29   (EOPStereoEnabled)
# ---------------------------------------------------------------------------
Camera02M = namedtuple("Camera02M", ["role", "stream", "msg_name", "stream_type", "cam_id",
                                     "sensor_name", "i2c_bus", "i2c_addr", "hdr_param", "gate_param",
                                     "dev_fallback"])

CAMERAS_02M = (
  Camera02M("mono_narrow", "road", "roadCameraState", STREAM_ROAD, "road_camera",
            "ox03c10", 3, 0x36, "EOPRoadHDR", None, ["/dev/video0"]),
  Camera02M("mono_wide", "wide_road", "wideRoadCameraState", STREAM_WIDE_ROAD, "wide_camera",
            "ox03c10", 4, 0x36, "EOPWideHDR", None, ["/dev/video1"]),
  Camera02M("mono_tele", "tele_road", "teleRoadCameraState", STREAM_TELE_ROAD, "tele_camera",
            "ox03c10", 5, 0x36, "EOPRoadHDR", "EOPTeleEnabled", ["/dev/video2"]),
  Camera02M("stereo_left", "stereo_left", "stereoCameraState", STREAM_STEREO_LEFT, "stereo_left_camera",
            "gc4653", 5, 0x29, None, "EOPStereoEnabled", ["/dev/video3"]),
  Camera02M("stereo_right", "stereo_right", "stereoCameraStateRight", STREAM_STEREO_RIGHT, "stereo_right_camera",
            "gc4653", 6, 0x29, None, "EOPStereoEnabled", ["/dev/video4"]),
)


def _confirmed_path(cam: Camera02M) -> str | None:
  """Device path recorded for this role on a real unit (hal rk3576_camera_paths),
  keyed by role (mono_narrow) or stream name (road); first existing candidate."""
  candidates = _HAL_MIPI_PATHS.get(cam.role) or _HAL_MIPI_PATHS.get(cam.stream) or []
  for p in candidates:
    if os.path.exists(p):
      return p
  return candidates[0] if candidates else None


def _default_camera_configs(device_type: str | None = None) -> list[CameraConfig]:
  """ExoPilot 02M's 5-camera MIPI array (+ USB cameras, handled by uvcd).

  Device paths come ONLY from paths confirmed on real hardware (hal
  rk3576_camera_paths.DEFAULT_MIPI_CAMERA_PATHS). Three cameras are OX03C10
  and two are GC4653, so matching /dev/videoN by sensor name cannot tell
  narrow/wide/tele (or left/right) apart; a role without a confirmed path is
  skipped with an error rather than risk publishing one camera under
  another's identity. `python3 -m openpilot.system.v4l2d.list_cameras` on a
  unit prints what is needed to fill the table. On a dev PC ('pc'/None) the
  plain /dev/videoN fallbacks are used.
  """
  sensor = messaging.log.FrameData.ImageSensor
  dev_pc = device_type in (None, 'pc')

  try:
    cam_config = HARDWARE.get_camera_array_config()
    cloudlog.info(
        f"v4l2d: Camera array - {cam_config.get('platform', 'Unknown')} "
        + f"({cam_config.get('num_cameras', 0)} cameras, "
        + f"{cam_config.get('stereo_baseline_mm', 0)}mm baseline)"
    )
  except Exception as e:
    cloudlog.warning(f"v4l2d: Failed to get camera array config: {e}")

  configs = []
  used: set[str] = set()
  for cam in CAMERAS_02M:
    if cam.gate_param and not _params.get_bool(cam.gate_param):
      continue
    path = _confirmed_path(cam)
    if path is None and dev_pc:
      path = next((p for p in cam.dev_fallback if p not in used), None)
    if path is None:
      cloudlog.error(
        f"v4l2d: no confirmed /dev/video path for {cam.role} (I2C {cam.i2c_bus}-{cam.i2c_addr:#04x}); "
        + "not opening it -- record it in hal rk3576_camera_paths.py (list_cameras.py)")
      continue
    if path in used:
      cloudlog.error(f"v4l2d: {path} is recorded for two cameras; not opening {cam.role}")
      continue
    used.add(path)
    # HDR4 on the OX03C10s (2-lane MIPI limits it to ~20 fps); the GC4653
    # stereo pair is always SDR (no HDR hardware, and HDR's temporal
    # misalignment would degrade stereo depth).
    hdr = (_params.get(cam.hdr_param) or "hdr4") if cam.hdr_param else "sdr"
    configs.append(CameraConfig(
      msg_name    = cam.msg_name,
      stream_type = cam.stream_type,
      device_path = path,
      cam_id      = cam.cam_id,
      vipc_server = "v4l2d",
      sensor      = getattr(sensor, cam.sensor_name),
      sensor_name = cam.sensor_name,
      hdr_mode    = hdr,
      fps         = 20,
    ))
  return configs


def load_camera_configs() -> list[CameraConfig]:
  """Load configs from HAL; fall back to defaults."""
  sensor = messaging.log.FrameData.ImageSensor
  stereo_enabled = _params.get_bool("EOPStereoEnabled")
  tele_enabled = _params.get_bool("EOPTeleEnabled")

  # Map HAL stream name → (stream_type, vipc_server, sensor, sensor_name)
  _HAL_STREAM_MAP = {
    "road":          (STREAM_ROAD,         "v4l2d", sensor.ox03c10, "ox03c10"),
    "wide_road":     (STREAM_WIDE_ROAD,    "v4l2d", sensor.ox03c10, "ox03c10"),
    "tele_road":     (STREAM_TELE_ROAD,    "v4l2d", sensor.ox03c10, "ox03c10"),
    "stereo_left":   (STREAM_STEREO_LEFT,  "v4l2d", sensor.gc4653,  "gc4653"),
    "stereo_right":  (STREAM_STEREO_RIGHT, "v4l2d", sensor.gc4653,  "gc4653"),
    "stereo_depth":  (STREAM_STEREO_DEPTH, "v4l2d", sensor.gc4653,  "gc4653"),
  }
  _HAL_MSG_MAP = {
    "road":         "roadCameraState",
    "wide_road":    "wideRoadCameraState",
    "tele_road":    "teleRoadCameraState",
    "stereo_left":  "stereoCameraState",
    "stereo_right": "stereoCameraStateRight",
  }

  try:
    hw_configs = HARDWARE.get_camera_configs()
  except Exception:
    hw_configs = []

  configs: list[CameraConfig] = []
  used_nodes: set[str] = set()

  for cam in hw_configs:
    stream_name = cam.get("stream", cam.get("name", "")).lower()
    if stream_name not in _HAL_STREAM_MAP:
      cloudlog.warning(f"v4l2d: unknown HAL stream '{stream_name}', skipping")
      continue

    if "stereo" in stream_name and not stereo_enabled:
      continue

    if stream_name == "tele_road" and not tele_enabled:
      continue

    stream_type, vipc_server, cam_sensor, sensor_name = _HAL_STREAM_MAP[stream_name]

    # A HAL entry must name its device, or have a confirmed path for its
    # stream; never discover by sensor name (02M has three OX03C10s).
    device_path = cam.get("device") or next(iter(_mipi_paths(stream_name, [])), None)
    if not device_path or device_path in used_nodes:
      cloudlog.error(f"v4l2d: HAL camera '{stream_name}' has no unique device path; not opening it")
      continue

    used_nodes.add(device_path)

    configs.append(CameraConfig(
      msg_name    = _HAL_MSG_MAP.get(stream_name, "roadCameraState"),
      stream_type = stream_type,
      device_path = device_path,
      cam_id      = cam.get("id", stream_name + "_camera"),
      vipc_server = vipc_server,
      sensor      = cam_sensor,
      sensor_name = sensor_name,
      hdr_mode    = cam.get("hdr_mode", "hdr4" if "stereo" not in stream_name else "sdr"),
      fps         = cam.get("fps", 20),
    ))

  if not configs:
    try:
      device_type = HARDWARE.get_device_type()
    except Exception:
      device_type = None
    configs = _default_camera_configs(device_type)
  return configs


# ---------------------------------------------------------------------------
# Per-camera runtime state
# ---------------------------------------------------------------------------
@dataclass
class CameraState:
  config: CameraConfig
  camera: object | None = None
  generator: Generator | None = None
  frame_id: int = 0
  buffers_created: bool = False
  restart_time: float = 0.0
  healthy: bool = False
  isp_hal: object | None = None  # Hardware ISP for 3A

  @property
  def health_param(self) -> str:
    parts = self.config.cam_id.replace("-", "_").split("_")
    return "".join(p.capitalize() for p in parts if p) + "Ready"

  def init_isp(self, device_path: str):
    """Initialize ISP HAL for hardware 3A (AE/AWB) via Rockchip RKIAQ."""
    sensor_name = self._get_sensor_name()
    if not sensor_name:
      cloudlog.warning(
        f"v4l2d: skipping ISP init for {self.config.cam_id} — unknown sensor"
      )
      self.isp_hal = None
      return

    try:
      from openpilot.system.v4l2d.isp.rkiaq_wrapper import RKIAQWrapper
      isp = RKIAQWrapper(device_path, sensor_name)
      if not isp.initialize():
        cloudlog.warning(
          f"v4l2d: RKIAQ init failed for {self.config.cam_id} — " +
          "falling back to sensor default AE/AWB"
        )
        self.isp_hal = None
        return

      if not isp.start():
        cloudlog.warning(f"v4l2d: RKIAQ start failed for {self.config.cam_id}")
        isp.shutdown()
        self.isp_hal = None
        return

      # Default AE: 100 µs – 33 ms, 0–24 dB gain, target 128/255.
      # These are safe starting points for ADAS day/night operation;
      # per-sensor tuning is applied via the IQ file loaded from /etc/iqfiles.
      isp.set_ae(
        target_brightness=128,
        exposure_range_us=(100, 33000),
        gain_range_db=(0.0, 24.0),
      )
      isp.set_awb("auto")

      self.isp_hal = isp
      cloudlog.info(
        f"v4l2d: RKIAQ ISP ready for {self.config.cam_id} ({sensor_name})"
      )
    except Exception as e:
      cloudlog.warning(f"v4l2d: ISP init failed for {self.config.cam_id}: {e}")
      self.isp_hal = None

  def _get_sensor_name(self) -> str:
    """Extract sensor name from config."""
    sensor_map = {
      "ox03c10": "OX03C10",
      "gc4653": "GC4653",
      "imx415": "IMX415",
    }
    cam_id_lower = self.config.cam_id.lower()
    for key, name in sensor_map.items():
      if key in cam_id_lower:
        return name
    # Try to infer from sensor enum
    sensor_str = str(self.config.sensor).lower()
    for key, name in sensor_map.items():
      if key in sensor_str:
        return name
    return ""


# ---------------------------------------------------------------------------
# V4L2 Daemon
# ---------------------------------------------------------------------------
class V4L2D:
  """V4L2 Camera Daemon — road, wide_road, tele_road, stereo_left, stereo_right.

  Safety critical ADAS input pipeline - runs on A76 big cores.
  """

  def __init__(self):
    # Camera capture is safety critical (ADAS input) - A76 cores
    set_daemon_affinity("v4l2d")

    self.params = Params()
    self.camera_configs = load_camera_configs()
    self._stop_event = threading.Event()

    cloudlog.info("v4l2d: cameras loaded: %s",
                  [c.cam_id for c in self.camera_configs])

    # cereal PubMaster — one entry per unique msg_name
    msg_names = list({c.msg_name for c in self.camera_configs})
    self.pm = messaging.PubMaster(msg_names)

    # VisionIPC servers — one per unique server name
    server_names = list({c.vipc_server for c in self.camera_configs})
    self._vipc: dict[str, VisionIpcServer] = {
      name: VisionIpcServer(name) for name in server_names
    }

    # Occlusion detector per camera (lazy init)
    self._occlusion_detectors: dict[str, OcclusionDetector] = {}

    # Camera states and initial open
    self.camera_states: list[CameraState] = [
      CameraState(config=cfg) for cfg in self.camera_configs
    ]
    for state in self.camera_states:
      self._ensure_camera(state, initial=True)

    # Start VisionIPC listeners
    for srv in self._vipc.values():
      srv.start_listener()

    ready = sum(1 for s in self.camera_states if s.camera is not None)
    cloudlog.info("v4l2d: %d/%d cameras ready", ready, len(self.camera_states))

  # ---- health ---------------------------------------------------------------

  def _set_health(self, state: CameraState, healthy: bool) -> None:
    if state.healthy == healthy:
      return
    state.healthy = healthy
    try:
      self.params.put_bool(state.health_param, healthy)
    except Exception:
      pass

  # ---- camera lifecycle -----------------------------------------------------

  def _release_camera(self, state: CameraState) -> None:
    if state.camera is not None:
      try:
        if hasattr(state.camera, "close"):
          state.camera.close()
      except Exception:
        pass
    state.camera = None
    state.generator = None

    # Stop ISP but keep the reference for restart
    if state.isp_hal is not None:
      try:
        state.isp_hal.stop()
      except Exception:
        pass

  def _ensure_camera(self, state: CameraState, initial: bool = False) -> bool:
    if state.camera is not None and state.generator is not None:
      return True
    now = time.monotonic()
    if not initial and now < state.restart_time:
      return False
    try:
      hal = HARDWARE.get_camera_hal()
      camera = hal.open_camera(
        device_path=state.config.device_path,
        stream_type=state.config.stream_type,
        cam_id=state.config.cam_id,
        sensor_name=state.config.sensor_name,
      )
      if not state.buffers_created:
        self._vipc[state.config.vipc_server].create_buffers(
          state.config.stream_type, 4, camera.width, camera.height
        )
        state.buffers_created = True
      state.camera = camera
      state.generator = camera.read_frames()
      state.restart_time = now

      # Initialize ISP for hardware 3A
      if state.isp_hal is None:
        state.init_isp(state.config.device_path)

      self._set_health(state, True)
      cloudlog.info("v4l2d: %s opened (%dx%d)", state.config.cam_id, camera.width, camera.height)
      return True
    except Exception as e:
      cloudlog.error("v4l2d: %s open failed: %s", state.config.cam_id, e)
      self._set_health(state, False)
      state.restart_time = now + CAMERA_RESTART_COOLDOWN_SEC
      self._release_camera(state)
      return False

  def _get_frame(self, state: CameraState) -> object | None:
    if state.camera is None or state.generator is None:
      return None
    try:
      frame_obj = next(state.generator)
      self._set_health(state, True)
      return frame_obj
    except StopIteration:
      cloudlog.warning("v4l2d: %s generator exhausted", state.config.cam_id)
    except Exception as e:
      cloudlog.warning("v4l2d: %s capture error: %s", state.config.cam_id, e)
    self._set_health(state, False)
    self._release_camera(state)
    state.restart_time = time.monotonic() + CAMERA_RESTART_COOLDOWN_SEC
    return None

  # ---- frame send (identical for all cameras) -------------------------------

  def _send_frame(self, frame_obj: object, state: CameraState) -> None:
    ts = int(time.monotonic() * 1e9)

    try:
      self._vipc[state.config.vipc_server].send(
        state.config.stream_type,
        frame_obj.data,
        state.frame_id, ts, ts,
      )
    except Exception as e:
      cloudlog.error("v4l2d: %s VisionIPC send error: %s", state.config.cam_id, e)
      return

    try:
      # Prefer RKIAQ ISP metadata when available; otherwise fall back to the
      # V4L2 driver-reported values from the frame object.
      exposure_us = frame_obj.exposure_time_us
      gain = frame_obj.analog_gain
      if state.isp_hal is not None:
        try:
          meta = state.isp_hal.get_metadata()
          if meta.exposure_time_us > 0:
            exposure_us = meta.exposure_time_us
          if meta.analog_gain > 0:
            gain = meta.analog_gain
        except Exception:
          pass

      dat = messaging.new_message(state.config.msg_name, valid=True)
      msg = getattr(dat, state.config.msg_name)
      msg.frameId = state.frame_id
      msg.timestampSof = ts
      msg.timestampEof = ts
      msg.exposureTime = exposure_us
      msg.gain = gain
      msg.exposureValPercent = (1.0 - frame_obj.light_level) * 100.0
      msg.sensor = state.config.sensor
      msg.transform = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
      self.pm.send(state.config.msg_name, dat)
    except Exception as e:
      cloudlog.error("v4l2d: %s cereal send error: %s", state.config.cam_id, e)

  # ---- per-camera thread (identical for all cameras) -----------------------

  def _check_occlusion(self, state: CameraState, frame_obj) -> bool:
    """Run occlusion detection on frame. Returns True if healthy."""
    try:
      cam_id = state.config.cam_id
      if cam_id not in self._occlusion_detectors:
        det = OcclusionDetector(frame_obj.width, frame_obj.height)
        if det.available:
          self._occlusion_detectors[cam_id] = det
          cloudlog.info("v4l2d: occlusion detector ready for %s", cam_id)
        else:
          return True  # library unavailable — assume healthy

      det = self._occlusion_detectors[cam_id]
      # NV12: first height rows are Y plane
      if len(frame_obj.data.shape) == 2 and frame_obj.data.shape[0] >= int(frame_obj.height * 1.5):
        y_plane = frame_obj.data[:frame_obj.height, :frame_obj.width].tobytes()
      else:
        return True  # unsupported format — assume healthy

      roi = OcclusionROI(0, 0, frame_obj.height - 1, frame_obj.width - 1)
      results = det.detect(y_plane, [roi])
      if results and results[0].occluded:
        cloudlog.warning("v4l2d: %s occlusion detected", cam_id)
        return False
    except Exception as e:
      cloudlog.error("v4l2d: occlusion check error for %s: %s", state.config.cam_id, e)
    return True

  def camera_runner(self, state: CameraState) -> None:
    cloudlog.info("v4l2d: %s capture thread started", state.config.cam_id)
    rk: Ratekeeper | None = None
    occlusion_check_interval = 5  # frames (~4 Hz at 20 fps)
    frame_counter = 0

    try:
      while not self._stop_event.is_set():
        if not self._ensure_camera(state):
          time.sleep(0.1)
          continue

        if rk is None:
          rk = Ratekeeper(20, None)

        frame_obj = self._get_frame(state)
        if frame_obj is None:
          rk = None
          time.sleep(0.05)
          continue

        try:
          # Occlusion detection (O-10)
          frame_counter += 1
          if frame_counter % occlusion_check_interval == 0:
            if not self._check_occlusion(state, frame_obj):
              self._set_health(state, False)
            else:
              self._set_health(state, True)

          self._send_frame(frame_obj, state)
          state.frame_id += 1
          if rk is not None:
            rk.keep_time()
          if state.frame_id % 200 == 0:
            cloudlog.debug("v4l2d: %s frame %d", state.config.cam_id, state.frame_id)
        except Exception as e:
          cloudlog.error("v4l2d: %s frame error: %s", state.config.cam_id, e)
          rk = None
          self._set_health(state, False)
          self._release_camera(state)
          state.restart_time = time.monotonic() + CAMERA_RESTART_COOLDOWN_SEC
          time.sleep(0.05)
    finally:
      cloudlog.info("v4l2d: %s stopped (%d frames)", state.config.cam_id, state.frame_id)
      if state.config.cam_id in self._occlusion_detectors:
        self._occlusion_detectors[state.config.cam_id].close()
        del self._occlusion_detectors[state.config.cam_id]
      self._release_camera(state)
      self._set_health(state, False)

  # ---- main run loop --------------------------------------------------------

  def run(self) -> int:
    threads: list[tuple[CameraState, threading.Thread]] = []
    for state in self.camera_states:
      t = threading.Thread(
        target=self.camera_runner,
        args=(state,),
        daemon=True,
        name=f"cam_{state.config.cam_id}",
      )
      t.start()
      threads.append((state, t))
      cloudlog.info("v4l2d: started thread %s", state.config.cam_id)

    if not threads:
      cloudlog.error("v4l2d: no camera threads started — exiting")
      return 1

    try:
      while not self._stop_event.is_set():
        time.sleep(5)
        for idx, (state, t) in enumerate(list(threads)):
          if not t.is_alive():
            cloudlog.warning("v4l2d: %s thread died; restarting", state.config.cam_id)
            new_t = threading.Thread(
              target=self.camera_runner,
              args=(state,),
              daemon=True,
              name=f"cam_{state.config.cam_id}",
            )
            new_t.start()
            threads[idx] = (state, new_t)
    except KeyboardInterrupt:
      pass
    finally:
      cloudlog.info("v4l2d: shutting down")
      self._stop_event.set()
      for _state, t in threads:
        t.join(timeout=2.0)
    return 0


def main() -> int:
  try:
    device_type = HARDWARE.get_device_type()
    cloudlog.info("v4l2d: running on %s", device_type)
  except Exception as e:
    cloudlog.warning("v4l2d: could not detect hardware: %s", e)
    device_type = None

  # This daemon's camera list (_default_camera_configs) hardcodes one board's
  # MIPI array and device-path candidates. On any other board, silently
  # proceeding would open whatever /dev/videoN nodes happen to exist and
  # mislabel them as road/wide_road/stereo_left/stereo_right, publishing
  # wrong camera identities on the VisionIPC bus rather than failing
  # visibly. Refuse to guess.
  if device_type not in SUPPORTED_DEVICE_TYPES:
    cloudlog.error(
      "v4l2d: platform '%s' is not supported by this daemon's hardcoded "
      + "camera array (supported: %s) -- refusing to start rather than open "
      + "the wrong devices.", device_type, SUPPORTED_DEVICE_TYPES)
    return 1

  try:
    return V4L2D().run()
  except Exception as e:
    cloudlog.exception(f"v4l2d fatal error: {e}")
    return 1


if __name__ == "__main__":
  exit(main())
