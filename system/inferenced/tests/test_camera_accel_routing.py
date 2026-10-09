#!/usr/bin/env python3
"""BackendType.ACCEL: drivable-area segmentation (TwinLiteNet+) for every camera, on whichever card is fitted."""

from __future__ import annotations

import os
import tempfile
import unittest  # noqa: TID251
from unittest.mock import MagicMock  # noqa: TID251

import numpy as np

from openpilot.system.inferenced.compute import BackendType, InferenceResult
from openpilot.system.inferenced.drivable import CARD_MODEL, DRIVABLE, LANE, OTHER, drivable_map, letterbox
from openpilot.system.inferenced.inferenced import InferenceD, InferenceJob

TRANSPORT = (54, 96)
NET_HW = (48, 80)  # stands in for 384x640, same 5:3 letterbox


def card_outputs(h: int, w: int, channels_last: bool = False) -> dict:
  """[drivable, lane] scores: left half drivable, a lane column at w // 4."""
  scores = np.full((2, h, w), -4.0, np.float32)
  scores[0, :, : w // 2] = 4.0
  scores[1, :, w // 4] = 4.0
  out = scores[None]
  return {'output_0': np.moveaxis(out, 1, -1) if channels_last else out}


class _Card:
  """Stand-in camera-tier backend that records what it loaded and ran."""

  def __init__(self, backend_type: BackendType, channels_last: bool = False):
    self.backend_type = backend_type
    self.channels_last = channels_last
    self.loaded: list[str] = []
    self.ran: list[str] = []
    self.inputs: list[np.ndarray] = []

  def is_available(self) -> bool:
    return True

  def load_model(self, config) -> bool:
    self.loaded.append(config.name)
    return True

  def infer(self, model_name, inputs):
    self.ran.append(model_name)
    self.inputs.append(inputs['input'])
    h, w = inputs['input'].shape[1:3]
    return InferenceResult(success=True, outputs=card_outputs(h, w, self.channels_last))


class _Hal:
  def __init__(self, cards):
    self._backends = cards
    self._models_cache: dict = {}

  def get_backend(self, bt):
    return self._backends.get(bt)

  def get_available_backends(self):
    return list(self._backends)

  def is_model_cached(self, name):
    return name in self._models_cache

  def cache_model(self, config):
    self._models_cache[config.name] = config


class TestCameraAccelRouting(unittest.TestCase):

  def setUp(self):
    self._tmp = tempfile.TemporaryDirectory()
    d = self._tmp.name
    self.hef, self.dxnn = os.path.join(d, f'{CARD_MODEL}.hef'), os.path.join(d, f'{CARD_MODEL}.dxnn')
    for path in (self.hef, self.dxnn):
      open(path, 'wb').close()
    artifacts = {BackendType.HAILO_8: ((self.hef, NET_HW),), BackendType.DX_M1: ((self.dxnn, NET_HW),)}
    self.models = {name: dict(artifacts) for name in ('seg_road', 'seg_side', 'seg_rear')}

  def tearDown(self):
    self._tmp.cleanup()

  def _daemon(self, cards):
    daemon = InferenceD.__new__(InferenceD)
    daemon.hal = _Hal(cards)
    daemon._accel_assignment = {}
    daemon._accel_degraded = {}
    daemon.CAMERA_ACCEL_MODELS = self.models
    return daemon

  def _job(self, model_name):
    frame = np.zeros((1, *TRANSPORT, 3), dtype=np.uint8)
    return InferenceJob(job_id=1, daemon_name='segd', backend_type=int(BackendType.ACCEL),
                        model_name=model_name, priority=2, timeout_ms=0,
                        input_data=frame.tobytes(), input_shape=frame.shape, input_dtype=str(frame.dtype))

  def test_hailo_runs_the_network_letterboxed(self):
    hailo = _Card(BackendType.HAILO_8, channels_last=True)
    daemon = self._daemon({BackendType.HAILO_8: hailo})
    job = self._job('seg_road')
    self.assertEqual(daemon._execute_job(job), (True, ''))
    self.assertEqual(hailo.loaded, [f'HAILO_8:{self.hef}'])
    self.assertEqual(hailo.inputs[0].shape, (1, *NET_HW, 3))          # letterboxed to the network
    self.assertEqual(job.output_shape, TRANSPORT)                    # map back at frame size
    self.assertEqual(job.output_dtype, 'uint8')

  def test_dx_m1m_runs_the_same_network(self):
    dx = _Card(BackendType.DX_M1)
    daemon = self._daemon({BackendType.DX_M1: dx})
    self.assertTrue(daemon._execute_job(self._job('seg_side'))[0])
    self.assertEqual(dx.loaded, [f'DX_M1:{self.dxnn}'])
    self.assertEqual(dx.inputs[0].shape, (1, *NET_HW, 3))

  def test_one_card_one_session_for_every_camera(self):
    hailo = _Card(BackendType.HAILO_8)
    daemon = self._daemon({BackendType.HAILO_8: hailo})
    for name in ('seg_road', 'seg_side', 'seg_rear'):
      self.assertTrue(daemon._execute_job(self._job(name))[0])
    self.assertEqual(len(hailo.loaded), 1)
    self.assertEqual(len(hailo.ran), 3)

  def test_two_cards_spread(self):
    hailo, dx = _Card(BackendType.HAILO_8), _Card(BackendType.DX_M1)
    daemon = self._daemon({BackendType.HAILO_8: hailo, BackendType.DX_M1: dx})
    for name in ('seg_road', 'seg_side', 'seg_rear'):
      self.assertTrue(daemon._execute_job(self._job(name))[0])
    self.assertEqual(daemon._accel_assignment['seg_road'], BackendType.HAILO_8)
    self.assertEqual(daemon._accel_assignment['seg_side'], BackendType.DX_M1)

  def test_no_card_is_not_available(self):
    daemon = self._daemon({BackendType.NPU: MagicMock()})
    ok, reason = daemon._execute_job(self._job('seg_road'))
    self.assertFalse(ok)
    self.assertIn('not available', reason)

  def test_load_failure_degrades_the_card(self):
    """No other model, no retry: the card is degraded and says so."""
    hailo = _Card(BackendType.HAILO_8)
    attempts: list[str] = []
    hailo.load_model = lambda c: attempts.append(c.name) and False
    daemon = self._daemon({BackendType.HAILO_8: hailo})
    for name in ('seg_road', 'seg_road', 'seg_side'):
      ok, reason = daemon._execute_job(self._job(name))
      self.assertFalse(ok)
      self.assertIn('not available', reason)  # segd / gridd switch off on this
    self.assertEqual(attempts, [f'HAILO_8:{self.hef}'])  # tried once
    self.assertIn(BackendType.HAILO_8, daemon._accel_degraded)

  def test_degraded_card_does_not_move_to_the_other_card(self):
    hailo, dx = _Card(BackendType.HAILO_8), _Card(BackendType.DX_M1)
    hailo.load_model = lambda c: False
    daemon = self._daemon({BackendType.HAILO_8: hailo, BackendType.DX_M1: dx})
    self.assertFalse(daemon._execute_job(self._job('seg_road'))[0])   # pinned to Hailo, fails
    self.assertFalse(daemon._execute_job(self._job('seg_road'))[0])   # stays degraded
    self.assertEqual(daemon._accel_assignment['seg_road'], BackendType.HAILO_8)
    self.assertEqual(dx.ran, [])
    # A camera not yet assigned is never given the degraded card
    self.assertTrue(daemon._execute_job(self._job('seg_side'))[0])
    self.assertEqual(daemon._accel_assignment['seg_side'], BackendType.DX_M1)

  def test_unknown_model(self):
    daemon = self._daemon({BackendType.HAILO_8: _Card(BackendType.HAILO_8)})
    ok, reason = daemon._execute_job(self._job('driving_vision'))
    self.assertFalse(ok)
    self.assertIn('Unknown camera accelerator model', reason)

  def test_card_does_segmentation_only(self):
    """Detection and the driving model stay on RKNN: none of them is a card id."""
    ids = set(InferenceD.CAMERA_ACCEL_MODELS)
    self.assertEqual(ids, {'seg_road', 'seg_wide', 'seg_tele', 'seg_side', 'seg_rear'})
    self.assertFalse([m for m in ids if 'yolo' in m or 'driving' in m])

  def test_both_cards_run_the_same_network(self):
    for per_card in InferenceD.CAMERA_ACCEL_MODELS.values():
      names = set()
      for card in (BackendType.HAILO_8, BackendType.DX_M1):
        (path, hw), = per_card[card]  # one artifact, no alternatives
        names.add(os.path.splitext(os.path.basename(path))[0])
        self.assertEqual(hw, (384, 640))
      self.assertEqual(names, {CARD_MODEL})


class TestDrivableMap(unittest.TestCase):

  def _map(self, outs, frame_hw=(540, 960), net_hw=(384, 640)):
    _, content = letterbox(np.zeros((*frame_hw, 3), np.uint8), net_hw)
    return drivable_map(outs, frame_hw, content, net_hw)

  def test_letterbox_matches_training(self):
    img, content = letterbox(np.zeros((1, 540, 960, 3), np.uint8), (384, 640))
    self.assertEqual(img.shape, (1, 384, 640, 3))
    self.assertEqual(content, (12, 0, 360, 640))  # 12 grey rows top and bottom, as TwinLiteNet+ trains
    self.assertEqual(int(img[0, 0, 0, 0]), 114)

  def test_nchw_and_nhwc_give_the_same_map(self):
    a = self._map(card_outputs(384, 640))
    b = self._map(card_outputs(384, 640, channels_last=True))
    np.testing.assert_array_equal(a, b)
    self.assertEqual(a.shape, (540, 960))
    self.assertEqual(int(a[300, 100]), DRIVABLE)
    self.assertEqual(int(a[300, 900]), OTHER)
    self.assertEqual(int(a[300, 240]), LANE)

  def test_letterbox_padding_is_cropped(self):
    outs = card_outputs(384, 640)
    scores = outs['output_0']
    scores[0, 0] = -4.0
    scores[0, 0, :12] = 4.0   # "drivable" only in the top padding
    m = self._map(outs)
    self.assertEqual(int((m == DRIVABLE).sum()), 0)

  def test_wrong_output_is_an_error(self):
    with self.assertRaises(ValueError):
      self._map({'x': np.zeros((1, 19, 48, 80), np.float32)})


class TestDxInputLayout(unittest.TestCase):
  """DX-COM may leave a .dxnn taking NCHW float; frames arrive NHWC uint8."""

  def _backend(self, input_shape):
    from openpilot.system.inferenced.deepx_dxnn import DeepXBackend
    backend = DeepXBackend.__new__(DeepXBackend)
    engine = MagicMock()
    engine.get_input_tensors_info.return_value = [{'shape': input_shape}]
    engine.run.side_effect = lambda bufs: [bufs[0]]
    backend._initialized = True
    backend._engines = {'m': engine}
    backend._nchw_input = {'m': DeepXBackend._wants_nchw(engine)}
    backend._stats = MagicMock(tasks_completed=0, total_exec_time_ms=0.0)
    return backend

  def test_nchw_model_gets_a_transposed_float_frame(self):
    frame = np.arange(2 * 4 * 3, dtype=np.uint8).reshape(1, 2, 4, 3)
    out = self._backend([1, 3, 2, 4]).infer('m', {'input': frame}).outputs['output_0']
    expected = frame.transpose(0, 3, 1, 2).astype(np.float32)
    np.testing.assert_array_equal(out.view(np.float32), expected.ravel())

  def test_nhwc_model_gets_the_frame_as_is(self):
    frame = np.arange(2 * 4 * 3, dtype=np.uint8).reshape(1, 2, 4, 3)
    out = self._backend([1, 2, 4, 3]).infer('m', {'input': frame}).outputs['output_0']
    np.testing.assert_array_equal(out, frame.ravel())


if __name__ == '__main__':
  unittest.main()
