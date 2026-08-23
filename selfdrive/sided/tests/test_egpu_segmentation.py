#!/usr/bin/env python3

from __future__ import annotations

import numpy as np

from openpilot.selfdrive.sided.egpu_camera_detector import (
  EgpuSegmentationInference,
  EgpuSegmentationComparison,
  EgpuSegmentationShadowRunner,
)
from openpilot.system.inferenced.compute import InferenceResult


class _FakeClient:
  def __init__(self, output: np.ndarray | None = None, error: str = "") -> None:
    self.output = output
    self.error = error
    self.calls: list[dict] = []

  def submit_job(self, **kwargs):
    self.calls.append(kwargs)
    if self.error:
      return InferenceResult(success=False, error_message=self.error)
    return InferenceResult(success=True, outputs={'output': self.output})

  def wait_for_backend(self, backend_type, timeout: float = 0.0) -> bool:
    return True


def test_preprocess_is_compact_fp16_nchw():
  frame = np.zeros((720, 1280, 3), dtype=np.uint8)
  frame[:, :, 2] = 255
  tensor = EgpuSegmentationShadowRunner.preprocess(frame, (512, 288))
  assert tensor.shape == (1, 3, 288, 512)
  assert tensor.dtype == np.float16
  assert float(tensor[0, 0, 0, 0]) == 1.0  # BGR red -> RGB channel 0


def test_postprocess_logits_to_class_map():
  # [1, 19, 4, 4] logits
  logits = np.zeros((1, 19, 4, 4), dtype=np.float32)
  logits[0, 5, 1, 2] = 10.0
  class_map = EgpuSegmentationShadowRunner.postprocess({'output': logits})
  assert class_map.shape == (4, 4)
  assert class_map.dtype == np.uint8
  assert class_map[1, 2] == 5


def test_postprocess_class_map_passes_through():
  seg = np.array([[0, 1], [1, 0]], dtype=np.uint8)
  class_map = EgpuSegmentationShadowRunner.postprocess({'output': seg})
  np.testing.assert_array_equal(class_map, seg)


def test_infer_tensor_submits_with_no_direct_fallback():
  logits = np.zeros((1, 19, 4, 4), dtype=np.float32)
  client = _FakeClient(logits)
  runner = EgpuSegmentationShadowRunner('testd', 'front_road_seg_egpu', input_size=(512, 288), client=client)
  tensor = np.zeros((1, 3, 288, 512), dtype=np.float16)
  result = runner.infer_tensor(tensor)
  assert result.success
  assert client.calls[0]['model_name'] == 'front_road_seg_egpu'
  assert client.calls[0]['allow_direct_fallback'] is False


def test_comparison_with_reference_mask():
  # Shadow says classes {0,1} in a 4x4 map
  class_map = np.zeros((4, 4), dtype=np.uint8)
  class_map[:2, :] = 1  # 8 sidewalk pixels
  result = EgpuSegmentationInference(True, class_map, 10.0)

  # Reference mask is 4x4 with 4 road pixels
  reference = np.zeros((4, 4), dtype=np.uint8)
  reference[2:, :] = 1  # 8 pixels

  runner = EgpuSegmentationShadowRunner('testd', 'seg', input_size=(4, 4), class_interest={0, 1})
  comparison = runner._compare('road', reference, result)
  assert isinstance(comparison, EgpuSegmentationComparison)
  assert comparison.camera == 'road'
  assert comparison.model_name == 'seg'
  assert comparison.reference_pixels == 8
  assert comparison.shadow_pixels == 16


def test_comparison_without_reference_mask():
  class_map = np.zeros((4, 4), dtype=np.uint8)
  result = EgpuSegmentationInference(True, class_map, 5.0)
  runner = EgpuSegmentationShadowRunner('testd', 'seg', input_size=(4, 4), class_interest={0})
  comparison = runner._compare('road', None, result)
  assert comparison.mean_iou == 0.0
  assert comparison.reference_pixels == 0
  assert comparison.shadow_pixels == 16
