#!/usr/bin/env python3
"""
reard.py — Rear Camera Perception Daemon (AHD → UVC)

Pure camera RCTA (Rear Cross Traffic Alert) for ExoPilot 01M/02.
Consumes BGR frames from uvcd VisionIPC (VISION_STREAM_REAR),
runs object detection, and publishes rearDetections.

No radar dependency. No cross-camera handover (single camera).
"""

import logging
import time
from typing import List, Optional

import numpy as np

import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware import HARDWARE

# NOTE: capability check happens in main() — a module-level exit() would kill
# any process that merely imports this module (tests, tooling).

# VisionIPC integration
try:
  from msgq.visionipc import VisionIpcClient, VisionStreamType
  HAS_VISIONIPC = True
except ImportError:
  HAS_VISIONIPC = False

VISION_STREAM_REAR = VisionStreamType.VISION_STREAM_REAR

from openpilot.selfdrive.sided.hailo_side_detector import HailoSideDetector
from openpilot.selfdrive.sided.simple_tracker import SimpleTracker, SideObject

RATE = 20  # 20 Hz

# Object classes relevant for RCTA (vehicles, pedestrians, cyclists)
RELEVANT_CLASSES = frozenset({
  'person', 'bicycle', 'motorcycle', 'car', 'bus', 'truck',
})


class SideObjectFromDetection:
  """Adapter: convert hailo/cpu detection → SideObject for tracker."""

  def __init__(self, det):
    self.label = det.label
    self.confidence = det.confidence
    self.bbox = det.bbox  # [x1, y1, x2, y2]
    self.distance_m = det.distance_m if hasattr(det, 'distance_m') else 0.0
    self.lateral_m = det.lateral_m if hasattr(det, 'lateral_m') else 0.0
    self.track_id = -1


def _to_tracker_objects(detections) -> List[SideObject]:
  """Convert detector output to SideObject list for SimpleTracker."""
  objs = []
  for det in detections:
    if det.label not in RELEVANT_CLASSES:
      continue
    # For rear camera, we use pixel-space tracking.
    # BEV reprojection is less reliable for rear (steep angle).
    so = SideObject()
    so.label = det.label
    so.confidence = det.confidence
    so.bbox = det.bbox
    so.distance_m = 0.0  # Will be estimated from bbox size
    so.lateral_m = 0.0   # Centered in rear view
    so.track_id = -1
    objs.append(so)
  return objs


def _estimate_distance_from_bbox(bbox, img_h: int) -> float:
  """
  Rough distance estimate from rear-camera bounding box height.
  Rear camera is mounted high looking down; larger bbox = closer.
  Calibrated for 640x480 rear camera at typical mounting height.
  """
  _, y1, _, y2 = bbox
  bh = max(y2 - y1, 1)
  # Heuristic: a car at 5m fills ~80 px vertically at 480p
  # distance ≈ 400 / bh
  return min(400.0 / bh, 50.0)


class RearProcessor:
  """CPU fallback processor (same as SideProcessor but for rear)."""

  def __init__(self):
    self.tracker = SimpleTracker(max_age=5)

  def detect(self, frame_bgr: np.ndarray | None) -> List[SideObject]:
    if frame_bgr is None:
      return []
    # CPU fallback: no neural network, use simple motion detection / blob
    # For now, return empty — rear CPU detection is not implemented.
    # The Hailo path is the primary production path.
    return []

  def analyze_quality(self, frame_bgr: np.ndarray):
    """Return basic frame quality metrics."""
    gray = frame_bgr.mean(axis=2)
    return {
      'brightness': float(gray.mean()),
      'contrast': float(gray.std()),
    }


class HailoRearProcessor:
  """Hailo-8 YOLO processor for rear camera. Reuses HailoSideDetector."""

  def __init__(self):
    self.detector = HailoSideDetector()
    self.tracker = SimpleTracker(max_age=5)

  @property
  def is_available(self) -> bool:
    return self.detector.is_available

  def detect(self, frame_bgr: np.ndarray | None) -> List[SideObject]:
    if frame_bgr is None or not self.is_available:
      return []
    dets = self.detector.detect(frame_bgr)
    objs = _to_tracker_objects(dets)
    # Estimate distance from bbox size
    h = frame_bgr.shape[0]
    for obj in objs:
      obj.distance_m = _estimate_distance_from_bbox(obj.bbox, h)
      # lateral position: center of bbox relative to image center
      x1, _, x2, _ = obj.bbox
      cx = (x1 + x2) / 2.0
      # Normalize: 0 = center, -1 = left edge, +1 = right edge
      img_w = frame_bgr.shape[1]
      obj.lateral_m = (cx - img_w / 2.0) / (img_w / 2.0)
    return self.tracker.update(objs)


class RearD:
  """Rear Camera Perception Daemon."""

  def __init__(self) -> None:
    set_daemon_affinity("reard")

    self.pm = messaging.PubMaster(['rearDetections', 'rearStatus'])
    self.sm = messaging.SubMaster(['rearCameraState'])

    self.rk = Ratekeeper(RATE, print_delay_threshold=None)
    self.params = Params()
    self.enabled = self.params.get_bool("EOPRearCameraEnabled")

    self.frame_id = 0
    self.cpu_processor = RearProcessor()
    self.hailo_processor = HailoRearProcessor()
    self.use_hailo = self.hailo_processor.is_available

    self._vipc_rear = None
    self._init_visionipc()

    cloudlog.info(
      "RearD initialized: enabled=%s, hailo=%s, visionipc=%s",
      self.enabled, self.use_hailo, HAS_VISIONIPC,
    )

  def _init_visionipc(self) -> None:
    if not HAS_VISIONIPC:
      cloudlog.warning("RearD: VisionIPC not available")
      return

    try:
      self._vipc_rear = VisionIpcClient("uvcd", VISION_STREAM_REAR, False)
      if not self._vipc_rear.connect(False):
        cloudlog.warning("RearD: rear VisionIPC not available")
        self._vipc_rear = None
      else:
        cloudlog.info("RearD: rear VisionIPC connected (%dx%d)",
                      self._vipc_rear.width, self._vipc_rear.height)
    except Exception as e:
      cloudlog.warning("RearD: rear VisionIPC init failed: %s", e)
      self._vipc_rear = None

  def _get_frame(self) -> np.ndarray | None:
    if self._vipc_rear is None:
      return None
    try:
      buf = self._vipc_rear.recv(timeout_ms=50)
      if buf is None:
        return None
      h = self._vipc_rear.height or 480
      w = self._vipc_rear.width or 640
      frame = np.frombuffer(buf.data, dtype=np.uint8).reshape((h, w, 3))
      return frame
    except Exception as e:
      cloudlog.debug("RearD: VisionIPC frame retrieval failed: %s", e)
      return None

  def _publish(self, tracks: List[SideObject], quality: dict | None,
               ts: int, proc_time_ms: float) -> None:
    # rearDetections
    msg = messaging.new_message('rearDetections', valid=True)
    msg.rearDetections.frameId = self.frame_id
    msg.rearDetections.timestamp = ts / 1e9
    msg.rearDetections.numTracks = len(tracks)
    msg.rearDetections.cameraSource = "rear"

    if tracks:
      items = msg.rearDetections.init('detections', len(tracks))
      for i, track in enumerate(tracks):
        items[i].className = track.label
        items[i].confidence = track.confidence
        items[i].x = track.distance_m
        items[i].y = track.lateral_m
        items[i].cameraSource = 'rear'
    self.pm.send('rearDetections', msg)

    # rearStatus
    status_msg = messaging.new_message('rearStatus', valid=True)
    ss = status_msg.rearStatus
    ss.enabled = self.enabled
    ss.fault = False
    ss.faultReason = ""
    ss.consecutiveFailures = 0
    ss.numTracks = len(tracks)
    ss.processingTimeMs = round(proc_time_ms, 2)
    self.pm.send('rearStatus', status_msg)

  def run(self) -> None:
    if not self.enabled:
      cloudlog.info("RearD disabled — exiting")
      return

    cloudlog.info("RearD running (rear camera perception)")

    try:
      while True:
        self.sm.update(0)

        frame = None
        if self.sm.updated['rearCameraState']:
          frame = self._get_frame()

        quality = None
        if frame is not None:
          quality = self.cpu_processor.analyze_quality(frame)

        # Inference
        t0 = time.monotonic()
        if self.use_hailo:
          dets = self.hailo_processor.detect(frame)
        else:
          dets = self.cpu_processor.detect(frame)
        proc_time_ms = (time.monotonic() - t0) * 1000.0

        ts = int(time.monotonic() * 1e9)
        self._publish(dets, quality, ts, proc_time_ms)

        self.frame_id = (self.frame_id + 1) & 0xFFFFFF
        self.rk.keep_time()
    except Exception:
      raise


def main() -> int:
  if not HARDWARE.has_rear_camera():
    cloudlog.error("reard: platform does not support rear camera — exiting")
    return 1
  try:
    logging.basicConfig(level=logging.INFO)
    RearD().run()
    return 0
  except KeyboardInterrupt:
    cloudlog.info("RearD stopped")
    return 0
  except Exception as e:
    cloudlog.exception(f"RearD fatal error: {e}")
    raise


if __name__ == "__main__":
  exit(main())
