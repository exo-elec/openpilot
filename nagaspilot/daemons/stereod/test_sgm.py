#!/usr/bin/env python3
"""Matcher API tests: GPU is mandatory by default; CPU is explicit/offline.

Hardware accuracy tests live in test_stereo_runtime.TestRealGPUStereo and must
be explicitly requested on a GPU. Missing GPU is never converted to CPU success.
"""
import importlib.util
from pathlib import Path
import sys
import unittest  # noqa: TID251
from unittest.mock import patch  # noqa: TID251

import numpy as np

spec = importlib.util.spec_from_file_location('sgm_api_test', Path(__file__).with_name('sgm.py'))
sgm = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sgm
spec.loader.exec_module(sgm)


class TestSGM(unittest.TestCase):
  def test_default_requires_hal_gpu(self):
    with patch.object(sgm.SGM, '_allocate_buffers') as allocate, self.assertRaises(sgm.SGMDeviceError):
      sgm.SGM(sgm.SGMConfig())
    allocate.assert_not_called()

  def test_auto_does_not_fall_back_to_cpu(self):
    with self.assertRaises(sgm.SGMDeviceError):
      sgm.SGM(target='auto')

  def test_unknown_target_is_rejected(self):
    with self.assertRaises(ValueError):
      sgm.SGM(target='typo')

  def test_explicit_cpu_is_reported_as_numpy(self):
    matcher = sgm.SGM(sgm.SGMConfig(target_width=32, target_height=16, max_disparity=8), target='cpu')
    try:
      self.assertTrue(matcher.is_available())
      self.assertEqual(matcher.get_device_info()['backend'], 'numpy')
      self.assertFalse(matcher.gpu_available)
      left = np.random.default_rng(17).integers(0, 256, (16, 32), np.uint8)
      result = matcher.compute(left, np.roll(left, -3, axis=1))
      self.assertTrue(result.success)
      self.assertEqual(result.backend_used, 'numpy')
      self.assertEqual(result.disparity.shape, (16, 32))
    finally:
      matcher.release()

  def test_gpu_convenience_function_requires_context(self):
    blank = np.zeros((16, 32), np.uint8)
    with self.assertRaises(sgm.SGMDeviceError):
      sgm.compute_disparity(blank, blank)


if __name__ == '__main__':
  unittest.main()
