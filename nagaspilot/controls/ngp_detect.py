"""YOLOv8-style detector post-processing: letterbox, head decode, per-class NMS.

Pure numpy, no model runtime. The model itself is whatever ONNX the device
carries (nagaspilot/runtime/monod.py loads it); nothing here depends on a
vendor package. Head layouts accepted:
  - [1, 4 + C, N]  (Ultralytics YOLOv8 export, channels first), or its transpose [1, N, 4 + C]
  - [N, 6] already NMS-fused rows: x1, y1, x2, y2, score, class
Boxes come back in source-image pixels.
"""
from dataclasses import dataclass

import numpy as np

# COCO ids used on the road camera. A three-lamp traffic-light head is not ranged here.
ROAD_CLASSES: dict[int, str] = {
  0: 'person', 1: 'bicycle', 2: 'car', 3: 'motorcycle', 5: 'bus', 7: 'truck',
}


@dataclass(frozen=True)
class Letterbox:
  """Uniform scale + centred padding that fits a (src_w, src_h) image into a square input."""
  scale: float
  pad_x: float
  pad_y: float

  @staticmethod
  def fit(src_w: int, src_h: int, size: int) -> 'Letterbox':
    scale = min(size / src_w, size / src_h)
    return Letterbox(scale, (size - src_w * scale) / 2.0, (size - src_h * scale) / 2.0)

  def to_source(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return (x - self.pad_x) / self.scale, (y - self.pad_y) / self.scale


@dataclass(frozen=True)
class Box:
  cls: int
  name: str
  conf: float
  x1: float
  y1: float
  x2: float
  y2: float

  @property
  def w(self) -> float:
    return self.x2 - self.x1

  @property
  def h(self) -> float:
    return self.y2 - self.y1

  @property
  def cx(self) -> float:
    return 0.5 * (self.x1 + self.x2)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_thresh: float) -> list[int]:
  """Greedy NMS on [n,4] xyxy boxes. Returns kept indices, best score first."""
  order = np.argsort(-scores)
  keep: list[int] = []
  areas = np.maximum(boxes[:, 2] - boxes[:, 0], 0) * np.maximum(boxes[:, 3] - boxes[:, 1], 0)
  while order.size:
    i = int(order[0])
    keep.append(i)
    if order.size == 1:
      break
    rest = order[1:]
    ix1 = np.maximum(boxes[i, 0], boxes[rest, 0])
    iy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
    ix2 = np.minimum(boxes[i, 2], boxes[rest, 2])
    iy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
    inter = np.maximum(ix2 - ix1, 0) * np.maximum(iy2 - iy1, 0)
    union = areas[i] + areas[rest] - inter
    iou = np.where(union > 0, inter / np.maximum(union, 1e-9), 0.0)
    order = rest[iou <= iou_thresh]
  return keep


def decode(out: np.ndarray, lb: Letterbox, src_w: int, src_h: int,
           classes: dict[int, str] = ROAD_CLASSES, conf_thresh: float = 0.35,
           iou_thresh: float = 0.5, max_det: int = 50) -> list[Box]:
  """Turn one raw model output into boxes in source-image pixels."""
  arr = np.asarray(out, dtype=np.float32)
  if arr.ndim == 3:
    arr = arr[0]
  if arr.ndim != 2 or arr.size == 0:
    return []

  if arr.shape[1] == 6 and arr.shape[0] != 6:  # NMS-fused rows
    xyxy, score, cls = arr[:, :4], arr[:, 4], arr[:, 5].astype(int)
    xyxy = _unletterbox(xyxy, lb, src_w, src_h)
    keep = [i for i in range(len(score)) if score[i] >= conf_thresh and int(cls[i]) in classes]
    return [_box(int(cls[i]), classes, float(score[i]), xyxy[i]) for i in keep][:max_det]

  if arr.shape[0] < arr.shape[1]:  # [4 + C, N] -> [N, 4 + C]
    arr = arr.T
  if arr.shape[1] < 5:
    return []

  cxcywh, cls_scores = arr[:, :4], arr[:, 4:]
  ids = np.array(sorted(classes))
  ids = ids[ids < cls_scores.shape[1]]
  if ids.size == 0:
    return []
  sub = cls_scores[:, ids]
  best = np.argmax(sub, axis=1)
  conf = sub[np.arange(len(sub)), best]
  cls = ids[best]
  m = conf >= conf_thresh
  if not m.any():
    return []
  cxcywh, conf, cls = cxcywh[m], conf[m], cls[m]
  xyxy = np.stack([cxcywh[:, 0] - cxcywh[:, 2] / 2, cxcywh[:, 1] - cxcywh[:, 3] / 2,
                   cxcywh[:, 0] + cxcywh[:, 2] / 2, cxcywh[:, 1] + cxcywh[:, 3] / 2], axis=1)
  xyxy = _unletterbox(xyxy, lb, src_w, src_h)

  result: list[Box] = []
  for c in np.unique(cls):  # per-class NMS
    idx = np.where(cls == c)[0]
    for k in nms(xyxy[idx], conf[idx], iou_thresh):
      j = idx[k]
      result.append(_box(int(c), classes, float(conf[j]), xyxy[j]))
  result.sort(key=lambda b: -b.conf)
  return result[:max_det]


def _unletterbox(xyxy: np.ndarray, lb: Letterbox, src_w: int, src_h: int) -> np.ndarray:
  x1, y1 = lb.to_source(xyxy[:, 0], xyxy[:, 1])
  x2, y2 = lb.to_source(xyxy[:, 2], xyxy[:, 3])
  return np.stack([np.clip(x1, 0, src_w), np.clip(y1, 0, src_h), np.clip(x2, 0, src_w), np.clip(y2, 0, src_h)], axis=1)


def _box(cls: int, classes: dict[int, str], conf: float, xyxy: np.ndarray) -> Box:
  return Box(cls, classes[cls], conf, float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3]))
