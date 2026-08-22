#!/usr/bin/env python3
"""Optional tinygrad/eGPU camera detector and shadow runner.

The eGPU is owned exclusively by ``inferenced``. This module only submits
single-input/single-output detector jobs over IPC and never falls back to a
process-local HAL, preventing sided/reard from racing for the USB GPU.

Shadow results are compared and logged but never published or used for driving.
The existing Hailo/local path remains authoritative until hardware and replay
validation establish explicit promotion gates.
"""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.sided.simple_tracker import SideObject
from openpilot.system.inferenced.client import InferenceClient
from openpilot.system.inferenced.compute import BackendType


COCO_CAMERA_CLASSES: dict[int, str] = {
  0: 'person',
  1: 'bicycle',
  2: 'car',
  3: 'motorcycle',
  5: 'bus',
  7: 'truck',
}


@dataclass(frozen=True)
class EgpuCameraInference:
  success: bool
  detections: list[SideObject]
  latency_ms: float
  error: str = ""


@dataclass(frozen=True)
class EgpuShadowComparison:
  camera: str
  reference_count: int
  shadow_count: int
  matched_count: int
  mean_iou: float
  latency_ms: float


class EgpuCameraDetector:
  """One YOLOv8 ONNX model executed by tinygrad on the centralized eGPU."""

  INPUT_SIZE = (640, 640)  # width, height
  CONF_THRESHOLD = 0.35
  NMS_THRESHOLD = 0.45
  POLL_TIMEOUT_MS = 250.0

  def __init__(self, daemon_name: str, model_name: str, client: InferenceClient | None = None) -> None:
    self._client = client or InferenceClient(daemon_name, use_ipc=True)
    self.model_name = model_name

  @classmethod
  def preprocess(cls, frame_bgr: np.ndarray) -> np.ndarray:
    """Return normalized FP16 NCHW input.

    FP16 halves USB traffic relative to FP32. EgpuBackend casts the tensor in
    VRAM to the ONNX input dtype before invoking OnnxRunner.
    """
    target_w, target_h = cls.INPUT_SIZE
    resized = cv2.resize(frame_bgr, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(rgb.transpose(2, 0, 1)[None], dtype=np.float16) / np.float16(255.0)

  def infer_tensor(self, tensor: np.ndarray, original_shape: tuple[int, int]) -> EgpuCameraInference:
    start = time.monotonic()
    result = self._client.submit_job(
      backend_type=BackendType.EGPU,
      model_name=self.model_name,
      input_array=tensor,
      priority=1,
      timeout_ms=200,
      poll_timeout_ms=self.POLL_TIMEOUT_MS,
      allow_direct_fallback=False,
    )
    latency_ms = (time.monotonic() - start) * 1000.0
    if not result.success:
      return EgpuCameraInference(False, [], latency_ms, result.error_message or "eGPU inference failed")

    try:
      detections = self.postprocess(result.outputs, original_shape)
      return EgpuCameraInference(True, detections, latency_ms)
    except Exception as e:
      return EgpuCameraInference(False, [], latency_ms, f"invalid detector output: {e}")

  @classmethod
  def postprocess(cls, outputs: dict[str, Any], original_shape: tuple[int, int]) -> list[SideObject]:
    """Decode either raw Ultralytics YOLOv8 output or decoded Nx6 output."""
    if not outputs:
      return []
    raw = np.asarray(outputs.get('output', next(iter(outputs.values()))))
    if raw.ndim == 3 and raw.shape[0] == 1:
      raw = raw[0]
    if raw.ndim != 2:
      raise ValueError(f"expected a 2-D detector output, got {raw.shape}")

    # Ultralytics exports [84, 8400]; decoded engines commonly return [N, 6].
    if raw.shape[0] in (84, 85) and raw.shape[1] > raw.shape[0]:
      raw = raw.T

    if raw.shape[1] == 6:
      candidates = cls._decode_nx6(raw)
    elif raw.shape[1] in (84, 85):
      candidates = cls._decode_yolo_rows(raw)
    else:
      raise ValueError(f"unsupported detector output shape {raw.shape}")

    orig_h, orig_w = original_shape
    input_w, input_h = cls.INPUT_SIZE
    detections: list[SideObject] = []
    for box, score, class_id in candidates:
      if score < cls.CONF_THRESHOLD or class_id not in COCO_CAMERA_CLASSES:
        continue
      x1, y1, x2, y2 = box
      x1 = float(np.clip(x1 * orig_w / input_w, 0, orig_w - 1))
      x2 = float(np.clip(x2 * orig_w / input_w, 0, orig_w - 1))
      y1 = float(np.clip(y1 * orig_h / input_h, 0, orig_h - 1))
      y2 = float(np.clip(y2 * orig_h / input_h, 0, orig_h - 1))
      if x2 <= x1 or y2 <= y1:
        continue
      detections.append(SideObject(
        label=COCO_CAMERA_CLASSES[class_id],
        confidence=score,
        bbox_2d=(x1, y1, x2, y2),
      ))
    return cls._nms(detections)

  @classmethod
  def _decode_nx6(cls, rows: np.ndarray) -> list[tuple[tuple[float, float, float, float], float, int]]:
    coords_normalized = bool(rows.size and np.nanmax(np.abs(rows[:, :4])) <= 2.0)
    input_w, input_h = cls.INPUT_SIZE
    decoded = []
    for row in rows:
      x1, y1, x2, y2 = (float(v) for v in row[:4])
      if coords_normalized:
        # Decoded Nx6 convention is normalized xyxy.
        x1, x2 = x1 * input_w, x2 * input_w
        y1, y2 = y1 * input_h, y2 * input_h
      decoded.append(((x1, y1, x2, y2), float(row[4]), int(row[5])))
    return decoded

  @classmethod
  def _decode_yolo_rows(cls, rows: np.ndarray) -> list[tuple[tuple[float, float, float, float], float, int]]:
    decoded = []
    has_objectness = rows.shape[1] == 85
    for row in rows:
      scores = row[5:] * row[4] if has_objectness else row[4:]
      class_id = int(np.argmax(scores))
      score = float(scores[class_id])
      if score < cls.CONF_THRESHOLD:
        continue
      cx, cy, width, height = (float(v) for v in row[:4])
      decoded.append(((cx - width / 2.0, cy - height / 2.0,
                       cx + width / 2.0, cy + height / 2.0), score, class_id))
    return decoded

  @classmethod
  def _nms(cls, detections: list[SideObject]) -> list[SideObject]:
    remaining = sorted(detections, key=lambda d: d.confidence, reverse=True)
    keep: list[SideObject] = []
    while remaining:
      current = remaining.pop(0)
      keep.append(current)
      remaining = [candidate for candidate in remaining
                   if candidate.label != current.label or cls._iou(current.bbox_2d, candidate.bbox_2d) < cls.NMS_THRESHOLD]
    return keep

  @staticmethod
  def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0.0 else 0.0


class EgpuCameraShadowRunner:
  """One-in-flight asynchronous shadow runner with failure backoff."""

  LOG_INTERVAL = 50

  def __init__(self, daemon_name: str, model_name: str,
               detector: EgpuCameraDetector | None = None) -> None:
    self.detector = detector or EgpuCameraDetector(daemon_name, model_name)
    self.model_name = model_name
    self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"{daemon_name}-egpu-shadow")
    self._future: Future[EgpuCameraInference] | None = None
    self._camera = ""
    self._reference: tuple[tuple[str, tuple[float, ...]], ...] = ()
    self._failures = 0
    self._retry_after = 0.0
    self.submitted = 0
    self.completed = 0
    self.last_comparison: EgpuShadowComparison | None = None

  def submit(self, camera: str, frame_bgr: np.ndarray | None, reference: list[SideObject]) -> bool:
    """Poll the prior job and submit the latest frame if the worker is free."""
    self.poll()
    if frame_bgr is None or self._future is not None or time.monotonic() < self._retry_after:
      return False
    try:
      tensor = self.detector.preprocess(frame_bgr)
    except Exception as e:
      cloudlog.debug("EgpuCameraShadowRunner: preprocess failed for %s: %s", camera, e)
      return False

    self._camera = camera
    self._reference = tuple((det.label, tuple(det.bbox_2d)) for det in reference)
    self._future = self._executor.submit(self.detector.infer_tensor, tensor, frame_bgr.shape[:2])
    self.submitted += 1
    return True

  def poll(self) -> EgpuShadowComparison | None:
    if self._future is None or not self._future.done():
      return None
    future, camera, reference = self._future, self._camera, self._reference
    self._future = None
    try:
      result = future.result()
    except Exception as e:
      result = EgpuCameraInference(False, [], 0.0, str(e))

    if not result.success:
      self._failures += 1
      backoff_s = min(30.0, float(2 ** min(self._failures - 1, 5)))
      self._retry_after = time.monotonic() + backoff_s
      if self._failures == 1 or self._failures % 10 == 0:
        cloudlog.warning("eGPU %s shadow failed (%s), retry in %.0fs: %s", self.model_name, camera, backoff_s, result.error)
      return None

    self._failures = 0
    self._retry_after = 0.0
    comparison = self._compare(camera, reference, result)
    self.completed += 1
    self.last_comparison = comparison
    if self.completed % self.LOG_INTERVAL == 0:
      cloudlog.info(
        "eGPU %s shadow %s: reference=%d shadow=%d matched=%d mean_iou=%.3f latency=%.1fms",
        self.model_name, comparison.camera, comparison.reference_count, comparison.shadow_count,
        comparison.matched_count, comparison.mean_iou, comparison.latency_ms,
      )
    return comparison

  @classmethod
  def _compare(cls, camera: str, reference: tuple[tuple[str, tuple[float, ...]], ...],
               result: EgpuCameraInference) -> EgpuShadowComparison:
    candidates = list(result.detections)
    ious: list[float] = []
    for label, bbox in reference:
      best_index = -1
      best_iou = 0.0
      for index, candidate in enumerate(candidates):
        if candidate.label != label:
          continue
        overlap = EgpuCameraDetector._iou(bbox, candidate.bbox_2d)
        if overlap > best_iou:
          best_iou, best_index = overlap, index
      if best_index >= 0 and best_iou >= 0.30:
        ious.append(best_iou)
        candidates.pop(best_index)
    return EgpuShadowComparison(
      camera=camera,
      reference_count=len(reference),
      shadow_count=len(result.detections),
      matched_count=len(ious),
      mean_iou=float(np.mean(ious)) if ious else 0.0,
      latency_ms=result.latency_ms,
    )

  def close(self) -> None:
    self._executor.shutdown(wait=False, cancel_futures=True)
