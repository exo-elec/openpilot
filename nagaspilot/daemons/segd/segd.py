#!/usr/bin/env python3
"""
segd — drivable-area segmentation for every camera, on the camera-tier card.

The Hailo-8 / DX-M1M runs one network, TwinLiteNet+ Large (drivable area + lane lines,
the same on both cards), constantly, round-robin over the cameras. Object detection is not here: it
runs on the SoC's RKNN NPU (YOLOv8 in monod, sided, reard).

Publishes, per card job:
  drivableBev  -- that camera's map projected onto gridd's BEV grid
                  (drivable_bev.py); gridd fuses it into its cost layer.
  monoSegments -- hasRoad / hasEdge / hasDrivable per camera seen; rcd reads
                  the 'road' entry.

segd is the card's only segmentation client, at the 20 Hz foundation rate
(cameras stream 30 fps), at most one job per tick. Which camera goes to the
card is decided by what the drive needs (schedule.py): road always, wide and
tele by speed, side cameras with a blinker / blind-spot flag / side
detection, rear in reverse or with a rear detection. A camera nobody needs
is not segmented, and a tick with nothing due runs no job. With no card, or
no artifact for it, segd idles; gridd then marks segmentation degraded --
never a fault.
"""

from __future__ import annotations

import time

import cv2
import numpy as np

import cereal.messaging as messaging
from cereal import car
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.nagaspilot.daemons.segd.card_segmenter import CardSegmenter, summarize
from openpilot.nagaspilot.daemons.segd.schedule import Scheduler, SegContext
from openpilot.nagaspilot.daemons.segd.drivable_bev import (
  DEFAULT_CAMERA_HEIGHT_M, FRONT_LENS, BevLut, build_lut, front_geometry, project, side_rear_geometry)
from openpilot.nagaspilot.daemons.gridd.lazy_bev import BEV_GRID
from openpilot.system.hardware import HARDWARE

try:
  from msgq.visionipc import VisionIpcClient, VisionStreamType
  HAS_VISIONIPC = True
except ImportError:
  HAS_VISIONIPC = False

RATE = 20  # foundation rate
CALIB_ROUND_RAD = 0.002  # rebuild a front camera's lookup table only on a real calibration change
RECONNECT_EVERY_N = 100  # retry missing cameras every 5 s

# camera → (VisionIPC server, stream type name, card model id)
CAMERAS: dict[str, tuple[str, str, str]] = {
  'road':       ('v4l2d', 'VISION_STREAM_ROAD', 'seg_road'),
  'wide':       ('v4l2d', 'VISION_STREAM_WIDE_ROAD', 'seg_wide'),
  'tele':       ('v4l2d', 'VISION_STREAM_TELE_ROAD', 'seg_tele'),
  'side_left':  ('uvcd', 'VISION_STREAM_SIDE_LEFT', 'seg_side'),
  'side_right': ('uvcd', 'VISION_STREAM_SIDE_RIGHT', 'seg_side'),
  'rear':       ('uvcd', 'VISION_STREAM_REAR', 'seg_rear'),
}

def decode_frame(buf, width: int, height: int) -> np.ndarray | None:
  """VisionIPC buffer → BGR, whether the server sends BGR or NV12."""
  data = np.frombuffer(buf.data, dtype=np.uint8)
  if data.size == width * height * 3:
    return data.reshape((height, width, 3))
  if data.size >= width * height * 3 // 2:
    nv12 = data[:width * height * 3 // 2].reshape((height * 3 // 2, width))
    return cv2.cvtColor(nv12, cv2.COLOR_YUV2BGR_NV12)
  return None


class SegD:
  def __init__(self) -> None:
    set_daemon_affinity("segd")
    self.pm = messaging.PubMaster(['monoSegments', 'drivableBev'])
    self.sm = messaging.SubMaster(['liveCalibration', 'carState', 'sideDetections', 'rearDetections'])
    self.scheduler = Scheduler(RATE)
    self.rk = Ratekeeper(RATE, print_delay_threshold=None)
    try:
      self.has_tele = bool(HARDWARE.get_camera_array_config().get('has_tele_road', False))
    except Exception:
      self.has_tele = False
    # One segmenter per model id; side_left and side_right share seg_side
    self._segmenters: dict[str, CardSegmenter] = {}
    for cam, (_, _, model) in CAMERAS.items():
      if cam == 'tele' and not self.has_tele:
        continue
      self._segmenters.setdefault(model, CardSegmenter(model, "segd", priority=2))
    self._clients: dict[str, object] = {}
    self._summaries: dict[str, tuple[bool, bool, bool]] = {}
    self._luts: dict[str, tuple[tuple, BevLut]] = {}
    self.frame_id = 0
    self._connect_missing()

  @property
  def card_active(self) -> bool:
    return any(s.is_available for s in self._segmenters.values())

  def _connect_missing(self) -> None:
    if not HAS_VISIONIPC:
      return
    for cam, (server, stream_name, _) in CAMERAS.items():
      if cam in self._clients or (cam == 'tele' and not self.has_tele):
        continue
      stream = getattr(VisionStreamType, stream_name, None)
      if stream is None:
        continue
      try:
        client = VisionIpcClient(server, stream, True)
        if client.connect(False):
          self._clients[cam] = client
          cloudlog.info(f"segd: {cam} connected")
      except Exception as e:
        cloudlog.debug(f"segd: {cam} connect failed: {e}")

  def _geometry(self, cam: str, img_w: float, img_h: float):
    """The camera's pose for projection, and a key that changes when it does."""
    if cam in FRONT_LENS:
      rpy, height = [0.0, 0.0, 0.0], DEFAULT_CAMERA_HEIGHT_M
      if self.sm.valid.get('liveCalibration', False):
        lc = self.sm['liveCalibration']
        if len(lc.rpyCalib) == 3:
          rpy = list(lc.rpyCalib)
          if cam == 'wide' and len(lc.wideFromDeviceEuler) == 3:
            rpy = [a + b for a, b in zip(rpy, lc.wideFromDeviceEuler, strict=True)]
        if len(lc.height) and 0.5 < lc.height[0] < 3.0:
          height = float(lc.height[0])
      key = (cam, img_w, img_h, tuple(round(a / CALIB_ROUND_RAD) for a in rpy), round(height, 2))
      return key, (lambda: front_geometry(cam, img_w, img_h, rpy, height))
    return (cam, img_w, img_h), (lambda: side_rear_geometry(cam, img_w, img_h))

  def _bev(self, cam: str, class_map: np.ndarray, img_hw: tuple[int, int]) -> np.ndarray | None:
    """That class map on gridd's BEV grid, through the camera's lookup table."""
    try:
      key, make = self._geometry(cam, float(img_hw[1]), float(img_hw[0]))
      key = key + (class_map.shape,)
      cached = self._luts.get(cam)
      if cached is None or cached[0] != key:
        cached = (key, build_lut(make(), class_map.shape, cam))
        self._luts[cam] = cached
      return project(class_map, cached[1])
    except Exception as e:
      cloudlog.debug(f"segd: {cam} BEV projection failed: {e}")
      return None

  def _publish_bev(self, cam: str, cells: np.ndarray) -> None:
    msg = messaging.new_message('drivableBev')
    d = msg.drivableBev
    d.frameId = self.frame_id
    d.resolution = BEV_GRID.resolution
    d.width = BEV_GRID.cols
    d.height = BEV_GRID.rows
    d.originX = BEV_GRID.origin_left
    d.originY = BEV_GRID.origin_forward
    entry = d.init('cameras', 1)[0]
    entry.camera = cam
    entry.frameTimestamp = int(time.monotonic() * 1e9)
    entry.data = cells.tobytes()
    self.pm.send('drivableBev', msg)

  def _frame(self, cam: str) -> np.ndarray | None:
    client = self._clients.get(cam)
    if client is None:
      return None
    try:
      buf = client.recv(timeout_ms=0)
      if buf is None:
        return None
      return decode_frame(buf, client.width, client.height)
    except Exception as e:
      cloudlog.debug(f"segd: {cam} frame failed: {e}")
      return None

  def _context(self) -> SegContext:
    """What the drive needs, from carState and the side/rear detections."""
    sm = self.sm
    kw: dict = {'has_tele': self.has_tele}
    if sm.alive.get('carState', False):
      cs = sm['carState']
      kw.update(v_ego=float(cs.vEgo), reverse=cs.gearShifter == car.CarState.GearShifter.reverse,
                steering_deg=float(cs.steeringAngleDeg), left_blinker=bool(cs.leftBlinker),
                right_blinker=bool(cs.rightBlinker), left_blindspot=bool(cs.leftBlindspot),
                right_blindspot=bool(cs.rightBlindspot))
    if sm.alive.get('sideDetections', False):
      sources = {d.cameraSource for d in sm['sideDetections'].detections}
      kw.update(left_detection='side_left' in sources, right_detection='side_right' in sources)
    if sm.alive.get('rearDetections', False):
      kw.update(rear_detection=len(sm['rearDetections'].detections) > 0)
    return SegContext(**kw)

  def step(self) -> None:
    self.sm.update(0)
    cam = self.scheduler.next_camera(self.frame_id, self._context())
    if cam is None:  # nothing due: the card idles
      self._publish()
      return
    segmenter = self._segmenters.get(CAMERAS[cam][2])
    if segmenter is not None and segmenter.is_available:
      frame = self._frame(cam)
      class_map = segmenter.segment(frame)
      if class_map is not None and frame is not None:
        self._summaries[cam] = summarize(class_map)
        cells = self._bev(cam, class_map, frame.shape[:2])
        if cells is not None:
          self._publish_bev(cam, cells)
    self._publish()

  def _publish(self) -> None:
    msg = messaging.new_message('monoSegments', valid=bool(self._summaries))
    msg.monoSegments.frameId = self.frame_id
    segs = msg.monoSegments.init('segments', len(self._summaries))
    for i, (cam, (has_road, has_edge, has_drivable)) in enumerate(self._summaries.items()):
      segs[i].camera = cam
      segs[i].hasRoad = has_road
      segs[i].hasEdge = has_edge
      segs[i].hasDrivable = has_drivable
    self.pm.send('monoSegments', msg)

  def run(self) -> None:
    cloudlog.info(f"segd running: cameras={list(CAMERAS) if self.has_tele else [c for c in CAMERAS if c != 'tele']}")
    while True:
      if self.frame_id % RECONNECT_EVERY_N == 0:
        self._connect_missing()
      if self.card_active:
        self.step()
        self.rk.keep_time()
      else:
        time.sleep(1.0)  # no card / no artifact: nothing to do
      self.frame_id += 1


def main() -> int:
  try:
    SegD().run()
    return 0
  except Exception as e:
    cloudlog.exception(f"segd fatal error: {e}")
    return 1


if __name__ == "__main__":
  exit(main())
