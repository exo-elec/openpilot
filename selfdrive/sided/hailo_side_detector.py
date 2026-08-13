#!/usr/bin/env python3
"""
Hailo-8 side/rear camera object detector.

Runs YOLOv8-nano via the centralized inferenced daemon over IPC.
Produces SideObject detections for BSD/RCTA tracking.

Shared by sided.py (side_left/side_right) and reard.py (rear) — both consume
this class with their own daemon_name. The Hailo-8 is a single physical PCIe
device: HailoRT gives one process exclusive VDevice ownership (there's no
hailort multi-process scheduler service running on ExoPilot), so detection
MUST go through inferenced's IPC job queue (which is the sole process that
touches Hailo8Backend/VDevice) rather than each daemon creating its own
VDevice directly — two daemons doing that concurrently means only one of them
ever gets a working accelerator and the other silently gets zero detections.
"""

from __future__ import annotations


import cv2
import numpy as np

from openpilot.common.swaglog import cloudlog
from openpilot.system.inferenced.client import InferenceClient
from openpilot.system.inferenced.compute import BackendType

from openpilot.selfdrive.sided.simple_tracker import SideObject


# COCO class IDs we care about for blind-spot / cross-traffic monitoring
RELEVANT_COCO_CLASSES: dict[int, str] = {
  0: 'person',
  1: 'bicycle',
  2: 'car',
  3: 'motorcycle',
  5: 'bus',
  7: 'truck',
}

# Class size priors for rough distance estimation (fallback when BEV is unavailable)
CLASS_HEIGHT_PRIORS_M: dict[str, float] = {
  'person':     1.7,
  'bicycle':    1.0,
  'motorcycle': 1.2,
  'car':        1.5,
  'bus':        2.8,
  'truck':      2.5,
}


class HailoSideDetector:
  """Hailo-8 YOLO detector for side/rear cameras.

  Submits jobs to the centralized inferenced daemon (IPC) instead of loading
  the HEF and owning a VDevice locally — see module docstring.
  """

  MODEL_NAME = 'yolo_side'
  INPUT_SIZE = (640, 640)  # (width, height)
  CONF_THRESHOLD = 0.35
  NMS_THRESHOLD = 0.45
  # Bounded so a stalled/absent inferenced daemon can't stall the 20Hz camera
  # loop; a miss just yields zero detections for that frame and retries next.
  POLL_TIMEOUT_MS = 120.0

  def __init__(self, daemon_name: str = "sided") -> None:
    self._client = InferenceClient(daemon_name, use_ipc=True)
    # Optimistic — the daemon that actually holds the Hailo-8 confirms/denies
    # per job. Only latched False on a definitive "not available" response so
    # a slow-starting inferenced daemon doesn't permanently disable detection.
    self._available = True

  @property
  def is_available(self) -> bool:
    return self._available

  def detect(self, frame_bgr: np.ndarray | None) -> list[SideObject]:
    """Run YOLO inference on a single BGR frame via the centralized daemon."""
    if frame_bgr is None or not self._available:
      return []

    try:
      rgb = self._preprocess(frame_bgr)
      result = self._client.submit_job(
        backend_type=BackendType.HAILO_8,
        model_name=self.MODEL_NAME,
        input_array=rgb,
        poll_timeout_ms=self.POLL_TIMEOUT_MS,
      )
      if not result.success:
        if result.error_message and 'not available' in result.error_message:
          self._available = False
          cloudlog.warning(f"HailoSideDetector: {result.error_message} — disabling")
        else:
          cloudlog.debug(f"HailoSideDetector: inference failed: {result.error_message}")
        return []

      return self._postprocess(result.outputs, frame_bgr.shape[:2])

    except Exception as e:
      cloudlog.debug(f"HailoSideDetector: detect error ({e})")
      return []

  # ---------------------------------------------------------------------------
  # Pre-processing
  # ---------------------------------------------------------------------------
  def _preprocess(self, frame_bgr: np.ndarray) -> np.ndarray:
    """Resize and convert BGR -> RGB, add batch dimension."""
    target_w, target_h = self.INPUT_SIZE
    resized = cv2.resize(frame_bgr, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    return np.expand_dims(rgb, axis=0)  # NHWC

  # ---------------------------------------------------------------------------
  # Post-processing
  # ---------------------------------------------------------------------------
  def _postprocess(self, outputs: dict, orig_shape: tuple[int, int]) -> list[SideObject]:
    """Parse Hailo YOLO outputs into SideObject list."""
    if not outputs:
      return []

    # Hailo may name the output tensor 'output' or something else
    if 'output' in outputs:
      raw = outputs['output']
    else:
      raw = next(iter(outputs.values()))

    # Ensure shape is [num_detections, 6]
    if raw.ndim == 3:
      raw = raw[0]  # drop batch dim
    if raw.ndim != 2 or raw.shape[1] < 6:
      return []

    orig_h, orig_w = orig_shape
    in_w, in_h = self.INPUT_SIZE

    detections: list[SideObject] = []
    pixel_mode = False

    # Heuristic: if any coordinate > 1.5, assume pixel space (0-input_size)
    for pred in raw:
      if float(pred[4]) < self.CONF_THRESHOLD:
        continue
      coords = pred[:4]
      if np.max(np.abs(coords)) > 1.5:
        pixel_mode = True
        break

    for pred in raw:
      score = float(pred[4])
      if score < self.CONF_THRESHOLD:
        continue

      class_id = int(pred[5])
      if class_id not in RELEVANT_COCO_CLASSES:
        continue

      label = RELEVANT_COCO_CLASSES[class_id]

      if pixel_mode:
        # Assume [x1, y1, x2, y2] in input-resolution pixels
        x1, y1, x2, y2 = float(pred[0]), float(pred[1]), float(pred[2]), float(pred[3])
        # Scale to original frame size
        x1 = x1 * orig_w / in_w
        y1 = y1 * orig_h / in_h
        x2 = x2 * orig_w / in_w
        y2 = y2 * orig_h / in_h
      else:
        # Assume [x_center, y_center, w, h] normalized [0,1]
        cx, cy, w, h = float(pred[0]), float(pred[1]), float(pred[2]), float(pred[3])
        x1 = (cx - w / 2.0) * orig_w
        y1 = (cy - h / 2.0) * orig_h
        x2 = (cx + w / 2.0) * orig_w
        y2 = (cy + h / 2.0) * orig_h

      # Clamp to image bounds
      x1 = max(0.0, min(x1, orig_w - 1))
      y1 = max(0.0, min(y1, orig_h - 1))
      x2 = max(0.0, min(x2, orig_w - 1))
      y2 = max(0.0, min(y2, orig_h - 1))

      if x2 <= x1 or y2 <= y1:
        continue

      # Rough distance estimate from apparent box height (fallback)
      box_h_px = y2 - y1
      dist_m = self._estimate_distance(label, box_h_px, orig_h)

      detections.append(SideObject(
        uid=-1,
        label=label,
        confidence=score,
        distance_m=dist_m,
        lateral_m=0.0,
        height_m=CLASS_HEIGHT_PRIORS_M.get(label, 1.5),
        velocity_mps=0.0,
        bbox_2d=(x1, y1, x2, y2),
        width_m=1.8,
        length_m=4.5,
      ))

    # Apply NMS to remove duplicates
    detections = self._nms(detections)
    return detections

  def _estimate_distance(self, label: str, box_h_px: float, img_h: int) -> float:
    """Very rough distance from apparent height (fallback only)."""
    real_h = CLASS_HEIGHT_PRIORS_M.get(label, 1.5)
    if box_h_px <= 0:
      return 50.0
    # Side cameras: ~90° FOV, focal length roughly image height for ~60° vFOV
    # This is coarse; BEV reprojector refines it using camera geometry.
    focal_len_px = img_h  # rough
    return (real_h * focal_len_px) / box_h_px

  def _nms(self, detections: list[SideObject]) -> list[SideObject]:
    """Greedy IoU-based NMS."""
    if not detections:
      return detections

    # Sort by confidence descending
    dets = sorted(detections, key=lambda d: d.confidence, reverse=True)
    keep: list[SideObject] = []

    while dets:
      current = dets.pop(0)
      keep.append(current)
      dets = [d for d in dets if self._iou(current.bbox_2d, d.bbox_2d) < self.NMS_THRESHOLD]

    return keep

  @staticmethod
  def _iou(box_a: tuple[float, ...], box_b: tuple[float, ...]) -> float:
    x1 = max(box_a[0], box_b[0])
    y1 = max(box_a[1], box_b[1])
    x2 = min(box_a[2], box_b[2])
    y2 = min(box_a[3], box_b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
    area_b = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0
