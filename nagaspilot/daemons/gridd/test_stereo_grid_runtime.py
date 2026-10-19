"""Host calibration/fusion contracts, plus opt-in mandatory real GPU execution.

Run directly. EOP_GPU_TESTS=1 requires a GPU; absent OpenCL is then a failure.
"""
import importlib.util
import ast
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest  # noqa: TID251
from unittest.mock import Mock, patch  # noqa: TID251

import numpy as np

ROOT = Path(__file__).resolve().parents[3]


def load(name, path):
  spec = importlib.util.spec_from_file_location(name, ROOT / path)
  module = importlib.util.module_from_spec(spec)
  sys.modules[name] = module
  spec.loader.exec_module(module)
  return module


inputs = load('grid_input_test', 'nagaspilot/daemons/stereod/stereo_input.py')
projection = load('grid_projection_test', 'system/inferenced/opencl_reproject.py')
with patch.dict(sys.modules, {'openpilot.nagaspilot.daemons.stereod.stereo_input': inputs,
                             'openpilot.system.inferenced.opencl_reproject': projection}):
  mapping = load('grid_mapping_test', 'nagaspilot/daemons/gridd/stereo_grid.py')
hardware = ModuleType('openpilot.system.hardware')
hardware.HARDWARE = Mock(get_max_reliable_depth_m=Mock(return_value=80))
camera = ModuleType('openpilot.nagaspilot.daemons.gridd.camera_geometry')
camera.CameraArrayGeometry = object
with patch.dict(sys.modules, {hardware.__name__: hardware, camera.__name__: camera}):
  bev = load('grid_evidence_test', 'nagaspilot/daemons/gridd/lazy_bev.py')


def calibration():
  y, x = np.indices((32, 32), dtype=np.float32)
  return SimpleNamespace(Q=np.array([[1, 0, 0, -16], [0, 1, 0, -16], [0, 0, 0, 100], [0, 0, 10, 0]], float),
                         T=np.array([-0.1, 0, 0]), R1=np.eye(3),
                         left_map1=x, right_map1=x.copy(), left_map2=y, right_map2=y.copy())


def mounting():
  return np.array([[0, 0, 1, 0], [-1, 0, 0, 0], [0, -1, 0, 1.2], [0, 0, 0, 1]], np.float32)


class TestStereoGridContracts(unittest.TestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.addCleanup(self.directory.cleanup)
    self.path = Path(self.directory.name) / 'stereo_extrinsics.npz'
    self.cal = calibration()
    self.identity = inputs.calibration_id(self.cal)
    self.save()

  def save(self, **changes):
    fields = dict(calibration_id=self.identity, rectified_optical_to_footprint=mounting(),
                  ground_plane=np.array([0, 0, 1, 0], np.float32))
    fields.update(changes)
    np.savez(self.path, **fields)

  def test_mount_preserves_measured_height_and_axis_convention(self):
    transform, plane = mapping.measured_mount(self.path, self.identity)
    np.testing.assert_allclose(transform @ [2, 0.2, 10, 1], [10, -2, 1, 1], atol=1e-6)
    np.testing.assert_array_equal(plane, [0, 0, 1, 0])

  def test_wrong_factory_revision_is_rejected(self):
    with self.assertRaises(ValueError):
      mapping.measured_mount(self.path, 'different-calibration')

  def test_reflection_scale_nonrigid_and_nominal_missing_mount_are_rejected(self):
    for transform in (np.eye(4), mounting()*2, np.diag([-1, 1, 1, 1]), np.full((4, 4), np.nan)):
      self.save(rectified_optical_to_footprint=transform)
      with self.subTest(transform=transform), self.assertRaises(ValueError):
        mapping.measured_mount(self.path, self.identity)
    self.path.unlink()
    with self.assertRaises(FileNotFoundError):
      mapping.measured_mount(self.path, self.identity)

  def test_nonunit_downward_or_invalid_ground_is_rejected(self):
    for plane in ([0, 0, -1, 0], [0, 0, 2, 0], [0, 1, 0, 0], [0, 0, 1, 10], [0, 0, np.nan, 0]):
      self.save(ground_plane=plane)
      with self.subTest(plane=plane), self.assertRaises(ValueError):
        mapping.measured_mount(self.path, self.identity)

  def test_identity_is_stable_but_changes_with_maps_q_and_rectified_frame(self):
    self.assertEqual(self.identity, inputs.calibration_id(calibration()))
    for field in ('Q', 'T', 'left_map1', 'right_map2'):
      other = calibration()
      getattr(other, field).flat[0] += 0.01
      self.assertNotEqual(self.identity, inputs.calibration_id(other))
    other = calibration()
    other.R1 = np.diag([-1., -1., 1.])
    self.assertNotEqual(self.identity, inputs.calibration_id(other))

  def test_invalid_rectified_frame_cannot_be_fingerprinted(self):
    for rotation in (np.eye(2), np.diag([-1, 1, 1]), np.full((3, 3), np.nan)):
      self.cal.R1 = rotation
      with self.assertRaises(ValueError):
        inputs.calibration_id(self.cal)

  def source(self):
    return mapping.StereoGridSource(lambda: self.cal, Mock(), bev.BEV_GRID, self.path)

  def message(self, **fields):
    values = dict(timestamp=1_000_000_000, height=32, width=32, calibrationId=self.identity,
                  disparityMap=np.ones((32, 32), np.float32).tobytes(),
                  confidenceMap=np.ones((32, 32), np.float32).tobytes())
    values.update(fields)
    return SimpleNamespace(**values)

  def test_bad_identity_shape_and_missing_confidence_produce_no_evidence(self):
    for fields in ({'calibrationId': ''}, {'calibrationId': 'old'}, {'width': 16}, {'confidenceMap': b''}):
      source = self.source()
      with patch.object(mapping, 'GPUGridMapper') as mapper, patch.object(mapping.time, 'monotonic_ns', return_value=1_100_000_000):
        self.assertIsNone(source.observe(self.message(**fields)))
        mapper.return_value.observe.assert_not_called()
        self.assertTrue(source.last_error)

  def test_gpu_failure_drops_frame_and_retries_only_after_backoff(self):
    source = self.source()
    with patch.object(mapping, 'GPUGridMapper', side_effect=RuntimeError('GPU absent')) as mapper, \
         patch.object(mapping.time, 'monotonic_ns', return_value=1_100_000_000), \
         patch.object(mapping.time, 'monotonic', return_value=10):
      self.assertIsNone(source.observe(self.message()))
      self.assertIsNone(source.observe(self.message()))
      self.assertEqual(mapper.call_count, 1)
      self.assertEqual(source.last_error, 'GPU absent')

  def test_missing_future_stale_and_expired_captures_produce_no_grid(self):
    for stamp in (0, 1_100_000_001, 800_000_000):
      source = self.source()
      with patch.object(mapping, 'GPUGridMapper') as mapper, patch.object(mapping.time, 'monotonic_ns', return_value=1_100_000_000):
        self.assertIsNone(source.observe(self.message(timestamp=stamp)))
        mapper.assert_not_called()
    source = self.source()
    with patch.object(mapping, 'GPUGridMapper'), patch.object(mapping.time, 'monotonic_ns', side_effect=[1_100_000_000, 1_200_000_001]):
      self.assertIsNone(source.observe(self.message()))
      self.assertIn('expired', source.last_error)

  def test_accepted_calibration_and_capture_are_forwarded_without_cpu_reprojection(self):
    source = self.source()
    expected = mapping.StereoObservation(np.empty((0, 3)), np.ones((1, 1), bool), np.zeros((1, 1), bool))
    with patch.object(mapping, 'GPUGridMapper') as mapper, patch.object(mapping.time, 'monotonic_ns', return_value=1_100_000_000):
      mapper.return_value.observe.return_value = expected
      self.assertIs(source.observe(self.message()), expected)
      args = mapper.return_value.observe.call_args.args
      np.testing.assert_array_equal(args[2], self.cal.Q)
      self.assertIsNone(source.observe(self.message()))  # Same capture is not fused twice.

  def test_endpoint_fusion_keeps_unknown_and_obstacle_wins_over_road(self):
    grid = bev.LazyBEV()
    occupied = np.zeros_like(grid.log_odds, bool)
    ground = np.zeros_like(occupied)
    occupied[60, 25] = True
    ground[60, 25:27] = True
    grid.log_odds[60, 25] = -10  # Previously clear cell is now a parked car.
    grid.update_from_evidence(occupied, ground, retain_history=True)
    p = grid.get_grid()
    self.assertGreater(p[60, 25], 0.7)
    self.assertLess(p[60, 26], 0.5)
    self.assertEqual(p[60, 27], 0.5)

  def test_motion_or_missing_pose_clears_misregistered_history(self):
    grid = bev.LazyBEV()
    grid.log_odds[50, 20] = 5
    grid.update_from_evidence(None, None, retain_history=False)
    self.assertTrue((grid.get_grid() == 0.5).all())

  def test_stationary_dropout_ages_toward_unknown_not_free(self):
    grid = bev.LazyBEV()
    grid.log_odds[50, 20:22] = [5, -5]
    grid._last_frame_time = 1
    with patch.object(bev.time, 'monotonic', return_value=3):
      grid.update_from_evidence(None, None, retain_history=True)
    self.assertTrue(0 < grid.log_odds[50, 20] < 5)
    self.assertTrue(-5 < grid.log_odds[50, 21] < 0)

  def test_unknown_grid_is_not_an_obstacle_hazard(self):
    self.assertFalse(bev.LazyBEV()._metrics()['occupancy_detected'])

  def test_actual_history_guard_rejects_motion_invalid_axes_and_stale_pose(self):
    tree = ast.parse((ROOT / 'nagaspilot/daemons/gridd/gridd.py').read_text())
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'GridD')
    node = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == '_stereo_history_safe')
    clock = SimpleNamespace(monotonic=lambda: 10.)
    namespace = dict(np=np, time=clock)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'grid_pose_guard', 'exec'), namespace)
    velocity = SimpleNamespace(x=0., y=0., z=0., valid=True)
    angular = SimpleNamespace(x=0., y=0., z=0., valid=True)
    class SM(dict):
      valid = {'livePose': True}
      recv_time = {'livePose': 9.9}
    host = SimpleNamespace(sm=SM(livePose=SimpleNamespace(velocityDevice=velocity, angularVelocityDevice=angular)))
    def guard():
      return namespace['_stereo_history_safe'](host)
    self.assertTrue(guard())
    for axis in (velocity, angular):
      for value in (1., np.nan, np.inf):
        axis.y = value
        self.assertFalse(guard())
      axis.y = 0.
    for stamp in (9., 11.):
      host.sm.recv_time['livePose'] = stamp
      self.assertFalse(guard())

  def test_actual_pathd_stereo_inputs_expire_without_retaining_nudge_hazards(self):
    tree = ast.parse((ROOT / 'nagaspilot/daemons/pathd/pathd.py').read_text())
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'PathD')
    update = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == 'update')
    incoming = next(node for node in update.body if isinstance(node, ast.If) and
                    "updated.get('stereoGround'" in ast.unparse(node.test))
    validity = next(node for node in update.body if isinstance(node, ast.Assign) and
                    any(isinstance(target, ast.Name) and target.id == 'stereo_ground_valid' for target in node.targets))
    expiry = next(node for node in update.body if isinstance(node, ast.If) and ast.unparse(node.test) == 'not stereo_ground_valid')
    code = compile(ast.Module(body=[incoming, validity, expiry], type_ignores=[]), 'pathd_stereo_freshness', 'exec')
    now = [10.]
    clock = SimpleNamespace(monotonic=lambda: now[0], monotonic_ns=lambda: int(now[0]*1e9))
    message = SimpleNamespace(timestamp=10_000_000_000, hasStereoDepth=True,
                              leftBoundary=[-2.]*7, rightBoundary=[2.]*7,
                              bikeHazard=True, occupancyDetected=True, drivableLimitM=5.)
    class SM(dict):
      valid = {'stereoGround': True}
      updated = {'stereoGround': True}
    host = SimpleNamespace(sm=SM(stereoGround=message), log_counter=1, _stereo_ground_last_time=0.)
    namespace = dict(self=host, time=clock, GRID_STALE_TIMEOUT=0.5, cloudlog=Mock())
    exec(code, namespace)
    self.assertTrue(namespace['stereo_ground_valid'])
    self.assertEqual(host._stereo_left_boundary, [-2.]*7)
    now[0], host.sm.updated['stereoGround'] = 10.6, False
    exec(code, namespace)
    self.assertFalse(namespace['stereo_ground_valid'])
    self.assertEqual(host._stereo_left_boundary, [])
    self.assertFalse(host._occupancy_detected or host._bike_hazard)
    self.assertTrue(np.isinf(host._drivable_limit_m))
    for valid, has_depth in ((True, False), (False, True)):
      host.sm.updated['stereoGround'], host.sm.valid['stereoGround'] = True, valid
      message.timestamp, message.hasStereoDepth = int(now[0]*1e9), has_depth
      exec(code, namespace)
      self.assertFalse(namespace['stereo_ground_valid'])
      self.assertEqual(host._stereo_left_boundary, [])

  def test_real_wire_keeps_parked_car_above_road_cost_and_drops_stereo_on_loss(self):
    import capnp
    log = capnp.load(str(ROOT / 'cereal/log.capnp'), imports=[str(ROOT / 'cereal')])
    health = load('grid_wire_source_flags', 'nagaspilot/daemons/gridd/source_health.py')
    with patch.dict(sys.modules, {'openpilot.nagaspilot.daemons.gridd.lazy_bev': bev}):
      costs = load('grid_wire_costs', 'nagaspilot/daemons/gridd/fusion_costmap.py')
    def message(name):
      event = log.Event.new_message(valid=False)  # cereal.messaging explicitly overrides the schema default.
      event.init(name)
      return event
    # Execute the actual serialization methods without compiled IPC/device imports.
    tree = ast.parse((ROOT / 'nagaspilot/daemons/gridd/gridd.py').read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'fill_grid_objects']
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'GridD')
    nodes += [node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name in ('_publish', '_extract_road_boundaries')]
    namespace = dict(np=np, BEV_GRID=bev.BEV_GRID, grid_source_flags=health.grid_source_flags,
                     messaging=SimpleNamespace(new_message=message))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'gridd_wire_methods', 'exec'), namespace)
    messages = {}
    grid = bev.LazyBEV()
    occupied = np.zeros_like(grid.log_odds, bool)
    ground = np.zeros_like(occupied)
    r, c, _ = bev.BEV_GRID.cells(10, 1)  # Stationary parked car, one metre left.
    occupied[r, c] = ground[r, c] = True
    grid.update_from_evidence(occupied, ground, retain_history=False)
    costmap = costs.FusionCostmap()
    road_cost = np.full_like(costmap.costmap, costs.COST_ROAD)
    costmap.apply_drivable(road_cost)
    costmap.mark_occupied(grid.get_grid() >= 0.7)
    host = SimpleNamespace(bev=grid, frame_id=42,
                           pm=SimpleNamespace(send=lambda name, msg: messages.update({name: msg})),
                           _extract_road_boundaries=lambda _: ([], []),
                           drivable=SimpleNamespace(probability=lambda: np.ones_like(grid.log_odds)),
                           _stereo_observation=mapping.StereoObservation(np.array([[-1, -1, 10]]), occupied, ground))
    namespace['_publish'](host, 1_000_000_000, np.array([[-1, -1, 10]]), True, costmap.costmap, [])
    with log.Event.from_bytes(messages['gridObjects'].to_bytes()) as event:
      self.assertTrue(event.valid)
      wire = event.gridObjects
      self.assertEqual(wire.sourceFlags, 1)
      layer = next(layer for layer in wire.layers if layer.name == 'cost')
      decoded = np.frombuffer(layer.data, '<u2').reshape(bev.BEV_GRID.rows, bev.BEV_GRID.cols)
      self.assertEqual(decoded[r, c], costs.COST_OBSTACLE)
      _, mirrored, _ = bev.BEV_GRID.cells(10, -1)
      self.assertEqual(decoded[r, mirrored], costs.COST_ROAD)
    with log.Event.from_bytes(messages['stereoGround'].to_bytes()) as event:
      self.assertTrue(event.valid)
      self.assertTrue(event.stereoGround.hasStereoDepth)
      self.assertEqual(list(event.stereoGround.leftBoundary), [])
      self.assertTrue(np.isinf(event.stereoGround.drivableLimitM))
    host._stereo_observation = None
    namespace['_publish'](host, 1_100_000_000, None, False, None, [])
    with log.Event.from_bytes(messages['gridObjects'].to_bytes()) as event:
      self.assertEqual(event.gridObjects.sourceFlags, 0)
    with log.Event.from_bytes(messages['stereoGround'].to_bytes()) as event:
      self.assertFalse(event.stereoGround.hasStereoDepth)
      self.assertTrue(np.isinf(event.stereoGround.drivableLimitM))

  def test_real_cloud_wire_keeps_capture_frame_measured_coordinates_and_validity(self):
    import capnp
    log = capnp.load(str(ROOT / 'cereal/log.capnp'), imports=[str(ROOT / 'cereal')])
    tree = ast.parse((ROOT / 'nagaspilot/daemons/pointcloudd/pointcloudd.py').read_text())
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'PointcloudD')
    node = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == '_publish_processed')
    def message(name):
      event = log.Event.new_message(valid=False)
      event.init(name)
      return event
    namespace = dict(np=np, Any=object, messaging=SimpleNamespace(new_message=message), cloudlog=Mock())
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'pointcloud_wire_method', 'exec'), namespace)
    host = SimpleNamespace(pm=Mock(), stats=Mock(), reconstructor=SimpleNamespace(calibration_id=self.identity),
                           _source_frame_id=42, _geo_valid=False, _geo_lat=0, _geo_lon=0, _geo_alt=0,
                           _geo_heading=0, _geo_accuracy=0, _geo_source='none')
    points = np.array([[10, -1, 0.2], [15, 2, 0.3]], np.float32)
    namespace['_publish_processed'](host, 1_000_000_000, points, None, {}, {})
    host.pm.send.assert_called_once()
    with log.Event.from_bytes(host.pm.send.call_args.args[1].to_bytes()) as event:
      self.assertTrue(event.valid)
      cloud = event.pointcloudProcessed
      self.assertEqual((cloud.timestamp, cloud.frameId), (1_000_000_000, 42))
      self.assertEqual(cloud.calibrationId, self.identity)
      self.assertEqual(cloud.coordinateFrame, 'base_footprint')
      self.assertEqual(cloud.validPoints, 2)
      self.assertFalse(cloud.hasGeoPosition)
      np.testing.assert_allclose([[p.x, p.y, p.z] for p in cloud.points], points)

  def test_real_stereo_wire_is_valid_only_with_usable_depth_and_reports_gpu_failure(self):
    import capnp
    log = capnp.load(str(ROOT / 'cereal/log.capnp'), imports=[str(ROOT / 'cereal')])
    tree = ast.parse((ROOT / 'nagaspilot/daemons/stereod/stereod.py').read_text())
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'StereoD')
    node = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == '_publish')
    def message(name):
      event = log.Event.new_message(valid=False)
      event.init(name)
      return event
    namespace = dict(np=np, messaging=SimpleNamespace(new_message=message))
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'stereo_wire_method', 'exec'), namespace)
    host = SimpleNamespace(pm=Mock(), enabled=True, frame_id=42, width=32, height=32, Q=self.cal.Q,
                           _fault=False, _fault_reason='', _consecutive_failures=0, _calibration_id=self.identity,
                           _metrics=SimpleNamespace(get_latency_stats=lambda _: {'sgm': {'avg_ms': 1.}}))
    data = np.ones((32, 32), np.float32)
    namespace['_publish'](host, data, data, 1_000_000_000)
    with log.Event.from_bytes(host.pm.send.call_args_list[0].args[1].to_bytes()) as event:
      self.assertTrue(event.valid)
      self.assertEqual(event.stereoDepth.calibrationId, self.identity)
      self.assertEqual((event.stereoDepth.timestamp, event.stereoDepth.frameId), (1_000_000_000, 42))
    host.pm.reset_mock()
    namespace['_publish'](host, data, np.zeros_like(data), 1_100_000_000)
    self.assertFalse(host.pm.send.call_args_list[0].args[1].valid)
    host.pm.reset_mock()
    host._fault, host._fault_reason = True, 'gpu_unavailable'
    namespace['_publish'](host, None, None, 1_200_000_000)
    host.pm.send.assert_called_once()
    with log.Event.from_bytes(host.pm.send.call_args.args[1].to_bytes()) as event:
      self.assertTrue(event.valid)
      self.assertTrue(event.stereoStatus.fault)
      self.assertEqual(event.stereoStatus.faultReason, 'gpu_unavailable')


@unittest.skipUnless(os.environ.get('EOP_GPU_TESTS') == '1', 'explicit real GPU acceptance required')
class TestActualGPU(unittest.TestCase):
  def test_full_q_and_measured_mount_share_one_gpu_projection(self):
    compute = load('mount_gpu_required', 'common/gpu_compute.py')
    gpu = compute.GPUCompute()
    self.addCleanup(gpu.close)
    projector = projection.OpenCLReprojector(gpu)
    self.addCleanup(projector.close)
    disparity = np.ones((32, 32), np.float32)
    confidence = np.ones_like(disparity)
    confidence[0, 0] = 0
    points = projector.reproject(disparity, calibration().Q, confidence, transform=mounting())
    np.testing.assert_allclose(points[16, 16], [10, 0, 1.2], atol=1e-5)
    np.testing.assert_allclose(points[28, 16], [10, 0, 0], atol=1e-5)
    self.assertTrue(np.isnan(points[0, 0]).all())

  def test_parked_obstacle_ground_shadow_and_left_sign(self):
    compute = load('mapping_gpu_required', 'common/gpu_compute.py')
    gpu = compute.GPUCompute()
    self.addCleanup(gpu.close)
    mapper = mapping.GPUGridMapper(gpu, bev.BEV_GRID, mounting(), np.array([0, 0, 1, 0], np.float32))
    self.addCleanup(mapper.close)
    disparity, confidence = np.zeros((32, 32), np.float32), np.zeros((32, 32), np.float32)
    for row, col, d in ((16, 16, 1), (22, 16, 0.5), (22, 24, 0.5), (28, 16, 1)):
      disparity[row, col], confidence[row, col] = d, 0.9
    observation = mapper.observe(disparity, confidence, calibration().Q)
    r, c, _ = bev.BEV_GRID.cells(10, 0)
    self.assertTrue(observation.occupied[r, c])
    self.assertFalse(observation.ground[r, c])
    r, c, _ = bev.BEV_GRID.cells(20, 0)
    self.assertFalse(observation.ground[r, c])  # Behind the parked car remains unknown.
    r, c, _ = bev.BEV_GRID.cells(20, -1.6)
    self.assertTrue(observation.ground[r, c])
    self.assertEqual(len(observation.points), 1)

  def test_fractional_gpu_rectification_matches_bilinear_fixture(self):
    compute = load('rectify_gpu_required', 'common/gpu_compute.py')
    rectification = load('rectify_gpu_test', 'system/inferenced/opencl_rectify.py')
    gpu = compute.GPUCompute()
    self.addCleanup(gpu.close)
    y, x = np.indices((16, 32), dtype=np.float32)
    mapper = rectification.OpenCLRectifier(gpu, (x+0.5, y+0.5, x+0.5, y+0.5))
    self.addCleanup(mapper.close)
    image = (x*2+y*4).astype(np.uint8)
    expected = np.zeros_like(image)
    expected[:-1, :-1] = image[:-1, :-1]+3
    left, right = mapper.remap(image, image)
    np.testing.assert_array_equal(left, expected)
    np.testing.assert_array_equal(right, expected)


if __name__ == '__main__':
  unittest.main()
