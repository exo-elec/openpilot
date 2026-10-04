#!/usr/bin/env python3
"""monod on comma 3 / 3X and clones: road-camera YOLO detections, ranged (sensing only, untracked), published as `monoDetections`.

NGP10 base only: EOP10/01M/02M do not register this process (their RKNN `selfdrive.monod` publishes `monoDetections`; one publisher per service).
Publish-only and default off (`ngp_monod_enabled`): nothing in controls reads `monoDetections` yet.
Needs a detector ONNX (YOLOv8-style head) compiled by SCons to `models/yolo_detector_tinygrad.pkl`;
without it the process idles. The weights are not part of this repo (check their licence).

Parts:
  MonoPipeline     SENSING: boxes -> ranged detections (`detect`); `step` adds the tracker for tests/tools only
  fill_detections  tracks -> MonoDetections builder (same schema as EOP10)
  TinygradYolo     the model behind an interface (device only, not tested here)
  main             VisionIPC road stream at `ngp_monod_hz`, liveCalibration, radarState leads; publishes untracked detections
                   (tracking, velocity and the fused-object message belong to `gridd`: runtime/gridd.py)
"""
import math
import os
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from nagaspilot.controls.ngp_detect import Box, Letterbox, decode
from nagaspilot.controls.ngp_object_tracker import Measurement, ObjectTracker, Track, measurement_sigma
from nagaspilot.controls.ngp_ranging import LeadAnchoredRanger, RoadCamera

MODEL_SIZE = 640
MODEL_PKL = Path(__file__).resolve().parents[2] / 'selfdrive/modeld/models/yolo_detector_tinygrad.pkl'
INPUT_NAME = os.getenv('MONOD_INPUT_NAME', 'images')
MIN_HZ, MAX_HZ = 1, 10


def vanishing_point(view_from_calib: np.ndarray, K: np.ndarray) -> tuple[float, float]:
  """Pixel (u, v) of the point at infinity straight ahead: vanishing column, horizon row.

  `view_from_calib` is the 3x3 rotation from the calibrated frame (x forward) to the camera view frame.
  """
  d = K @ view_from_calib[:, 0]
  return float(d[0] / d[2]), float(d[1] / d[2])


class MonoPipeline:
  def __init__(self, K: np.ndarray, img_w: int, img_h: int, cam_height: float = 1.22):
    self.K, self.img_w, self.img_h = K, img_w, img_h
    self.ranger = LeadAnchoredRanger(RoadCamera(focal=float(K[0, 0]), cx=float(K[0, 2]), cy=float(K[1, 2]), height_m=cam_height))
    self.tracker = ObjectTracker()
    self.lb = Letterbox.fit(img_w, img_h, MODEL_SIZE)

  def detect(self, boxes: list[Box], leads: list[tuple[float, float]], view_from_calib: np.ndarray,
             cam_height: float | None = None) -> list[Measurement]:
    """SENSING: boxes -> metric detections (ranging + lead-anchored scale). No tracking: that is gridd's job.

    `leads` = [(dRel, yRel)] from radarState, yRel left positive."""
    if cam_height:
      self.ranger.cam = replace(self.ranger.cam, height_m=float(cam_height))
    vp_u, horizon_v = vanishing_point(view_from_calib, self.K)
    ranged = []
    for b in boxes:
      clipped = b.y2 >= self.img_h - 2
      r = self.ranger.range_box(b.name, b.cx, b.y2, b.h, horizon_v, vp_u, clipped)
      if r is not None:
        ranged.append((b, r))
    self.ranger.observe_leads([(b.name, r) for b, r in ranged], leads)
    meas = []
    for b, r in ranged:
      sx, sy = measurement_sigma(r.x)
      # re-range after the scale update so this frame already uses it
      x = r.raw_x * self.ranger.scale(b.name)
      y = r.y * (x / r.x) if r.x else r.y
      meas.append(Measurement(b.name, x, y, sx, sy, b.conf,
                              (b.cx / self.img_w, 0.5 * (b.y1 + b.y2) / self.img_h, b.w / self.img_w, b.h / self.img_h)))
    return meas

  def step(self, boxes: list[Box], leads: list[tuple[float, float]], view_from_calib: np.ndarray,
           dt: float, cam_height: float | None = None) -> list[Track]:
    """detect + track in one call (used by tests and offline tools; the daemon publishes `detect` and gridd tracks)."""
    return self.tracker.update(self.detect(boxes, leads, view_from_calib, cam_height), dt)


def fill_raw_detections(md, meas: list[Measurement], frame_id: int, timestamp_s: float, exec_time: float) -> None:
  """Fill a MonoDetections builder with UNTRACKED detections: position, class, confidence, box, sigma; no id, no velocity."""
  md.frameId = frame_id
  md.timestamp = timestamp_s
  md.numTracks = len(meas)
  md.modelExecutionTime = exec_time
  dets = md.init('detections', len(meas))
  for i, m in enumerate(meas):
    d = dets[i]
    d.trackId = 0
    d.className = m.name
    d.confidence = float(m.conf)
    d.cameraSource = 'road'
    d.u, d.v, d.w, d.h = (float(v) for v in m.box)
    d.x, d.y = float(m.x), float(m.y)
    d.distance = float(math.hypot(m.x, m.y))
    d.sigmaX, d.sigmaY = float(m.sigma_x), float(m.sigma_y)


def fill_detections(md, tracks: list[Track], frame_id: int, timestamp_s: float, exec_time: float) -> None:
  """Fill a MonoDetections builder. Occluded (coasting) tracks go out with confidence 0 and a growing sigma."""
  md.frameId = frame_id
  md.timestamp = timestamp_s
  md.numTracks = len(tracks)
  md.modelExecutionTime = exec_time
  dets = md.init('detections', len(tracks))
  for i, t in enumerate(tracks):
    d = dets[i]
    d.trackId = t.track_id
    d.className = t.name
    d.confidence = 0.0 if t.occluded else float(t.conf)
    d.cameraSource = 'road'
    d.u, d.v, d.w, d.h = (float(v) for v in t.box)
    d.x, d.y, d.vx, d.vy = t.x, t.y, t.vx, t.vy   # y left positive (yRel convention)
    d.distance = float(math.hypot(t.x, t.y))
    d.sigmaX, d.sigmaY = t.sigma_x, t.sigma_y


class TinygradYolo:
  """Compiled detector. Not exercised off-device. Input: RGB 640x640 uint8 -> float [0,1], NCHW."""

  def __init__(self, pkl: Path = MODEL_PKL):
    import pickle
    from tinygrad.tensor import Tensor
    self._Tensor = Tensor
    with open(pkl, 'rb') as f:
      self.run = pickle.load(f)

  def __call__(self, rgb640: np.ndarray) -> np.ndarray:
    x = (rgb640.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
    out = self.run(**{INPUT_NAME: self._Tensor(x, device='NPY').realize()})
    return out.contiguous().realize().uop.base.buffer.numpy()


def nv12_to_rgb(buf) -> np.ndarray:
  import cv2
  data = np.asarray(buf.data)
  y = data[:buf.stride * buf.height].reshape(buf.height, buf.stride)[:, :buf.width]
  uv = data[buf.uv_offset:buf.uv_offset + buf.stride * (buf.height // 2)].reshape(buf.height // 2, buf.stride)[:, :buf.width]
  return cv2.cvtColor(np.vstack([y, uv]), cv2.COLOR_YUV2RGB_NV12)


def letterbox_image(rgb: np.ndarray, lb: Letterbox) -> np.ndarray:
  import cv2
  h, w = rgb.shape[:2]
  img = cv2.resize(rgb, (round(w * lb.scale), round(h * lb.scale)))
  out = np.full((MODEL_SIZE, MODEL_SIZE, 3), 114, dtype=np.uint8)
  x0, y0 = round(lb.pad_x), round(lb.pad_y)
  out[y0:y0 + img.shape[0], x0:x0 + img.shape[1]] = img
  return out


def main():
  from cereal import messaging
  from cereal.messaging import PubMaster, SubMaster
  from msgq.visionipc import VisionIpcClient, VisionStreamType
  from openpilot.common.params import Params
  from openpilot.common.swaglog import cloudlog
  from openpilot.common.transformations.camera import DEVICE_CAMERAS, get_view_frame_from_calib_frame
  from openpilot.system.hardware import HARDWARE

  params = Params()
  if not MODEL_PKL.exists():
    cloudlog.warning("monod: no compiled detector, idling")
    while True:
      time.sleep(5)

  model = TinygradYolo()
  vipc = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_ROAD, True)
  while not vipc.connect(False):
    time.sleep(0.1)

  sm = SubMaster(["liveCalibration", "radarState", "roadCameraState"])
  pm = PubMaster(["monoDetections"])
  pipe: MonoPipeline | None = None
  last_t = time.monotonic()

  while True:
    buf = vipc.recv()
    if buf is None:
      continue
    hz = min(max(params.get("ngp_monod_hz") or 5, MIN_HZ), MAX_HZ)
    now = time.monotonic()
    if now - last_t < 1.0 / hz:
      continue
    dt, last_t = now - last_t, now

    sm.update(0)
    if not sm.seen["liveCalibration"] or not sm["liveCalibration"].rpyCalib:
      continue
    if pipe is None:
      cam = DEVICE_CAMERAS[(HARDWARE.get_device_type(), str(sm["roadCameraState"].sensor) if sm.seen["roadCameraState"] else "unknown")].fcam
      scale = buf.width / cam.width
      K = cam.intrinsics.copy()
      K[:2] *= scale
      pipe = MonoPipeline(K, buf.width, buf.height)

    rgb = nv12_to_rgb(buf)
    t0 = time.perf_counter()
    raw = model(letterbox_image(rgb, pipe.lb))
    exec_time = time.perf_counter() - t0

    calib = sm["liveCalibration"]
    rpy = np.array(calib.rpyCalib, dtype=np.float64)
    view_from_calib = get_view_frame_from_calib_frame(rpy[0], rpy[1], rpy[2], 0.0)[:, :3]
    leads = [(float(l.dRel), float(l.yRel)) for l in (sm["radarState"].leadOne, sm["radarState"].leadTwo) if l.status]
    height = float(calib.height[0]) if len(calib.height) else None
    meas = pipe.detect(decode(raw, pipe.lb, buf.width, buf.height), leads, view_from_calib, height)

    msg = messaging.new_message('monoDetections', valid=True)
    fill_raw_detections(msg.monoDetections, meas, vipc.frame_id, vipc.timestamp_sof * 1e-9, exec_time)
    pm.send('monoDetections', msg)


if __name__ == "__main__":
  main()
