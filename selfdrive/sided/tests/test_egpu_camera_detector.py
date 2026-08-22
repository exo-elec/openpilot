#!/usr/bin/env python3

from __future__ import annotations

import numpy as np

from openpilot.selfdrive.sided.egpu_camera_detector import (
  EgpuCameraDetector,
  EgpuCameraInference,
  EgpuCameraShadowRunner,
)
from openpilot.selfdrive.sided.simple_tracker import SideObject
from openpilot.system.inferenced.compute import InferenceResult


class _FakeClient:
  def __init__(self, output: np.ndarray | None = None, error: str = "") -> None:
    self.output = output
    self.error = error
    self.calls = []

  def submit_job(self, **kwargs):
    self.calls.append(kwargs)
    if self.error:
      return InferenceResult(success=False, error_message=self.error)
    return InferenceResult(success=True, outputs={'output': self.output})


def test_preprocess_is_compact_fp16_nchw():
  frame = np.zeros((720, 1280, 3), dtype=np.uint8)
  frame[:, :, 2] = 255
  tensor = EgpuCameraDetector.preprocess(frame)
  assert tensor.shape == (1, 3, 640, 640)
  assert tensor.dtype == np.float16
  assert tensor.nbytes == 1 * 3 * 640 * 640 * 2
  assert float(tensor[0, 0, 0, 0]) == 1.0  # BGR red -> RGB channel 0


def test_raw_yolov8_output_is_decoded_and_scaled():
  raw = np.zeros((1, 84, 2), dtype=np.float32)
  raw[0, :4, 0] = [320.0, 320.0, 100.0, 200.0]
  raw[0, 4 + 2, 0] = 0.90  # COCO car
  raw[0, :4, 1] = [100.0, 100.0, 20.0, 40.0]
  raw[0, 4 + 4, 1] = 0.99  # class outside the side/rear allowlist

  detections = EgpuCameraDetector.postprocess({'output': raw}, (720, 1280))
  assert len(detections) == 1
  assert detections[0].label == 'car'
  assert detections[0].bbox_2d == (540.0, 247.5, 740.0, 472.5)


def test_side_and_rear_use_independent_model_ids_and_fail_closed():
  decoded = np.array([[[100.0, 100.0, 200.0, 200.0, 0.8, 2.0]]], dtype=np.float32)
  side_client = _FakeClient(decoded)
  rear_client = _FakeClient(decoded)
  tensor = np.zeros((1, 3, 640, 640), dtype=np.float16)

  side = EgpuCameraDetector('sided', 'side_yolo_egpu', side_client)
  rear = EgpuCameraDetector('reard', 'rear_yolo_egpu', rear_client)
  assert side.infer_tensor(tensor, (640, 640)).success
  assert rear.infer_tensor(tensor, (640, 640)).success
  assert side_client.calls[0]['model_name'] == 'side_yolo_egpu'
  assert rear_client.calls[0]['model_name'] == 'rear_yolo_egpu'
  assert side_client.calls[0]['allow_direct_fallback'] is False
  assert rear_client.calls[0]['allow_direct_fallback'] is False


def test_shadow_comparison_matches_by_class_and_iou():
  reference = (
    ('car', (10.0, 10.0, 30.0, 30.0)),
    ('person', (100.0, 100.0, 120.0, 160.0)),
  )
  result = EgpuCameraInference(True, [
    SideObject(label='car', bbox_2d=(11.0, 11.0, 31.0, 31.0)),
    SideObject(label='truck', bbox_2d=(100.0, 100.0, 120.0, 160.0)),
  ], 12.5)
  comparison = EgpuCameraShadowRunner._compare('side_left', reference, result)
  assert comparison.reference_count == 2
  assert comparison.shadow_count == 2
  assert comparison.matched_count == 1
  assert comparison.mean_iou > 0.8

