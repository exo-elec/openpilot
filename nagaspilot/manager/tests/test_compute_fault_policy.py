"""Host policy tests: only core CPU/RKNN compute faults disengage."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest  # noqa: TID251
from unittest.mock import Mock, patch  # noqa: TID251

ROOT = Path(__file__).resolve().parents[3]


def load(name, path):
  spec = importlib.util.spec_from_file_location(name, path)
  module = importlib.util.module_from_spec(spec)
  sys.modules[name] = module
  spec.loader.exec_module(module)
  return module


policy = load('eop_compute_policy_test', ROOT / 'nagaspilot/manager/compute_fault_policy.py')
POLICY_MODULE = 'openpilot.nagaspilot.manager.compute_fault_policy'


def stub(name, **attrs):
  module = ModuleType(name)
  module.__dict__.update(attrs)
  return module


class Names:
  def __getattr__(self, name):
    return name


class TestComputeFaultPolicy(unittest.TestCase):
  def test_optional_events_do_not_disable_or_block_engagement(self):
    implementation = load('compute_policy_events_test', ROOT / 'nagaspilot/manager/eop_events.py')
    events = {'cameraMalfunction': {}}
    implementation.register_eop_events(events, EventName=Names(), ET=Names(), Alert=Mock(),
                                       AlertStatus=Names(), AlertSize=Names(), Priority=Names(), VisualAlert=Names(), AudibleAlert=Names(),
                                       ImmediateDisableAlert=Mock(), NoEntryAlert=Mock(), NormalPermanentAlert=Mock(),
                                       EngagementAlert=Mock(), soft_disable_alert=Mock())
    for event in ('stereoFault', 'gridFault', 'rgaFault', 'mppFault', 'pointcloudFault'):
      with self.subTest(event=event):
        self.assertEqual(set(events[event]), {'PERMANENT'})
    for event in ('inferenceFault', 'monoFault'):
      self.assertIn('IMMEDIATE_DISABLE', events[event])
      self.assertIn('NO_ENTRY', events[event])
    self.assertIn('SOFT_DISABLE', events['healthDegradedStop'])
    self.assertIn('IMMEDIATE_DISABLE', events['healthCriticalStop'])

  def test_optional_inference_reasons_do_not_disengage_with_healthy_rknn(self):
    for reason in ('gpu_failed', 'hailo_failed', 'dx_m1_failed', 'rga_failed', 'mpp_failed', 'initialization_failed'):
      status = SimpleNamespace(enabled=True, npuAvailable=True, fault=True, faultReason=reason)
      self.assertFalse(policy.critical_inference_fault(status))

  def test_rknn_loss_disengages_even_if_legacy_fault_flag_is_false(self):
    self.assertTrue(policy.critical_inference_fault(SimpleNamespace(enabled=True, npuAvailable=False, fault=False, faultReason='')))

  def test_cpu_and_rknn_failure_reasons_disengage(self):
    for reason in ('cpu_failed', 'cpu_deadline_missed', 'rknn_unavailable', 'rknn_failed', 'npu_failed'):
      status = SimpleNamespace(enabled=True, npuAvailable=True, fault=True, faultReason=reason)
      self.assertTrue(policy.critical_inference_fault(status))
    self.assertFalse(policy.critical_inference_fault(SimpleNamespace(enabled=False, npuAvailable=False)))

  def test_arm_and_unknown_hosts_cannot_mock_rknn_readiness(self):
    for machine in ('aarch64', 'arm64', 'armv7l', 'unknown'):
      self.assertFalse(policy.allow_rknn_mock(machine))
    self.assertTrue(policy.allow_rknn_mock('x86_64'))
    self.assertTrue(policy.allow_rknn_mock('AMD64'))


class TestInferenceStatus(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.types = SimpleNamespace(NPU=1, ACL=2, HAILO_8=6, DX_M1=8, ACCEL=10)
    modules = {
      POLICY_MODULE: policy,
      'openpilot.common.realtime': stub('openpilot.common.realtime', Ratekeeper=Mock()),
      'openpilot.common.core_config': stub('openpilot.common.core_config', set_daemon_affinity=Mock()),
      'openpilot.common.swaglog': stub('openpilot.common.swaglog', cloudlog=Mock()),
      'openpilot.system.inferenced.compute': stub('openpilot.system.inferenced.compute', HAL=Mock(), HALConfig=Mock(),
                                                BackendType=cls.types, ModelConfig=Mock(), CAMERA_ACCEL_BACKENDS=()),
      'openpilot.system.inferenced.drivable': stub('openpilot.system.inferenced.drivable', CARD_INPUT_HW=(256, 256),
                                                 CARD_MODEL='test', drivable_map=Mock(), letterbox=Mock()),
    }
    with patch.dict(sys.modules, modules):
      cls.module = load('inferenced_status_policy_test', ROOT / 'system/inferenced/inferenced.py')

  def publish(self, *, present=True, ready=True, healthy=True, optional_present=False, probe_error=False):
    obj = self.module.InferenceD.__new__(self.module.InferenceD)
    npu = Mock(is_available=Mock(return_value=ready)) if present else None
    obj.hal = Mock(get_backend=Mock(side_effect=lambda kind: npu if kind == 1 else (Mock() if optional_present else None)),
                   is_backend_healthy=Mock(return_value=healthy), get_available_backends=Mock(return_value=[]),
                   get_backend_health_report=Mock(return_value={}), _models_cache={})
    if probe_error:
      obj.hal.is_backend_healthy.side_effect = RuntimeError("health probe failed")
    obj._initialized = True
    obj._tasks_completed = obj._tasks_failed = obj._total_exec_time_ms = 0
    obj._accel_assignment = {}
    message = SimpleNamespace(inferencedStatus=SimpleNamespace())
    obj._messaging = Mock(new_message=Mock(return_value=message))
    obj.pm = Mock()
    obj._publish_status()
    obj.pm.send.assert_called_once()
    return message.inferencedStatus

  def test_optional_accelerator_loss_does_not_set_core_fault(self):
    status = self.publish(optional_present=False)
    self.assertTrue(status.npuAvailable)
    self.assertFalse(status.gpuAvailable)
    self.assertFalse(status.fault)

  def test_gpu_cannot_mask_missing_or_failed_rknn(self):
    for kwargs in ({'present': False}, {'ready': False}, {'healthy': False}):
      status = self.publish(optional_present=True, **kwargs)
      self.assertTrue(status.gpuAvailable)
      self.assertTrue(status.fault)
      self.assertIn(status.faultReason, ('rknn_unavailable', 'rknn_failed'))
      self.assertTrue(policy.critical_inference_fault(status))

  def test_failed_rknn_health_probe_is_reported_as_core_fault(self):
    status = self.publish(probe_error=True)
    self.assertTrue(status.fault)
    self.assertEqual(status.faultReason, 'rknn_failed')



class TestRKNNRuntimeFailure(unittest.TestCase):
  def test_missing_runtime_fails_on_arm_and_mock_is_only_offline_x86(self):
    class Base:
      def __init__(self, kind):
        self._initialized = False
    compute = stub('openpilot.system.inferenced.compute', HardwareBackend=Base, BackendType=SimpleNamespace(NPU=1),
                   InferenceResult=Mock(), ModelConfig=Mock())
    with patch.dict(sys.modules, {POLICY_MODULE: policy, compute.__name__: compute, 'rknnlite.api': None}):
      module = load('rknn_runtime_policy_test', ROOT / 'system/inferenced/rockchip_npu.py')
      for machine in ('aarch64', 'arm64'):
        backend = module.RKNNBackend()
        with patch.object(module.platform, 'machine', return_value=machine):
          self.assertFalse(backend.initialize())
        self.assertFalse(backend._initialized)
        self.assertFalse(backend._use_mock)
      backend = module.RKNNBackend()
      with patch.object(module.platform, 'machine', return_value='x86_64'):
        self.assertTrue(backend.initialize())
      self.assertTrue(backend._use_mock)


if __name__ == '__main__':
  unittest.main()
