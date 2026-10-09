#!/usr/bin/env python3
"""The USB eGPU runs openpilot's Chestnut driving model and nothing else."""

from __future__ import annotations

import tempfile
import unittest  # noqa: TID251
from unittest.mock import MagicMock  # noqa: TID251

from openpilot.selfdrive.modeld.runners.egpu_driving_runner import EGPU_MODEL_NAME
from openpilot.system.inferenced.compute import BackendType, InferenceResult, ModelConfig
from openpilot.system.inferenced.egpu import EGPU_ALLOWED_MODELS, EgpuBackend
from openpilot.system.inferenced.inferenced import InferenceD, InferenceJob


class TestEgpuChestnutOnly(unittest.TestCase):

  def test_allowlist_is_the_chestnut_model(self):
    self.assertEqual(EGPU_ALLOWED_MODELS, frozenset({EGPU_MODEL_NAME}))

  def _backend(self):
    backend = EgpuBackend()
    backend._initialized = True
    backend._onnx_runner_cls = MagicMock()
    return backend

  def test_backend_refuses_camera_models(self):
    backend = self._backend()
    with tempfile.NamedTemporaryFile(suffix='.onnx') as f:
      for name in ('side_yolo_egpu', 'rear_yolo_egpu', 'front_road_seg_egpu', 'yolo_side', 'seg_side'):
        self.assertFalse(backend.load_model(ModelConfig(name=name, path=f.name)), name)
      backend._onnx_runner_cls.assert_not_called()

  def test_backend_loads_chestnut(self):
    backend = self._backend()
    with tempfile.NamedTemporaryFile(suffix='.onnx') as f:
      self.assertTrue(backend.load_model(ModelConfig(name=EGPU_MODEL_NAME, path=f.name)))

  def test_inferenced_rejects_other_egpu_jobs(self):
    daemon = InferenceD.__new__(InferenceD)
    daemon.hal = MagicMock()
    daemon._accel_assignment = {}
    daemon._accel_degraded = {}
    job = InferenceJob(job_id=1, daemon_name='sided', backend_type=int(BackendType.EGPU),
                       model_name='side_yolo_egpu', priority=2, timeout_ms=0)
    ok, reason = daemon._execute_job(job)
    self.assertFalse(ok)
    self.assertIn('only the Chestnut driving model', reason)
    daemon.hal.get_backend.assert_not_called()

  def test_inferenced_passes_chestnut_to_egpu(self):
    daemon = InferenceD.__new__(InferenceD)
    backend = MagicMock()
    backend.is_available.return_value = True
    backend.infer.return_value = InferenceResult(success=True, outputs={})
    daemon.hal = MagicMock()
    daemon.hal.get_backend.return_value = backend
    daemon.hal.is_model_cached.return_value = True
    job = InferenceJob(job_id=1, daemon_name='modeld', backend_type=int(BackendType.EGPU),
                       model_name=EGPU_MODEL_NAME, priority=0, timeout_ms=0)
    self.assertEqual(daemon._execute_job(job), (True, ''))

  def test_only_the_owning_hal_opens_single_owner_devices(self):
    """monod/sided/reard/gridd build a local HAL for RKNN; it must never open the card or eGPU."""
    from openpilot.system.inferenced.compute import HAL, HALConfig
    for owner, expected in ((False, set()), (True, {BackendType.HAILO_8, BackendType.DX_M1, BackendType.EGPU})):
      hal = object.__new__(HAL)
      hal.config = HALConfig(enable_npu=False, enable_acl=False, enable_rga=False, enable_mpp=False,
                             enable_onnx=False, own_exclusive_devices=owner)
      hal._backends = {}
      hal._initialized = False
      opened = []
      hal._init_backend = lambda bt, mod, cls, _opened=opened: _opened.append(bt)
      for name in ('_setup_recovery', '_setup_monitoring', '_preload_models'):
        if hasattr(HAL, name):
          setattr(hal, name, lambda *a, **k: None)
      try:
        hal.initialize()
      except Exception:
        pass
      self.assertEqual(set(opened) & {BackendType.HAILO_8, BackendType.DX_M1, BackendType.EGPU}, expected)

  def test_inferenced_owns_them(self):
    import inspect
    from openpilot.system.inferenced import inferenced
    self.assertIn('own_exclusive_devices=True', inspect.getsource(inferenced.InferenceD.__init__))

  def test_no_egpu_camera_models_registered(self):
    self.assertFalse([m for m in InferenceD.MODEL_REGISTRY if m.endswith('_egpu')])


if __name__ == '__main__':
  unittest.main()
