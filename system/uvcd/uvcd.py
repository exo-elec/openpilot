#!/usr/bin/env python3
"""
UVC Camera Daemon — USB/UVC cameras (side cameras + rear camera).

ExoPilot 01M (RK3588): side_left / side_right / rear via USB 3.0 hub (RTS5411S).

Capture:
  - Pure V4L2 via OpenCV (CAP_V4L2).  No I2C, no ISP, no HDR.
  - Side cameras default 1280×720; rear camera defaults 1280×720 (720p AHD).
  - Framerate: 20 Hz to match the openpilot pipeline.

Outputs:
  - VisionIPC server "uvcd" (BGR buffers — 3 bytes / pixel)
  - cereal leftCameraState / rightCameraState / rearCameraState
"""

import os
import threading
import time
import typing
from collections import namedtuple
from dataclasses import dataclass

import numpy as np


# ---------------------------------------------------------------------------
# BGR → NV12 conversion (for rear camera to render in CameraWidget)
# ---------------------------------------------------------------------------
def _bgr_to_nv12(bgr: np.ndarray) -> np.ndarray:
    """Convert BGR (H, W, 3) to NV12 (H * 3 // 2, W) uint8."""
    h, w = bgr.shape[:2]
    b = bgr[:, :, 0].astype(np.float32)
    g = bgr[:, :, 1].astype(np.float32)
    r = bgr[:, :, 2].astype(np.float32)

    y = (0.299 * r + 0.587 * g + 0.114 * b).clip(0, 255).astype(np.uint8)
    u = (-0.169 * r - 0.331 * g + 0.500 * b + 128.0).clip(0, 255).astype(np.uint8)
    v = (0.500 * r - 0.419 * g - 0.081 * b + 128.0).clip(0, 255).astype(np.uint8)

    # 2×2 subsample + interleave UV
    u_ds = u[::2, ::2]
    v_ds = v[::2, ::2]
    uv = np.empty((h // 2, w), dtype=np.uint8)
    uv[:, 0::2] = u_ds
    uv[:, 1::2] = v_ds

    return np.vstack([y, uv])

from msgq.visionipc import VisionIpcServer
from cereal import messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.swaglog import cloudlog
from openpilot.common.params import Params
from openpilot.system.hardware import HARDWARE
from openpilot.system.v4l2d.drivers.base import BaseCameraDriver, CameraFrame

_params = Params()

# ---------------------------------------------------------------------------
# VisionIPC stream IDs
# ---------------------------------------------------------------------------
from msgq.visionipc import VisionStreamType
STREAM_REAR        = VisionStreamType.VISION_STREAM_REAR
STREAM_SIDE_LEFT   = VisionStreamType.VISION_STREAM_SIDE_LEFT
STREAM_SIDE_RIGHT  = VisionStreamType.VISION_STREAM_SIDE_RIGHT

# ---------------------------------------------------------------------------
# CameraConfig
# ---------------------------------------------------------------------------
CameraConfig = namedtuple("CameraConfig", [
  "msg_name",
  "stream_type",
  "device_path",
  "cam_id",
  "width",
  "height",
  "fps",
  "sensor_name",
])

CAMERA_RESTART_COOLDOWN_SEC = float(os.getenv("UVC_RESTART_COOLDOWN_SEC", "1.0"))

SIDE_LEFT_DEVICES = ["/dev/video-side-left", "/dev/video10", "/dev/video20"]
SIDE_RIGHT_DEVICES = ["/dev/video-side-right", "/dev/video11", "/dev/video21"]
REAR_CAMERA_DEVICES = ["/dev/video-rear", "/dev/video12", "/dev/video30",
                       "/dev/video13", "/dev/video14"]


def _find_device(candidates: list[str]) -> str:
  for p in candidates:
    if os.path.exists(p):
      return p
  return candidates[0]


def _load_side_camera_resolution() -> tuple[int, int]:
  """Read resolution from params; default 1280×720 (typical 720p)."""
  try:
    w = int(_params.get("EOPSideCameraWidth") or "1280")
    h = int(_params.get("EOPSideCameraHeight") or "720")
    return w, h
  except Exception:
    return 1280, 720


def _load_rear_camera_resolution() -> tuple[int, int]:
  """Read resolution from params; default 1280×720 (720p AHD, matches camera_config.py)."""
  try:
    w = int(_params.get("EOPRearCameraWidth") or "1280")
    h = int(_params.get("EOPRearCameraHeight") or "720")
    return w, h
  except Exception:
    return 1280, 720


def _default_camera_configs() -> list[CameraConfig]:
  configs: list[CameraConfig] = []

  # Rear camera (enabled on platforms that report has_rear_camera)
  if HARDWARE.has_rear_camera() and _params.get_bool("EOPRearCameraEnabled"):
    rw, rh = _load_rear_camera_resolution()
    configs.append(CameraConfig(
      msg_name    = "rearCameraState",
      stream_type = STREAM_REAR,
      device_path = _find_device(REAR_CAMERA_DEVICES),
      cam_id      = "rear_camera",
      width       = rw,
      height      = rh,
      fps         = 20,
      sensor_name = "uvc",
    ))

  # Side cameras (enabled on platforms that report has_side_cameras)
  if HARDWARE.has_side_cameras() and _params.get_bool("EOPSideCamerasEnabled"):
    sw, sh = _load_side_camera_resolution()
    configs.extend([
      CameraConfig(
        msg_name    = "leftCameraState",
        stream_type = STREAM_SIDE_LEFT,
        device_path = _find_device(SIDE_LEFT_DEVICES),
        cam_id      = "side_left_camera",
        width       = sw,
        height      = sh,
        fps         = 20,
        sensor_name = "uvc",
      ),
      CameraConfig(
        msg_name    = "rightCameraState",
        stream_type = STREAM_SIDE_RIGHT,
        device_path = _find_device(SIDE_RIGHT_DEVICES),
        cam_id      = "side_right_camera",
        width       = sw,
        height      = sh,
        fps         = 20,
        sensor_name = "uvc",
      ),
    ])

  return configs


# ---------------------------------------------------------------------------
# Per-camera runtime state
# ---------------------------------------------------------------------------
@dataclass
class CameraState:
  config: CameraConfig
  driver: BaseCameraDriver | None = None
  generator: typing.Generator[CameraFrame, None, None] | None = None
  frame_id: int = 0
  buffers_created: bool = False
  restart_time: float = 0.0
  healthy: bool = False

  @property
  def health_param(self) -> str:
    parts = self.config.cam_id.replace("-", "_").split("_")
    return "".join(p.capitalize() for p in parts if p) + "Ready"


# ---------------------------------------------------------------------------
# UVC Daemon
# ---------------------------------------------------------------------------
class UVCD:

  def __init__(self):
    set_daemon_affinity("uvcd")

    self.params = Params()
    self.camera_configs = _default_camera_configs()
    self._stop_event = threading.Event()

    cloudlog.info("uvcd: cameras loaded: %s",
                  [c.cam_id for c in self.camera_configs])

    msg_names = list({c.msg_name for c in self.camera_configs})
    self.pm = messaging.PubMaster(msg_names)

    self._vipc = VisionIpcServer("uvcd")

    self.camera_states: list[CameraState] = [
      CameraState(config=cfg) for cfg in self.camera_configs
    ]
    for state in self.camera_states:
      self._ensure_camera(state, initial=True)

    self._vipc.start_listener()

    ready = sum(1 for s in self.camera_states if s.driver is not None)
    cloudlog.info("uvcd: %d/%d cameras ready", ready, len(self.camera_states))

  def _set_health(self, state: CameraState, healthy: bool) -> None:
    if state.healthy == healthy:
      return
    state.healthy = healthy
    try:
      self.params.put_bool(state.health_param, healthy)
    except Exception:
      pass

  def _release_camera(self, state: CameraState) -> None:
    if state.driver is not None:
      try:
        state.driver.close()
      except Exception:
        pass
    state.driver = None
    state.generator = None

  def _ensure_camera(self, state: CameraState, initial: bool = False) -> bool:
    if state.driver is not None and state.driver.is_open:
      return True
    now = time.monotonic()
    if not initial and now < state.restart_time:
      return False
    try:
      cfg = state.config
      hal = HARDWARE.get_camera_hal()
      driver = hal.open_camera(
        device_path=cfg.device_path,
        stream_type=cfg.stream_type,
        cam_id=cfg.cam_id,
        sensor_name=cfg.sensor_name,
        width=cfg.width,
        height=cfg.height,
        fps=cfg.fps,
        fourcc="MJPG",
      )

      if not state.buffers_created:
        if cfg.stream_type == STREAM_REAR:
          # Rear camera: convert BGR → NV12 so CameraWidget can render it
          self._vipc.create_buffers_with_sizes(
            cfg.stream_type, 4, driver.width, driver.height,
            size=driver.width * driver.height * 3 // 2,
            stride=driver.width,
            uv_offset=driver.width * driver.height,
          )
        else:
          # Side cameras: keep BGR for the onroad PIP overlay's QPainter rendering
          self._vipc.create_buffers_with_sizes(
            cfg.stream_type, 4, driver.width, driver.height,
            size=driver.width * driver.height * 3,
            stride=driver.width * 3,
            uv_offset=0,
          )
        state.buffers_created = True

      state.driver = driver
      state.generator = driver.read_frames()
      state.restart_time = now
      self._set_health(state, True)
      cloudlog.info("uvcd: %s opened (%dx%d)", cfg.cam_id, driver.width, driver.height)
      return True
    except Exception as e:
      cloudlog.error("uvcd: %s open failed: %s", state.config.cam_id, e)
      self._set_health(state, False)
      state.restart_time = now + CAMERA_RESTART_COOLDOWN_SEC
      self._release_camera(state)
      return False

  def _get_frame(self, state: CameraState) -> np.ndarray | None:
    if state.generator is None:
      return None
    try:
      frame = next(state.generator)
      if frame is None or frame.data is None:
        return None
      self._set_health(state, True)
      return frame.data
    except StopIteration:
      cloudlog.warning("uvcd: %s generator exhausted", state.config.cam_id)
    except Exception as e:
      cloudlog.warning("uvcd: %s capture error: %s", state.config.cam_id, e)
    self._set_health(state, False)
    self._release_camera(state)
    state.restart_time = time.monotonic() + CAMERA_RESTART_COOLDOWN_SEC
    return None

  def _send_frame(self, frame: np.ndarray, state: CameraState) -> None:
    ts = int(time.monotonic() * 1e9)

    # Convert rear camera BGR → NV12 for CameraWidget rendering
    if state.config.stream_type == STREAM_REAR:
      frame = _bgr_to_nv12(frame)

    try:
      self._vipc.send(
        state.config.stream_type,
        frame,
        state.frame_id, ts, ts,
      )
    except Exception as e:
      cloudlog.error("uvcd: %s VisionIPC send error: %s", state.config.cam_id, e)
      return

    try:
      dat = messaging.new_message(state.config.msg_name, valid=True)
      sensor = messaging.log.FrameData.ImageSensor
      msg = getattr(dat, state.config.msg_name)
      msg.frameId = state.frame_id
      msg.timestampSof = ts
      msg.timestampEof = ts
      msg.exposureTime = 0.0
      msg.gain = 0.0
      msg.exposureValPercent = 50.0
      msg.sensor = sensor.unknown
      msg.transform = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
      self.pm.send(state.config.msg_name, dat)
    except Exception as e:
      cloudlog.error("uvcd: %s cereal send error: %s", state.config.cam_id, e)

  def camera_runner(self, state: CameraState) -> None:
    cloudlog.info("uvcd: %s capture thread started", state.config.cam_id)
    rk: Ratekeeper | None = None

    try:
      while not self._stop_event.is_set():
        if not self._ensure_camera(state):
          time.sleep(0.1)
          continue

        if rk is None:
          rk = Ratekeeper(state.config.fps, None)

        frame = self._get_frame(state)
        if frame is None:
          rk = None
          time.sleep(0.05)
          continue

        try:
          self._send_frame(frame, state)
          state.frame_id += 1
          if rk is not None:
            rk.keep_time()
          if state.frame_id % 200 == 0:
            cloudlog.debug("uvcd: %s frame %d", state.config.cam_id, state.frame_id)
        except Exception as e:
          cloudlog.error("uvcd: %s frame error: %s", state.config.cam_id, e)
          rk = None
          self._set_health(state, False)
          self._release_camera(state)
          state.restart_time = time.monotonic() + CAMERA_RESTART_COOLDOWN_SEC
          time.sleep(0.05)
    finally:
      cloudlog.info("uvcd: %s stopped (%d frames)", state.config.cam_id, state.frame_id)
      self._release_camera(state)
      self._set_health(state, False)

  def run(self) -> int:
    threads: list[tuple[CameraState, threading.Thread]] = []
    for state in self.camera_states:
      t = threading.Thread(
        target=self.camera_runner,
        args=(state,),
        daemon=True,
        name=f"uvc_{state.config.cam_id}",
      )
      t.start()
      threads.append((state, t))
      cloudlog.info("uvcd: started thread %s", state.config.cam_id)

    if not threads:
      cloudlog.error("uvcd: no camera threads started — exiting")
      return 1

    try:
      while not self._stop_event.is_set():
        time.sleep(5)
        for idx, (state, t) in enumerate(list(threads)):
          if not t.is_alive():
            cloudlog.warning("uvcd: %s thread died; restarting", state.config.cam_id)
            new_t = threading.Thread(
              target=self.camera_runner,
              args=(state,),
              daemon=True,
              name=f"uvc_{state.config.cam_id}",
            )
            new_t.start()
            threads[idx] = (state, new_t)
    except KeyboardInterrupt:
      pass
    finally:
      cloudlog.info("uvcd: shutting down")
      self._stop_event.set()
      for _state, t in threads:
        t.join(timeout=2.0)
    return 0


def main() -> int:
  try:
    return UVCD().run()
  except Exception as e:
    cloudlog.exception(f"UVCD fatal error: {e}")
    return 1


if __name__ == "__main__":
  exit(main())
