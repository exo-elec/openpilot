#!/usr/bin/env python3
"""
YOLOv8 object detector on the SoC's RKNN NPU, for every camera.

Shared by sided.py (side_left/side_right), reard.py (rear) and monod.py
(road, and the 02M telephoto). Object detection runs on RKNN because RKNN is
on the die: it cannot be unfitted or drop off a bus, so detection -- the part
BSD, RCTA and the front object list are built from -- is always there. The
PCIe card (Hailo-8 / DX-M1M) runs semantic segmentation for all cameras
instead (selfdrive/segd), which is one constant network: what a card with no
DRAM (Hailo-8) does best.

Each daemon loads its own RKNN context in-process (RKNN supports one per
process) through InferenceClient's local HAL, on NPU core 1 by default --
the core that segmentation used to occupy on both boards (01M: 3 cores,
02M: 2). The yolo_640 model is an Ultralytics YOLOv8n export: one
[1, 84, 8400] head, no NMS; parse_outputs() decodes it and also accepts an
NMS-fused [N, 6] list.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.modeld.runners.rknn_platform import get_platform_npu_config, rknn_soc_tag
from openpilot.system.inferenced.client import InferenceClient
from openpilot.system.inferenced.compute import ModelConfig

from openpilot.nagaspilot.daemons.sided.simple_tracker import SideObject

_REPO_ROOT = Path(__file__).resolve().parents[2]

# NPU core that segmentation freed when it moved to the card (both boards).
DETECTION_CORE = 1


def yolo_model_path() -> str | None:
  """First yolo_640 artifact present: SoC-tagged RKNN, legacy RKNN, repo ONNX (dev PC)."""
  soc = rknn_soc_tag()
  for p in (Path(f'/data/openpilot/models/rknn/yolo_640_{soc}.rknn'),
            Path('/data/openpilot/models/rknn/yolo_640.rknn'),
            _REPO_ROOT / f'models/rknn/yolo_640_{soc}.rknn',
            _REPO_ROOT / 'models/rknn/yolo_640.rknn',
            _REPO_ROOT / 'models/onnx/yolo_640.onnx'):
    if p.exists():
      return str(p)
  return None


# COCO class IDs we care about for blind-spot / cross-traffic monitoring
RELEVANT_COCO_CLASSES: dict[int, str] = {
  0: 'person',
  1: 'bicycle',
  2: 'car',
  3: 'motorcycle',
  5: 'bus',
  7: 'truck',
}

# monod's road camera also looks for traffic lights, for TLSC
TRAFFIC_LIGHT = 'traffic light'
ROAD_COCO_CLASSES: dict[int, str] = {**RELEVANT_COCO_CLASSES, 9: TRAFFIC_LIGHT}

# Class size priors for rough distance estimation (fallback when BEV is unavailable)
CLASS_HEIGHT_PRIORS_M: dict[str, float] = {
  'person':     1.7,
  'bicycle':    1.0,
  'motorcycle': 1.2,
  'car':        1.5,
  'bus':        2.8,
  'truck':      2.5,
  # A three-lamp signal head is about 1 m along its long side, vertical or
  # horizontal; ranging uses the box's long side for this class.
  TRAFFIC_LIGHT: 1.0,
}


class YoloDetector:
  """YOLOv8 on RKNN for one daemon; detect() takes a BGR frame of any size."""

  INPUT_SIZE = (640, 640)  # (width, height)
  CONF_THRESHOLD = 0.35
  NMS_THRESHOLD = 0.45
  FAULT_THRESHOLD = 3  # consecutive failures before fault is reported

  def __init__(self, daemon_name: str, core_id: int = DETECTION_CORE,
               model_path: str | None = None, backend=None, name: str | None = None,
               classes: dict[int, str] | None = None) -> None:
    self.daemon_name = daemon_name
    self.classes = classes if classes is not None else RELEVANT_COCO_CLASSES
    # One RKNN context per model name: monod runs "road" and "tele" side by side
    self.model_name = f"yolo_{daemon_name}_{name}" if name else f"yolo_{daemon_name}"
    npu_config = get_platform_npu_config()
    if not npu_config.is_core_available(core_id):
      core_id = npu_config.core_count - 1
    self.core_id = core_id
    self.consecutive_failures = 0
    self._backend = backend
    self._loaded = False
    path = model_path or yolo_model_path()
    if path is None:
      cloudlog.warning(f"YoloDetector[{daemon_name}]: no yolo_640 model found")
      return
    try:
      if self._backend is None:
        self._backend = InferenceClient(daemon_name).inference_backend()
      # RKNNLite core_mask is a bit mask (NPU_CORE_0=1, _1=2, _2=4), not an index
      config = ModelConfig(name=self.model_name, path=path, model_type='detection',
                           npu_cores=1 << core_id)
      self._loaded = bool(self._backend.load_model(config))
      cloudlog.info(f"YoloDetector[{daemon_name}]: {path} on NPU core {core_id}: {self._loaded}")
    except Exception as e:
      cloudlog.error(f"YoloDetector[{daemon_name}]: load failed: {e}")

  @property
  def is_available(self) -> bool:
    return self._loaded

  @property
  def is_fault(self) -> bool:
    return not self._loaded or self.consecutive_failures >= self.FAULT_THRESHOLD

  def detect(self, frame_bgr: np.ndarray | None) -> list[SideObject]:
    """Detect objects in one BGR frame; boxes are in that frame's pixels."""
    if frame_bgr is None or not self._loaded:
      return []
    try:
      result = self._backend.infer(model_name=self.model_name,
                                   inputs={'input': self.preprocess(frame_bgr)})
      if not result.success:
        self.consecutive_failures += 1
        cloudlog.debug(f"YoloDetector[{self.daemon_name}]: {result.error_message}")
        return []
      self.consecutive_failures = 0
      outputs = result.outputs
      if 'output' not in outputs and outputs.get('outputs'):
        outputs = {'output': outputs['outputs'][0]}
      return self.parse_outputs(outputs, frame_bgr.shape[:2], self.classes)
    except Exception as e:
      self.consecutive_failures += 1
      cloudlog.debug(f"YoloDetector[{self.daemon_name}]: detect error ({e})")
      return []

  # ---------------------------------------------------------------------------
  # Pre-processing
  # ---------------------------------------------------------------------------
  @classmethod
  def preprocess(cls, frame_bgr: np.ndarray) -> np.ndarray:
    """Resize and convert BGR -> RGB, add batch dimension."""
    target_w, target_h = cls.INPUT_SIZE
    resized = cv2.resize(frame_bgr, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    return np.expand_dims(rgb, axis=0)  # NHWC

  # ---------------------------------------------------------------------------
  # Post-processing
  # ---------------------------------------------------------------------------
  @classmethod
  def parse_outputs(cls, outputs: dict, orig_shape: tuple[int, int],
                    classes: dict[int, str] | None = None) -> list[SideObject]:
    """Parse YOLO outputs into SideObject list.

    Two layouts arrive here: a post-NMS detection list [N, 6] (an NMS-fused
    HEF) and the raw YOLOv8 head [4 + classes, anchors] (a DX-M1 .dxnn, or
    the Ultralytics-exported yolo_640 on RKNN, neither carrying NMS). The raw
    head is decoded to the same [N, 6] form.
    """
    if not outputs:
      return []
    classes = RELEVANT_COCO_CLASSES if classes is None else classes

    # Backends may name the output tensor 'output' or something else
    if 'output' in outputs:
      raw = outputs['output']
    else:
      raw = next(iter(outputs.values()))

    raw = np.asarray(raw)
    if raw.ndim == 3:
      raw = raw[0]  # drop batch dim
    if raw.ndim != 2:
      return []
    if raw.shape[1] != 6:
      raw = cls._decode_yolov8_head(raw)
    if raw.shape[0] == 0 or raw.shape[1] < 6:
      return []

    orig_h, orig_w = orig_shape
    in_w, in_h = cls.INPUT_SIZE

    detections: list[SideObject] = []
    pixel_mode = False

    # Heuristic: if any coordinate > 1.5, assume pixel space (0-input_size)
    for pred in raw:
      if float(pred[4]) < cls.CONF_THRESHOLD:
        continue
      coords = pred[:4]
      if np.max(np.abs(coords)) > 1.5:
        pixel_mode = True
        break

    for pred in raw:
      score = float(pred[4])
      if score < cls.CONF_THRESHOLD:
        continue

      class_id = int(pred[5])
      if class_id not in classes:
        continue

      label = classes[class_id]

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
      dist_m = cls._estimate_distance(label, box_h_px, orig_h)

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
    detections = cls._nms(detections)
    return detections

  @classmethod
  def _decode_yolov8_head(cls, head: np.ndarray) -> np.ndarray:
    """Raw YOLOv8 head → [N, 6] rows of (x1, y1, x2, y2, score, class).

    The head is [4 + classes, anchors] (or its transpose), boxes as centre,
    width and height in input pixels, one sigmoid score per class.
    """
    if min(head.shape) < 5:
      return np.zeros((0, 6), dtype=np.float32)
    if head.shape[0] < head.shape[1]:
      head = head.T  # → [anchors, 4 + classes]
    head = head.astype(np.float32, copy=False)
    scores = head[:, 4:]
    class_ids = np.argmax(scores, axis=1)
    conf = scores[np.arange(scores.shape[0]), class_ids]
    keep = conf >= cls.CONF_THRESHOLD
    if not np.any(keep):
      return np.zeros((0, 6), dtype=np.float32)
    cx, cy, w, h = (head[keep, i] for i in range(4))
    return np.stack([cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0,
                     conf[keep], class_ids[keep].astype(np.float32)], axis=1)

  @classmethod
  def _estimate_distance(cls, label: str, box_h_px: float, img_h: int) -> float:
    """Very rough distance from apparent height (fallback only)."""
    real_h = CLASS_HEIGHT_PRIORS_M.get(label, 1.5)
    if box_h_px <= 0:
      return 50.0
    # Side cameras: ~90° FOV, focal length roughly image height for ~60° vFOV
    # This is coarse; BEV reprojector refines it using camera geometry.
    focal_len_px = img_h  # rough
    return (real_h * focal_len_px) / box_h_px

  @classmethod
  def _nms(cls, detections: list[SideObject]) -> list[SideObject]:
    """Greedy IoU-based NMS."""
    if not detections:
      return detections

    # Sort by confidence descending
    dets = sorted(detections, key=lambda d: d.confidence, reverse=True)
    keep: list[SideObject] = []

    while dets:
      current = dets.pop(0)
      keep.append(current)
      dets = [d for d in dets if cls._iou(current.bbox_2d, d.bbox_2d) < cls.NMS_THRESHOLD]

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
