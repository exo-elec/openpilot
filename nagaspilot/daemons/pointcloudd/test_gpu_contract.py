"""Host tests for GPU-only stereo-to-point-cloud and degraded-source policy."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest  # noqa: TID251
from unittest.mock import Mock, patch  # noqa: TID251

import numpy as np

ROOT = Path(__file__).resolve().parents[3]


def load(name, path):
  spec = importlib.util.spec_from_file_location(name, path)
  module = importlib.util.module_from_spec(spec)
  sys.modules[name] = module
  spec.loader.exec_module(module)
  return module


support = load('pointcloud_stereo_test_support', ROOT / 'nagaspilot/daemons/stereod/test_stereo_runtime.py')
projection = load('opencl_projection_host_test', ROOT / 'system/inferenced/opencl_reproject.py')


def load_pointcloud():
  modules = {}
  def stub(name, **attrs):
    module = ModuleType(name)
    module.__dict__.update(attrs)
    modules[name] = module
    return module
  cereal = stub('cereal')
  cereal.messaging = stub('cereal.messaging')
  for name, attr in [('params', 'Params'), ('realtime', 'Ratekeeper'), ('core_config', 'set_daemon_affinity'), ('swaglog', 'cloudlog')]:
    stub(f'openpilot.common.{name}', **{attr: Mock()})
  for name, attrs in [('stereod.semantic_fusion', ['SemanticFusion']), ('stereod.yolo_3d', ['YOLO3DEstimator']),
                      ('pointcloudd.semantic_filter', ['SemanticPointFilter', 'FilterConfig']),
                      ('pointcloudd.adaptive_voxel', ['AdaptiveVoxelFilter']), ('pointcloudd.feature_extractor', ['SemanticFeatureExtractor'])]:
    stub(f'openpilot.nagaspilot.daemons.{name}', **{attr: Mock() for attr in attrs})
  stub('openpilot.system.inferenced', InferenceClient=Mock())
  stub('openpilot.system.hardware', HARDWARE=Mock())
  modules['openpilot.nagaspilot.daemons.stereod.stereo_input'] = support.inputs
  modules['openpilot.system.inferenced.opencl_reproject'] = projection
  with patch.dict(sys.modules, modules):
    return load('pointcloudd_host_test', ROOT / 'nagaspilot/daemons/pointcloudd/pointcloudd.py')


pointcloud = load_pointcloud()


class TestPointCloudGPUContract(unittest.TestCase):
  def reconstructor(self, *, use_gpu=True, cal=True):
    with patch.object(pointcloud.HARDWARE, 'load_stereo_intrinsics', return_value=support.calibration() if cal else None), \
         patch.dict(sys.modules, {'openpilot.nagaspilot.daemons.stereod.stereo_input': support.inputs}):
      return pointcloud.Reconstructor3D(Mock(), use_gpu=use_gpu)

  def test_missing_calibration_never_builds_nominal_geometry(self):
    self.assertIsNone(self.reconstructor(cal=False).Q)

  def test_gpu_failure_does_not_invoke_cpu_projection(self):
    obj = self.reconstructor()
    obj._gpu_reprojector = Mock(reproject=Mock(return_value=None))
    points, labels, stats = obj.reconstruct(np.ones((16, 32), np.float32), np.ones((16, 32), np.float32), None, [])
    self.assertIsNone(points)
    self.assertIsNone(labels)
    self.assertEqual(stats['recon_method'], 'gpu_unavailable')

  def test_gpu_disabled_produces_no_projection(self):
    obj = self.reconstructor(use_gpu=False)
    points, _, stats = obj.reconstruct(np.ones((16, 32), np.float32), np.ones((16, 32), np.float32), None, [])
    self.assertIsNone(points)
    self.assertEqual(stats['recon_method'], 'gpu_unavailable')

  def test_calibration_resolution_and_confidence_are_required(self):
    obj = self.reconstructor()
    obj._gpu_reprojector = Mock()
    for shape, confidence in (((8, 16), np.ones((8, 16), np.float32)), ((16, 32), None)):
      self.assertIsNone(obj.reconstruct(np.ones(shape, np.float32), confidence, None, [])[0])
    obj._gpu_reprojector.reproject.assert_not_called()

  def test_valid_cloud_keeps_only_confident_positive_disparity(self):
    obj = self.reconstructor()
    xyz = np.zeros((16, 32, 3), np.float32)
    xyz[:, :, 2] = 2
    obj._gpu_reprojector = Mock(reproject=Mock(return_value=xyz))
    disparity = np.ones((16, 32), np.float32)
    disparity[0, :4] = [0, -1, np.nan, np.inf]
    confidence = np.ones_like(disparity)
    confidence[1, :4] = [0, 0.2, np.nan, 2]
    points, _, stats = obj.reconstruct(disparity, confidence, None, [])
    self.assertEqual(len(points), 16*32-8)
    self.assertEqual(stats['recon_method'], 'gpu_opencl')

  def test_gpu_acquisition_retries_after_backoff(self):
    obj = pointcloud.GPUReprojector(Mock())
    obj.client.gpu.side_effect = [RuntimeError('GPU absent'), Mock()]
    clock = [10.0]
    fake = ModuleType('openpilot.system.inferenced.opencl_reproject')
    fake.OpenCLReprojector = Mock(return_value=Mock())
    with patch.dict(sys.modules, {fake.__name__: fake}), patch.object(pointcloud.time, 'monotonic', side_effect=lambda: clock[0]):
      self.assertFalse(obj.initialize())
      clock[0] = 11
      self.assertFalse(obj.initialize())
      self.assertEqual(obj.client.gpu.call_count, 1)
      clock[0] = 12
      self.assertTrue(obj.initialize())
      self.assertEqual(obj.client.gpu.call_count, 2)

  def test_projection_rejects_bad_q_before_device_allocation(self):
    obj = projection.OpenCLReprojector.__new__(projection.OpenCLReprojector)
    obj.closed, obj.gpu = False, Mock()
    with self.assertRaises(ValueError):
      obj.reproject(np.ones((4, 4)), np.zeros((4, 4)), np.ones((4, 4)))
    obj.gpu.check_allocations.assert_not_called()


class TestStereoDegradation(unittest.TestCase):
  def test_optional_stereo_failure_does_not_disable_core_adas(self):
    class Names:
      def __getattr__(self, name):
        return name
    events = {'cameraMalfunction': {}}
    implementation = load('eop_events_stereo_test', ROOT / 'nagaspilot/manager/eop_events.py')
    implementation.register_eop_events(events, EventName=Names(), ET=Names(), Alert=Mock(),
                                       AlertStatus=Names(), AlertSize=Names(), Priority=Names(), VisualAlert=Names(), AudibleAlert=Names(),
                                       ImmediateDisableAlert=Mock(), NoEntryAlert=Mock(), NormalPermanentAlert=Mock(),
                                       EngagementAlert=Mock(), soft_disable_alert=Mock())
    self.assertEqual(set(events['stereoFault']), {'PERMANENT'})
    self.assertIn('IMMEDIATE_DISABLE', events['inferenceFault'])
    self.assertIn('NO_ENTRY', events['inferenceFault'])

  def test_stereo_source_expires_and_rejects_fault_or_duplicate(self):
    health = load('stereo_fusion_health_test', ROOT / 'nagaspilot/daemons/gridd/source_health.py')
    self.assertTrue(health.fresh_stereo(100, 110, 99, source_valid=True, fault=False))
    for timestamp, now, previous, valid, fault in [(100, 110, 100, True, False), (100, 99, 0, True, False),
                                                 (100, 200_000_101, 0, True, False), (100, 110, 0, False, False),
                                                 (100, 110, 0, True, True)]:
      self.assertFalse(health.fresh_stereo(timestamp, now, previous, source_valid=valid, fault=fault))

  def test_source_flags_only_describe_real_contributors(self):
    health = load('grid_sources_health_test', ROOT / 'nagaspilot/daemons/gridd/source_health.py')
    self.assertEqual(health.grid_source_flags(False, []), 0)
    self.assertEqual(health.grid_source_flags(True, []), 1)
    self.assertEqual(health.grid_source_flags(False, [{'source': 'drive_vision', 'trackId': 4}]), 16 | 8)
    self.assertEqual(health.grid_source_flags(False, [{'source': 'side_left'}, {'source': 'side_right'}]), 2 | 4)

  def test_dropout_ages_occupied_and_free_evidence_toward_unknown(self):
    hardware = ModuleType('openpilot.system.hardware')
    hardware.HARDWARE = Mock(get_max_reliable_depth_m=Mock(return_value=80))
    cameras = ModuleType('openpilot.nagaspilot.daemons.gridd.camera_geometry')
    cameras.CameraArrayGeometry = object
    with patch.dict(sys.modules, {hardware.__name__: hardware, cameras.__name__: cameras}):
      bev = load('lazy_bev_dropout_test', ROOT / 'nagaspilot/daemons/gridd/lazy_bev.py')
    obj = bev.LazyBEV()
    obj.log_odds[0, 0], obj.log_odds[0, 1] = 4, -4
    obj._last_frame_time = 1
    with patch.object(bev.time, 'monotonic', return_value=3):
      obj.update_from_points(None)
    self.assertGreater(obj.log_odds[0, 0], 0)
    self.assertLess(obj.log_odds[0, 0], 4)
    self.assertLess(obj.log_odds[0, 1], 0)
    self.assertGreater(obj.log_odds[0, 1], -4)


if __name__ == '__main__':
  unittest.main()
