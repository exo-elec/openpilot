#!/usr/bin/env python3
from __future__ import annotations

"""
YOLO object detector using centralized HAL.

Supports YOLOv5 (anchor-based) and YOLOv8 (anchor-free) .rknn models.
All NPU access goes through InferenceClient.
"""

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable, Sequence

import cv2
import numpy as np

# Centralized HAL - ONLY way to access NPU
from openpilot.system.inferenced import InferenceClient, BackendType


COCO_CLASSES = (
  "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
  "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
  "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
  "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
  "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
  "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
  "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
  "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
  "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
  "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
  "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
  "toothbrush",
)


@dataclass
class Detection:
  """Single object detection result."""
  class_id: int
  class_name: str
  confidence: float
  bbox: tuple[float, float, float, float]  # x1, y1, x2, y2
  mask: np.ndarray | None = None  # Optional segmentation mask


def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
  """Non-maximum suppression."""
  if len(boxes) == 0:
    return []

  x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
  areas = (x2 - x1) * (y2 - y1)
  order = scores.argsort()[::-1]

  keep = []
  while order.size > 0:
    i = order[0]
    keep.append(i)

    if order.size == 1:
      break

    xx1 = np.maximum(x1[i], x1[order[1:]])
    yy1 = np.maximum(y1[i], y1[order[1:]])
    xx2 = np.minimum(x2[i], x2[order[1:]])
    yy2 = np.minimum(y2[i], y2[order[1:]])

    w = np.maximum(0, xx2 - xx1)
    h = np.maximum(0, yy2 - yy1)
    inter = w * h

    iou = inter / (areas[i] + areas[order[1:]] - inter)
    order = order[1:][iou <= threshold]

  return keep


def _sigmoid(x: np.ndarray) -> np.ndarray:
  return 1.0 / (1.0 + np.exp(-x))


class YoloRKNNDetector:
  """YOLO detector using NPU via centralized HAL."""

  def __init__(
    self,
    model_path: str | Path,
    model_format: str = "yolov5",
    input_size: tuple[int, int] = (640, 640),
    obj_threshold: float = 0.25,
    nms_threshold: float = 0.45,
    classes: Sequence[str] | None = None,
    use_npu_cores: str = "all",
  ) -> None:
    """Initialize YOLO detector via HAL.
    
    Args:
        model_path: Path to .rknn model file
        model_format: 'yolov5' or 'yolov8'
        input_size: Model input size (width, height)
        obj_threshold: Object confidence threshold
        nms_threshold: NMS IoU threshold
        classes: Class names (defaults to COCO)
        use_npu_cores: NPU cores to use ('all', '0', '1', '2', '0,1')
    """
    # Create HAL client - ONLY way to access NPU and RGA
    self.client = InferenceClient("yolo_detector")
    
    # Get NPU backend
    try:
      self.npu = self.client.npu()
    except RuntimeError as e:
      raise RuntimeError(f"NPU not available via HAL: {e}")
    
    # Get RGA backend for hardware-accelerated preprocessing
    try:
      self.rga = self.client.rga()
    except RuntimeError:
      self.rga = None  # RGA optional - will use cv2 fallback

    self.model_path = Path(model_path)
    if not self.model_path.exists():
      raise FileNotFoundError(f"RKNN model not found: {self.model_path}")

    self.model_format = model_format.lower()
    if self.model_format not in {"yolov5", "yolov8"}:
      raise ValueError("model_format must be 'yolov5' or 'yolov8'")

    self.input_size = (int(input_size[0]), int(input_size[1]))
    self.obj_threshold = float(obj_threshold)
    self.nms_threshold = float(nms_threshold)
    self.classes = list(classes) if classes is not None else list(COCO_CLASSES)
    self.anchors = (
      ((10.0, 13.0), (16.0, 30.0), (33.0, 23.0)),
      ((30.0, 61.0), (62.0, 45.0), (59.0, 119.0)),
      ((116.0, 90.0), (156.0, 198.0), (373.0, 326.0)),
    )

    # Load model via HAL
    self._load_model(use_npu_cores)

    print(f"✅ YOLO RKNN model loaded via HAL: {self.model_path.name} ({self.model_format}, input={self.input_size})")

  def _parse_core_mask(self, use_npu_cores: str) -> int:
    """Parse NPU core specification into core mask."""
    if use_npu_cores == "all":
      return 0xFF
    elif use_npu_cores == "0":
      return 0x01
    elif use_npu_cores == "1":
      return 0x02
    elif use_npu_cores == "2":
      return 0x04
    elif "," in use_npu_cores:
      cores = [int(c.strip()) for c in use_npu_cores.split(",")]
      mask = 0
      for core in cores:
        mask |= (1 << core)
      return mask
    else:
      return 0xFF

  def _load_model(self, use_npu_cores: str):
    """Load model through HAL."""
    from openpilot.system.inferenced.compute import ModelConfig
    
    core_mask = self._parse_core_mask(use_npu_cores)
    
    config = ModelConfig(
      name='yolo',
      path=str(self.model_path),
      npu_cores=core_mask
    )
    
    if not self.npu.load_model(config):
      raise RuntimeError(f"Failed to load model via HAL: {self.model_path}")

  def close(self) -> None:
    """Release resources."""
    self.client.release()

  def __del__(self) -> None:
    self.close()

  def infer(self, frame_bgr: np.ndarray) -> list[Detection]:
    """Run detection on a BGR frame."""
    rgb, ratio, dwdh = self._letterbox(frame_bgr)
    
    # Run inference through HAL
    result = self.npu.infer('yolo', {'input': rgb})
    
    if not result.success:
      print(f"YOLO inference failed: {result.error_message}")
      return []
    
    outputs = result.outputs.get('outputs', [])
    
    # Detect segmentation model
    is_segmentation = len(outputs) == 13 and self.model_format == "yolov8"

    if self.model_format == "yolov5":
      boxes, scores, classes, masks = self._postprocess_v5(outputs, is_segmentation)
    else:
      boxes, scores, classes, masks = self._postprocess_v8(outputs, is_segmentation)

    # Scale boxes back to original image
    detections = []
    for i in range(len(boxes)):
      x1, y1, x2, y2 = boxes[i]
      # Remove padding
      x1, y1, x2, y2 = (x1 - dwdh[0]) / ratio, (y1 - dwdh[1]) / ratio, (x2 - dwdh[0]) / ratio, (y2 - dwdh[1]) / ratio
      
      detections.append(Detection(
        class_id=int(classes[i]),
        class_name=self.classes[int(classes[i])],
        confidence=float(scores[i]),
        bbox=(float(x1), float(y1), float(x2), float(y2)),
        mask=masks[i] if masks is not None else None
      ))

    return detections

  def _letterbox(
    self,
    img: np.ndarray,
    color: tuple = (114, 114, 114),
  ) -> tuple[np.ndarray, float, tuple]:
    """Resize and pad image to input_size while maintaining aspect ratio."""
    shape = img.shape[:2]  # (height, width)
    new_shape = self.input_size[::-1]  # (height, width)

    # Scale ratio
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
    new_unpad = (int(round(shape[1] * r)), int(round(shape[0] * r)))

    # Compute padding
    dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]
    dw /= 2
    dh /= 2

    # Resize using RGA hardware accelerator if available, else cv2 fallback
    if shape[::-1] != new_unpad:
      if self.rga is not None:
        try:
          result = self.rga.infer(
            model_name='scale',
            inputs={'src': img, 'width': new_unpad[0], 'height': new_unpad[1]}
          )
          if result.success:
            img = result.outputs['output']
          else:
            img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
        except Exception:
          img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
      else:
        img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)

    # Add border
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)

    # Convert BGR to RGB using RGA if available, else cv2 fallback
    if self.rga is not None:
      try:
        result = self.rga.infer(
          model_name='cvtColor',
          inputs={'src': img}
        )
        if result.success:
          img = result.outputs['output']
        else:
          img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
      except Exception:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    else:
      img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = np.expand_dims(img, axis=0)  # Add batch dimension

    return img, r, (dw, dh)

  def _postprocess_v5(
    self,
    outputs: list[np.ndarray],
    is_segmentation: bool = False,
  ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray | None]:
    """Post-process YOLOv5 outputs."""
    # YOLOv5 output format: [batch, num_anchors, grid_h, grid_w, 5 + num_classes]
    # or for segmentation: [batch, num_anchors, grid_h, grid_w, 5 + num_classes + mask_coeffs]
    
    all_boxes = []
    all_scores = []
    all_classes = []
    all_masks = []

    for i, output in enumerate(outputs):
      if output.ndim == 4:
        output = output.reshape(output.shape[0], -1, output.shape[-1])
      
      # Extract predictions
      batch_size, num_preds, num_attrs = output.shape
      
      # For each anchor
      for b in range(batch_size):
        for p in range(num_preds):
          pred = output[b, p]
          
          # YOLOv5: [x, y, w, h, obj_conf, class_probs...]
          obj_conf = pred[4]
          if obj_conf < self.obj_threshold:
            continue
          
          class_probs = pred[5:]
          class_id = np.argmax(class_probs)
          class_conf = class_probs[class_id]
          
          confidence = obj_conf * class_conf
          if confidence < self.obj_threshold:
            continue
          
          # Convert to x1, y1, x2, y2
          x, y, w, h = pred[0:4]
          x1 = x - w / 2
          y1 = y - h / 2
          x2 = x + w / 2
          y2 = y + h / 2
          
          all_boxes.append([x1, y1, x2, y2])
          all_scores.append(confidence)
          all_classes.append(class_id)

    if not all_boxes:
      return np.array([]), np.array([]), np.array([]), None

    all_boxes = np.array(all_boxes)
    all_scores = np.array(all_scores)
    all_classes = np.array(all_classes)

    # NMS
    keep = _nms(all_boxes, all_scores, self.nms_threshold)

    return all_boxes[keep], all_scores[keep], all_classes[keep], None

  def _postprocess_v8(
    self,
    outputs: list[np.ndarray],
    is_segmentation: bool = False,
  ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray | None]:
    """Post-process YOLOv8 outputs."""
    # YOLOv8 output format is different from YOLOv5
    # Typically: [batch, 4 + num_classes + mask_coeffs, num_predictions]
    
    if not outputs:
      return np.array([]), np.array([]), np.array([]), None

    # YOLOv8 typically has outputs[0] as the main detection output
    predictions = outputs[0]
    
    if predictions.ndim == 3:
      predictions = predictions[0]  # Remove batch dimension
    
    # Transpose if needed: [num_predictions, attrs] -> expected format
    if predictions.shape[0] < predictions.shape[1]:
      predictions = predictions.T

    all_boxes = []
    all_scores = []
    all_classes = []

    num_classes = len(self.classes)
    
    for pred in predictions:
      # YOLOv8: [x_center, y_center, w, h, class_probs...]
      if len(pred) < 4 + num_classes:
        continue
      
      class_probs = pred[4:4 + num_classes]
      class_id = np.argmax(class_probs)
      confidence = class_probs[class_id]
      
      if confidence < self.obj_threshold:
        continue
      
      # Convert center format to corner format
      x_center, y_center, w, h = pred[0:4]
      x1 = x_center - w / 2
      y1 = y_center - h / 2
      x2 = x_center + w / 2
      y2 = y_center + h / 2
      
      all_boxes.append([x1, y1, x2, y2])
      all_scores.append(confidence)
      all_classes.append(class_id)

    if not all_boxes:
      return np.array([]), np.array([]), np.array([]), None

    all_boxes = np.array(all_boxes)
    all_scores = np.array(all_scores)
    all_classes = np.array(all_classes)

    # NMS
    keep = _nms(all_boxes, all_scores, self.nms_threshold)

    return all_boxes[keep], all_scores[keep], all_classes[keep], None
