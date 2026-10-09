#!/usr/bin/env python3
"""YoloDetector on RKNN: core mask, output layouts, and the side/rear rate split."""

from __future__ import annotations

import sys
import tempfile
import unittest  # noqa: TID251
from unittest.mock import MagicMock, patch  # noqa: TID251

import numpy as np

from openpilot.selfdrive.sided.yolo_detector import DETECTION_CORE, YoloDetector
from openpilot.system.inferenced.compute import InferenceResult


def _head(label_id: int = 2) -> np.ndarray:
  head = np.zeros((1, 84, 100), dtype=np.float32)
  head[0, :4, 7] = [320, 320, 100, 200]
  head[0, 4 + label_id, 7] = 0.9
  return head


class _Npu:
  def __init__(self, result=None, load_ok=True):
    self.configs = []
    self.result = result or InferenceResult(success=True, outputs={'outputs': [_head()]})
    self.load_ok = load_ok
    self.inputs = []

  def load_model(self, config):
    self.configs.append(config)
    return self.load_ok

  def infer(self, model_name, inputs):
    self.inputs.append(inputs['input'])
    return self.result


def _detector(npu=None, **kw):
  with tempfile.NamedTemporaryFile(suffix='.rknn') as f:
    return YoloDetector(kw.pop('daemon', 'sided'), model_path=f.name, backend=npu or _Npu(), **kw)


class TestYoloDetector(unittest.TestCase):

  def test_loads_on_the_freed_core_as_a_mask(self):
    npu = _Npu()
    det = _detector(npu)
    self.assertTrue(det.is_available)
    self.assertEqual(det.core_id, DETECTION_CORE)
    self.assertEqual(npu.configs[0].npu_cores, 1 << DETECTION_CORE)  # RKNNLite core_mask, not an index

  def test_detects_from_the_rknn_head(self):
    npu = _Npu()
    objs = _detector(npu).detect(np.zeros((480, 640, 3), dtype=np.uint8))
    self.assertEqual([o.label for o in objs], ['car'])
    self.assertEqual(npu.inputs[0].shape, (1, 640, 640, 3))

  def test_names_keep_contexts_apart(self):
    self.assertEqual(_detector(daemon='monod', name='road').model_name, 'yolo_monod_road')
    self.assertEqual(_detector(daemon='monod', name='tele').model_name, 'yolo_monod_tele')
    self.assertEqual(_detector(daemon='reard').model_name, 'yolo_reard')

  def test_failures_become_a_fault(self):
    det = _detector(_Npu(result=InferenceResult(success=False, error_message='x')))
    for _ in range(YoloDetector.FAULT_THRESHOLD):
      self.assertEqual(det.detect(np.zeros((64, 64, 3), dtype=np.uint8)), [])
    self.assertTrue(det.is_fault)

  def test_no_model_is_unavailable_not_a_crash(self):
    det = YoloDetector('sided', backend=_Npu(load_ok=False), model_path='/nonexistent.rknn')
    self.assertFalse(det.is_available)
    self.assertEqual(det.detect(np.zeros((64, 64, 3), dtype=np.uint8)), [])

  def test_nms_list_layout(self):
    rows = np.array([[100, 100, 300, 400, 0.9, 2]], dtype=np.float32)
    self.assertEqual([o.label for o in YoloDetector.parse_outputs({'output': rows[None]}, (640, 640))], ['car'])

  def test_raw_head_decode_and_nms(self):
    head = np.zeros((1, 84, 100), dtype=np.float32)
    head[0, :4, 7] = [320, 320, 100, 200]
    head[0, 4 + 0, 7] = 0.8
    head[0, :4, 8] = [322, 321, 100, 200]   # near-duplicate NMS must drop
    head[0, 4 + 0, 8] = 0.7
    head[0, :4, 9] = [100, 100, 50, 50]     # below threshold
    head[0, 4 + 2, 9] = 0.1
    objs = YoloDetector.parse_outputs({'output_0': head}, (640, 640))
    self.assertEqual([o.label for o in objs], ['person'])
    self.assertAlmostEqual(objs[0].bbox_2d[0], 270.0, places=3)

  def test_raw_head_transposed(self):
    head = np.zeros((100, 84), dtype=np.float32)
    head[3, :4] = [320, 320, 100, 100]
    head[3, 4 + 7] = 0.9  # truck
    self.assertEqual([o.label for o in YoloDetector.parse_outputs({'output': head}, (640, 640))], ['truck'])


def _daemon_module(name):
  # sided/reard import cereal.messaging at module scope; the processors need none of it
  stubs = {m: sys.modules.get(m, MagicMock()) for m in ('cereal.messaging', 'msgq', 'msgq.visionipc')}
  with patch.dict(sys.modules, stubs):
    return __import__(f'openpilot.selfdrive.{name}.{name}', fromlist=['_'])


class TestSideRearRates(unittest.TestCase):

  def test_both_side_cameras_every_frame(self):
    RknnSideProcessor = _daemon_module('sided').RknnSideProcessor
    det = MagicMock()
    det.detect.side_effect = lambda f: [f'dets-{int(f[0, 0, 0])}']
    proc = RknnSideProcessor(det)
    left = np.zeros((4, 4, 3), dtype=np.uint8)
    right = np.ones((4, 4, 3), dtype=np.uint8)
    for _ in range(2):
      self.assertEqual(proc.detect_both(left, right, True, True), (['dets-0'], ['dets-1']))
    self.assertEqual(det.detect.call_count, 4)  # 20 Hz each

  def test_disabled_or_missing_camera_is_skipped(self):
    RknnSideProcessor = _daemon_module('sided').RknnSideProcessor
    det = MagicMock()
    det.detect.return_value = ['d']
    proc = RknnSideProcessor(det)
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    self.assertEqual(proc.detect_both(frame, None, True, True), (['d'], []))
    self.assertEqual(proc.detect_both(frame, frame, True, False), (['d'], []))
    self.assertEqual(det.detect.call_count, 2)

  def test_rear_every_frame(self):
    RknnRearProcessor = _daemon_module('reard').RknnRearProcessor
    det = MagicMock()
    det.is_available = True
    det.detect.return_value = []
    proc = RknnRearProcessor(det)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    for _ in range(4):
      proc.detect(frame)
    self.assertEqual(det.detect.call_count, 4)  # 20 Hz


if __name__ == '__main__':
  unittest.main()
