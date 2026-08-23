#!/usr/bin/env python3
"""Unit tests for RKNNBackend's driving-model-specific input handling —
fp16 cast, NHWC layout swap, and the big_img affine mitigation, all ported
from bukapilot's proven KA2 runner (see rockchip_npu.py's _FP16_MODELS /
_NHWC_VISION_MODELS). These exercise the real-hardware code path directly
(RKNNLite isn't installed on dev PC, so ``get_hal()``-based tests only cover
the mock path)."""

from __future__ import annotations

import numpy as np
import pytest

from openpilot.system.inferenced.rockchip_npu import RKNNBackend


class _FakeRKNN:
  def __init__(self):
    self.last_inputs = None
    self.last_data_type = None

  def inference(self, inputs, data_type=None):
    self.last_inputs = inputs
    self.last_data_type = data_type
    return [np.zeros((1, 8), dtype=np.float32)]


@pytest.fixture
def backend():
  b = RKNNBackend()
  b._initialized = True
  b._use_mock = False
  return b


def _inject(backend, model_name):
  fake = _FakeRKNN()
  backend._loaded_models[model_name] = fake
  return fake


def _vision_inputs():
  return {
    "img": np.zeros((1, 12, 128, 256), dtype=np.uint8),
    "big_img": np.zeros((1, 12, 128, 256), dtype=np.uint8),
  }


def test_driving_vision_and_policy_cast_to_fp16(backend):
  fake = _inject(backend, "driving_vision")
  result = backend.infer("driving_vision", _vision_inputs())
  assert result.success
  assert fake.last_data_type == "float16"
  assert all(arr.dtype == np.float16 for arr in fake.last_inputs)


def test_driving_vision_nhwc_layout_by_default(backend, monkeypatch):
  monkeypatch.setattr("openpilot.system.inferenced.rockchip_npu._VISION_LAYOUT", "nhwc")
  fake = _inject(backend, "driving_vision")
  backend.infer("driving_vision", _vision_inputs())
  assert fake.last_inputs[0].shape == (1, 128, 256, 12)
  assert fake.last_inputs[1].shape == (1, 128, 256, 12)


def test_driving_vision_nchw_layout_when_enforced(backend, monkeypatch):
  monkeypatch.setattr("openpilot.system.inferenced.rockchip_npu._VISION_LAYOUT", "nchw")
  fake = _inject(backend, "driving_vision")
  backend.infer("driving_vision", _vision_inputs())
  assert fake.last_inputs[0].shape == (1, 12, 128, 256)
  assert fake.last_inputs[1].shape == (1, 12, 128, 256)


def test_big_img_affine_applied_before_cast(backend, monkeypatch):
  monkeypatch.setattr("openpilot.system.inferenced.rockchip_npu._NHWC_BIGIMG_AFFINE_ENABLE", True)
  monkeypatch.setattr("openpilot.system.inferenced.rockchip_npu._NHWC_BIGIMG_SCALE", 0.5)
  monkeypatch.setattr("openpilot.system.inferenced.rockchip_npu._NHWC_BIGIMG_BIAS", 10.0)
  fake = _inject(backend, "driving_vision")
  inputs = _vision_inputs()
  inputs["big_img"][:] = 100
  backend.infer("driving_vision", inputs)
  # 100 * 0.5 + 10 = 60, then cast to fp16.
  assert np.allclose(fake.last_inputs[1].astype(np.float32), 60.0)
  # img (not big_img) is untouched by the affine.
  assert np.allclose(fake.last_inputs[0].astype(np.float32), 0.0)


def test_big_img_affine_clips_to_valid_range(backend, monkeypatch):
  monkeypatch.setattr("openpilot.system.inferenced.rockchip_npu._NHWC_BIGIMG_AFFINE_ENABLE", True)
  monkeypatch.setattr("openpilot.system.inferenced.rockchip_npu._NHWC_BIGIMG_SCALE", 3.0)
  monkeypatch.setattr("openpilot.system.inferenced.rockchip_npu._NHWC_BIGIMG_BIAS", 0.0)
  fake = _inject(backend, "driving_vision")
  inputs = _vision_inputs()
  inputs["big_img"][:] = 200  # 200 * 3.0 = 600, must clip to 255
  backend.infer("driving_vision", inputs)
  assert np.allclose(fake.last_inputs[1].astype(np.float32), 255.0)


def test_big_img_affine_disabled(backend, monkeypatch):
  monkeypatch.setattr("openpilot.system.inferenced.rockchip_npu._NHWC_BIGIMG_AFFINE_ENABLE", False)
  fake = _inject(backend, "driving_vision")
  inputs = _vision_inputs()
  inputs["big_img"][:] = 100
  backend.infer("driving_vision", inputs)
  assert np.allclose(fake.last_inputs[1].astype(np.float32), 100.0)


def test_other_rknn_models_unaffected_by_layout_or_affine(backend, monkeypatch):
  monkeypatch.setattr("openpilot.system.inferenced.rockchip_npu._VISION_LAYOUT", "nhwc")
  monkeypatch.setattr("openpilot.system.inferenced.rockchip_npu._NHWC_BIGIMG_AFFINE_ENABLE", True)
  fake = _inject(backend, "ppliteseg")
  inputs = {"input": np.zeros((1, 3, 128, 256), dtype=np.float32)}
  backend.infer("ppliteseg", inputs)
  assert fake.last_inputs[0].shape == (1, 3, 128, 256)
  assert fake.last_inputs[0].dtype == np.float32
  assert fake.last_data_type is None
