"""Host contract/recovery tests; no camera, cereal extension or GPU required.

Run this file directly with unittest. EOP_GPU_TESTS=1 additionally requires and
executes a real OpenCL GPU; requested device tests fail if GPU setup fails.
"""
import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
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


inputs = load('stereo_input_host_test', ROOT / 'nagaspilot/daemons/stereod/stereo_input.py')
sgm = load('sgm_host_test', ROOT / 'nagaspilot/daemons/stereod/sgm.py')
backend = load('opencl_stereo_host_test', ROOT / 'system/inferenced/opencl_stereo.py')


def daemon_module():
  replacements = {}
  def stub(name, **attrs):
    module = ModuleType(name)
    module.__dict__.update(attrs)
    replacements[name] = module
    return module
  cereal = stub('cereal')
  cereal.messaging = stub('cereal.messaging')
  stub('msgq')
  stub('msgq.visionipc', VisionIpcClient=Mock())
  for name, attr in [('params', 'Params'), ('realtime', 'Ratekeeper'), ('core_config', 'set_daemon_affinity'),
                     ('perf_monitor', 'PerformanceMonitor'), ('swaglog', 'cloudlog')]:
    stub(f'openpilot.common.{name}', **{attr: Mock()})
  stub('openpilot.system.hardware.camera_geometry', CameraGeometry=Mock())
  stub('openpilot.system.hardware.registry', PlatformRegistry=Mock())
  stub('openpilot.system.inferenced', InferenceClient=Mock())
  replacements['openpilot.nagaspilot.daemons.stereod.stereo_input'] = inputs
  replacements['openpilot.nagaspilot.daemons.stereod.sgm'] = sgm
  with patch.dict(sys.modules, replacements):
    return load('stereod_host_test', ROOT / 'nagaspilot/daemons/stereod/stereod.py')


daemon = daemon_module()


def calibration(w=32, h=16):
  y, x = np.indices((h, w), dtype=np.float32)
  q = np.array([[1, 0, 0, -w/2], [0, 1, 0, -h/2], [0, 0, 0, 100], [0, 0, 10, 0]], dtype=float)
  return SimpleNamespace(left_map1=x, left_map2=y, right_map1=x.copy(), right_map2=y.copy(), Q=q, T=np.array([-0.1, 0, 0]))


class TestStereoInputs(unittest.TestCase):
  def test_padded_luma(self):
    data = np.array([[1, 2, 3, 99, 99], [4, 5, 6, 99, 99]], dtype=np.uint8).tobytes()
    np.testing.assert_array_equal(inputs.luma_plane(data, 3, 2, 5), [[1, 2, 3], [4, 5, 6]])
    for args in ((data, 6, 2, 5), (data[:5], 3, 2, 5)):
      with self.assertRaises(ValueError):
        inputs.luma_plane(*args)

  def test_capture_time_is_oldest_capture(self):
    self.assertEqual(inputs.capture_timestamp(100_000_000, 103_000_000, 110_000_000), 100_000_000)

  def test_bad_capture_pairs(self):
    for args in ((0, 1, 2), (100, 100, 99), (1, 6_000_002, 7_000_000),
                 (1, 1, 150_000_002), (100, 101, 105, 100)):
      with self.subTest(args=args), self.assertRaises(ValueError):
        inputs.capture_timestamp(*args)

  def test_exact_maps_and_census_footprint(self):
    cal = calibration()
    maps = inputs.calibration_maps(cal, 32, 16)
    self.assertTrue(maps[-2][4, 8])
    self.assertFalse(maps[-2][0, 0])
    cal.left_map1[4, 8] = -1
    support = inputs.calibration_maps(cal, 32, 16)[-2]
    self.assertFalse(support[4, 9])  # Bad neighbor invalidates its census window.
    with self.assertRaises(ValueError):
      inputs.calibration_maps(cal, 16, 8)

  def test_calibration_rejects_missing_nonfinite_and_wrong_metric_scale(self):
    for field, value in (('Q', np.zeros((4, 4))), ('Q', np.full((4, 4), np.nan)),
                         ('T', np.array([-100., 0, 0])), ('left_map1', None)):
      cal = calibration()
      setattr(cal, field, value)
      with self.subTest(field=field), self.assertRaises(ValueError):
        inputs.calibration_maps(cal, 32, 16)
    cal = calibration()
    cal.Q[3, 2] = 0.01  # Millimeter Q with meter T.
    with self.assertRaisesRegex(ValueError, 'scale'):
      inputs.calibration_maps(cal, 32, 16)


class TestStereoRecovery(unittest.TestCase):
  def make_daemon(self):
    obj = daemon.StereoD.__new__(daemon.StereoD)
    obj.width, obj.height = 32, 16
    obj.gpu, obj._sgm = None, None
    obj._next_init_attempt = 0
    obj._gpu_sgm_available = False
    obj._fault, obj._fault_reason, obj._consecutive_failures = False, '', 0
    obj._FAULT_THRESHOLD = 3
    obj.client = Mock()
    obj._hardware = Mock(load_stereo_intrinsics=Mock(return_value=calibration()))
    obj._metrics = Mock()
    obj.vipc_left = obj.vipc_right = SimpleNamespace(stride=32)
    return obj

  def ready(self):
    obj = self.make_daemon()
    with patch.object(daemon, 'SGM', return_value=Mock(is_available=Mock(return_value=True), get_device_info=Mock(return_value={}))), \
         patch.object(daemon.time, 'monotonic', return_value=10):
      self.assertTrue(obj._init_sgm(32, 16))
    obj._sgm_config = SimpleNamespace(max_disparity=64)
    return obj

  def test_failed_gpu_can_be_acquired_on_later_attempt(self):
    obj = self.make_daemon()
    obj.client.gpu.side_effect = [RuntimeError('GPU unplugged'), Mock()]
    self.assertFalse(obj._init_sgm(32, 16))
    self.assertEqual(obj._fault_reason, 'gpu_unavailable')
    with patch.object(daemon, 'SGM', return_value=Mock(is_available=Mock(return_value=True), get_device_info=Mock(return_value={}))):
      self.assertTrue(obj._init_sgm(32, 16))
    self.assertEqual(obj.client.gpu.call_count, 2)
    self.assertTrue(obj._fault)  # Initialization alone cannot clear a data fault.

  def test_invalid_calibration_clears_previous_q_and_can_retry(self):
    obj = self.ready()
    obj._hardware.load_stereo_intrinsics.return_value = None
    self.assertFalse(obj._init_sgm(32, 16))
    self.assertIsNone(obj.Q)
    self.assertFalse(obj._gpu_sgm_available)
    obj._hardware.load_stereo_intrinsics.return_value = calibration()
    with patch.object(daemon, 'SGM', return_value=Mock(is_available=Mock(return_value=True), get_device_info=Mock(return_value={}))):
      self.assertTrue(obj._init_sgm(32, 16))

  def test_bad_luma_is_handled_without_crashing(self):
    obj = self.ready()
    self.assertEqual(obj._compute_disparity(b'bad', b'bad'), (None, None))
    self.assertEqual(obj._consecutive_failures, 1)
    obj._sgm.compute.assert_not_called()

  def test_device_failure_drops_backend_without_cpu_matching(self):
    obj = self.ready()
    obj._sgm.compute.side_effect = sgm.SGMDeviceError('lost GPU')
    self.assertEqual(obj._compute_disparity(bytes(512), bytes(512)), (None, None))
    self.assertIsNone(obj._sgm)
    self.assertIsNone(obj.gpu)
    self.assertFalse(obj._gpu_sgm_available)
    obj.client.release.assert_called_once()

  def test_repeated_timeouts_release_backend(self):
    obj = self.ready()
    obj._sgm.compute.side_effect = sgm.SGMTimeoutError('late frame')
    for _ in range(3):
      self.assertEqual(obj._compute_disparity(bytes(512), bytes(512)), (None, None))
    self.assertEqual(obj._fault_reason, 'sgm_consecutive_failures')
    self.assertIsNone(obj._sgm)

  def test_success_clears_fault_only_with_valid_normalized_output(self):
    obj = self.ready()
    obj._fault, obj._fault_reason = True, 'stereo_input_invalid'
    obj._sgm.compute.return_value = SimpleNamespace(success=True, inference_time_ms=1,
                                                   disparity=np.full((16, 32), 4, np.float32),
                                                   confidence=np.full((16, 32), 0.8, np.float32))
    disp, confidence = obj._compute_disparity(bytes(512), bytes(512))
    self.assertFalse(obj._fault)
    self.assertEqual(disp[8, 16], 4)
    self.assertEqual(confidence[0, 0], 0)

  def test_nonfinite_and_late_outputs_are_not_returned(self):
    for invalid in ('nan', 'late', 'shape', 'confidence'):
      obj = self.ready()
      result = SimpleNamespace(success=True, inference_time_ms=1,
                               disparity=np.ones((16, 32), np.float32), confidence=np.ones((16, 32), np.float32))
      if invalid == 'nan':
        result.disparity[0, 0] = np.nan
      elif invalid == 'late':
        result.inference_time_ms = 51
      elif invalid == 'shape':
        result.disparity = np.ones((8, 16), np.float32)
      else:
        result.confidence[0, 0] = 1.1
      obj._sgm.compute.return_value = result
      with self.subTest(invalid=invalid):
        self.assertEqual(obj._compute_disparity(bytes(512), bytes(512)), (None, None))

  def loop_ready(self):
    obj = self.ready()
    obj.enabled, obj.frame_id, obj._last_capture = True, 42, 0
    image = SimpleNamespace(width=32, height=16, data=bytes(512))
    obj.vipc_left = Mock(stride=32, frame_id=42, timestamp_sof=1_000_000_000,
                         recv=Mock(return_value=image), connect=Mock(return_value=True))
    obj.vipc_right = Mock(stride=32, frame_id=42, timestamp_sof=1_001_000_000,
                          recv=Mock(return_value=image), connect=Mock(return_value=True))
    obj.rk = Mock(keep_time=Mock(side_effect=StopIteration))
    obj._publish = Mock()
    obj._sgm.compute.return_value = SimpleNamespace(success=True, inference_time_ms=1,
                                                   disparity=np.full((16, 32), 4, np.float32),
                                                   confidence=np.full((16, 32), 0.8, np.float32))
    return obj

  def test_capture_age_is_checked_after_compute(self):
    obj = self.loop_ready()
    with patch.object(daemon.time, 'monotonic_ns', side_effect=[1_010_000_000, 1_210_000_000]), \
         self.assertRaises(StopIteration):
      obj.run()
    self.assertEqual(obj._fault_reason, 'stereo_output_stale')
    obj._publish.assert_called_once_with(None, None, 1_000_000_000)

  def test_missing_camera_still_publishes_fault_status(self):
    obj = self.loop_ready()
    obj.vipc_left.recv.return_value = None
    with self.assertRaises(StopIteration):
      obj.run()
    self.assertEqual(obj._fault_reason, 'stereo_input_missing')
    self.assertIsNone(obj._publish.call_args.args[0])

  def test_retry_backoff_keeps_publishing_without_acquiring_gpu(self):
    obj = self.loop_ready()
    obj._gpu_sgm_available = False
    obj._next_init_attempt = 12.0
    obj._init_sgm = Mock()
    with patch.object(daemon.time, 'monotonic', return_value=11.0), \
         patch.object(daemon.time, 'monotonic_ns', return_value=1_010_000_000), self.assertRaises(StopIteration):
      obj.run()
    obj._init_sgm.assert_not_called()
    obj._publish.assert_called_once_with(None, None, 1_000_000_000)

  def test_publish_preserves_capture_metadata_and_schema_fields(self):
    obj = self.ready()
    obj.enabled, obj.frame_id, obj.pm = True, 42, Mock()
    obj._metrics.get_latency_stats.return_value = {'sgm': {'avg_ms': 12.5}}
    status_type = type('Status', (), {'__slots__': ('timestamp', 'enabled', 'fault', 'faultReason', 'consecutiveFailures',
                                                  'processingTimeMs', 'frameId', 'hasCalibration')})
    depth_type = type('Depth', (), {'__slots__': ('timestamp', 'frameId', 'width', 'height', 'disparityMap', 'confidenceMap',
                                                'meanDisparity', 'validPixels')})
    def message(name):
      return SimpleNamespace(**{name: status_type() if name == 'stereoStatus' else depth_type()})
    with patch.object(daemon.messaging, 'new_message', side_effect=message, create=True):
      obj._publish(np.ones((16, 32), np.float32), np.ones((16, 32), np.float32), 1_000_000_000)
    calls = obj.pm.send.call_args_list
    self.assertEqual(calls[0].args[1].stereoDepth.timestamp, 1_000_000_000)
    self.assertEqual(calls[0].args[1].stereoDepth.frameId, 42)
    self.assertEqual(calls[1].args[1].stereoStatus.processingTimeMs, 12.5)


class TestStereoConstructor(unittest.TestCase):
  def test_uses_the_actual_factory_calibration_interface(self):
    # HardwareBase has load_stereo_intrinsics; no get_camera_geometry method.
    hardware = SimpleNamespace(load_stereo_intrinsics=lambda: None)
    with patch.object(daemon.PlatformRegistry, 'create', return_value=hardware), \
         patch.object(daemon.messaging, 'PubMaster', return_value=Mock(), create=True):
      obj = daemon.StereoD()
    self.assertIs(obj._hardware, hardware)
    self.assertIsNone(obj.stereo_baseline_m)


class TestGPUContract(unittest.TestCase):
  def test_production_target_never_allocates_cpu_matcher(self):
    for target in ('auto', 'gpu'):
      with patch.object(sgm.SGM, '_allocate_buffers') as allocate, self.assertRaises(sgm.SGMDeviceError):
        sgm.SGM(target=target)
      allocate.assert_not_called()

  def test_default_matcher_and_convenience_function_require_gpu(self):
    with patch.object(sgm.SGM, '_allocate_buffers') as allocate:
      with self.assertRaises(sgm.SGMDeviceError):
        sgm.SGM()
      with self.assertRaises(sgm.SGMDeviceError):
        sgm.compute_disparity(np.zeros((16, 32), np.uint8), np.zeros((16, 32), np.uint8))
    allocate.assert_not_called()

  def test_working_set_rejection_precedes_compilation_and_allocation(self):
    gpu = Mock(device=SimpleNamespace(max_work_group_size=256, max_work_item_sizes=(256,)))
    gpu.check_allocations.side_effect = RuntimeError('memory budget')
    config = sgm.SGMConfig(target_width=1920, target_height=1080)
    with self.assertRaisesRegex(RuntimeError, 'budget'):
      backend.OpenCLStereo(gpu, config)
    gpu.build.assert_not_called()


class TestDepthHelper(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    log = ModuleType('openpilot.common.swaglog')
    log.cloudlog = Mock()
    with patch.dict(sys.modules, {'openpilot.nagaspilot.daemons.stereod.sgm': sgm, 'openpilot.common.swaglog': log}):
      cls.pipeline = load('depth_pipeline_host_test', ROOT / 'nagaspilot/daemons/stereod/depth_pipeline.py')

  def make_pipeline(self):
    matcher = Mock()
    matcher.compute.return_value = SimpleNamespace(success=True, disparity=np.full((16, 32), 2, np.float32),
                                                   confidence=np.ones((16, 32), np.float32), backend_used='opencl')
    with patch.object(self.pipeline, 'SGM', return_value=matcher) as factory:
      obj = self.pipeline.DepthPipeline(width=32, height=16, max_disparity=8, gpu=object())
    self.assertEqual(factory.call_args.kwargs['target'], 'gpu')
    return obj

  def test_q_offsets_are_used_and_backend_result_is_truthful(self):
    obj = self.make_pipeline()
    q = calibration().Q
    q[3, 3] = 20  # Complete Q gives 100/(10*2+20) = 2.5 m.
    result = obj.process(np.zeros((16, 32), np.uint8), np.zeros((16, 32), np.uint8), q)
    self.assertTrue(result.success)
    np.testing.assert_allclose(result.depth, 2.5)
    self.assertTrue(result.using_gpu)
    obj.sgm.compute.return_value.backend_used = 'numpy'
    self.assertFalse(obj.process(np.zeros((16, 32), np.uint8), np.zeros((16, 32), np.uint8)).using_gpu)

  def test_bad_q_returns_failure(self):
    obj = self.make_pipeline()
    result = obj.process(np.zeros((16, 32), np.uint8), np.zeros((16, 32), np.uint8), np.zeros((4, 4)))
    self.assertFalse(result.success)


@unittest.skipUnless(os.environ.get('EOP_GPU_TESTS') == '1', 'Set EOP_GPU_TESTS=1 on a real GPU to require numerical checks')
class TestRealGPUStereo(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    shared = load('gpu_compute_real_test', ROOT / 'common/gpu_compute.py')
    cls.gpu = shared.GPUCompute()
    cls.addClassCleanup(cls.gpu.close)
    cls.gpu.smoke_test()
    cls.matcher = backend.OpenCLStereo(cls.gpu, sgm.SGMConfig(target_width=96, target_height=32,
                                                            max_disparity=16, max_runtime_ms=10_000))
    cls.addClassCleanup(cls.matcher.close)

  def test_shifted_texture_and_reused_buffers(self):
    rng = np.random.default_rng(17)
    for shift in (4, 7, 3):
      left = rng.integers(0, 256, (32, 96), dtype=np.uint8)
      right = rng.integers(0, 256, left.shape, dtype=np.uint8)
      right[:, :-shift] = left[:, shift:]
      disparity, confidence, _ = self.matcher.compute(left, right)
      roi = disparity[5:-5, 22:-8]
      valid = roi > 0
      self.assertGreater(valid.mean(), 0.8)
      self.assertLess(np.median(np.abs(roi[valid] - shift)), 0.25)
      self.assertTrue(((confidence >= 0) & (confidence <= 1)).all())
      self.assertTrue((disparity[:2] == 0).all())

  def test_full_q_projection_matches_ros_opencv_oracle(self):
    import cv2
    projection = load('opencl_projection_real_test', ROOT / 'system/inferenced/opencl_reproject.py')
    projector = projection.OpenCLReprojector(self.gpu)
    self.addCleanup(projector.close)
    disparity = np.full((32, 96), 4, np.float32)
    confidence = np.ones_like(disparity)
    # ROS image_geometry full Q: fx!=fy, unequal cx and a metric baseline.
    fx, fy, cx, cy, cx_right, tx = 100., 120., 48., 16., 49., -0.1
    q = np.array([[fy*tx, 0, 0, -fy*cx*tx], [0, fx*tx, 0, -fx*cy*tx],
                  [0, 0, 0, fx*fy*tx], [0, 0, -fy, fy*(cx-cx_right)]], np.float32)
    expected = cv2.reprojectImageTo3D(disparity, q)
    result = projector.reproject(disparity, q, confidence, max_runtime_ms=10_000)
    np.testing.assert_allclose(result, expected, rtol=1e-5, atol=1e-5)
    disparity[0, :4] = [0, -1, np.nan, np.inf]
    confidence[1, :3] = [0, np.nan, 1.1]
    result = projector.reproject(disparity, q, confidence, max_runtime_ms=10_000)
    self.assertTrue(np.isnan(result[0, :4]).all())
    self.assertTrue(np.isnan(result[1, :3]).all())

  def test_flat_images_do_not_invent_depth(self):
    blank = np.zeros((32, 96), np.uint8)
    disparity, confidence, _ = self.matcher.compute(blank, blank)
    self.assertFalse(np.any(disparity))
    self.assertFalse(np.any(confidence))


if __name__ == '__main__':
  unittest.main()
