import logging
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from nagaspilot.runtime.bigmodel import PROVING_FRAMES, BigModelFacade, NgpSmallModel


class FakeParams:
  def __init__(self):
    self.values = {}
    self.writes = []

  def put_bool(self, key, value):
    self.values[key] = value
    self.writes.append((key, value))

  def remove(self, key):
    self.values.pop(key, None)
    self.writes.append((key, None))


class FakeInner:
  def __init__(self):
    self.numpy_inputs = {'desire': np.zeros(1)}
    self.vision_input_names = ['img', 'big_img']
    self.runs = []

  def run(self, bufs, transforms, inputs, prepare_only):
    self.runs.append(prepare_only)
    return {'small': True}


class FakeJoining:
  def __init__(self, small):
    self.small = small
    self.state = 'joining'
    self.handovers = 0
    self.frame_drop_ratio = 0.0
    self.chestnut = False
    self.seen_inputs = []
    self.get_action_from_model = lambda *a: 'big-action'

  @property
  def big_model_state(self):
    return self.state

  def run(self, bufs, transforms, inputs, after_enqueue=None):
    self.seen_inputs.append(dict(inputs))
    return {'big': self.chestnut}

  def close(self):
    pass


def make():
  inner = FakeInner()
  small = NgpSmallModel(inner)
  joining = FakeJoining(small)
  params = FakeParams()
  facade = BigModelFacade(joining, small, params, lambda *a: 'small-action')
  return inner, small, joining, params, facade


def test_the_small_model_shim_maps_run_and_calls_after_enqueue():
  inner = FakeInner()
  small = NgpSmallModel(inner)
  called = []
  assert small.run({}, {}, {}, lambda: called.append(1), True) == {'small': True}
  assert inner.runs == [True] and called == [1]
  assert small.desire_key == 'desire' and small.vision_input_names == ['img', 'big_img']
  small.lat_delay = 0.2
  assert small.lat_delay == 0.2 and not hasattr(inner, 'lat_delay')


def test_action_times_are_added_to_the_inputs():
  _, _, joining, _, facade = make()
  facade.action_t = (0.3, 0.4)
  facade.run({}, {}, {'desire': np.zeros(8)})
  assert joining.seen_inputs[0]['action_t'].dtype == np.float32
  assert list(joining.seen_inputs[0]['action_t']) == pytest.approx([0.3, 0.4])


def test_a_dropped_frame_is_a_prepare_only_run_on_the_small_model_and_skipped_on_the_big_one():
  inner, _, joining, _, facade = make()
  assert facade.run({}, {}, {}, prepare_only=True) == {'small': True}
  assert inner.runs == [True]
  joining.chestnut = True
  assert facade.run({}, {}, {}, prepare_only=True) is None
  assert inner.runs == [True]  # not run again


def test_the_action_function_follows_the_model_that_drives():
  _, _, joining, _, facade = make()
  assert facade.action_from_output({}, None, 0, 0, 0) == 'small-action'
  joining.chestnut = True
  assert facade.action_from_output({}, None, 0, 0, 0) == 'big-action'


def test_loading_blocks_engagement_for_the_proving_frames_then_active_stays_true():
  _, _, joining, params, facade = make()
  facade.run({}, {}, {})
  assert params.values.get("BigModelLoading") is False and "BigModelActive" not in params.values
  joining.state = 'running'
  for _ in range(PROVING_FRAMES):
    facade.run({}, {}, {})
  assert params.values["BigModelLoading"] is True and params.values["BigModelActive"] is True
  facade.run({}, {}, {})
  assert params.values["BigModelLoading"] is False and params.values["BigModelActive"] is True


def test_a_handback_after_driving_sets_active_false_once_and_never_for_a_link_that_never_joined():
  _, _, joining, params, facade = make()
  for _ in range(3):
    facade.run({}, {}, {})
  assert ("BigModelActive", False) not in params.writes
  joining.state = 'running'
  facade.run({}, {}, {})
  joining.state = 'ready'
  facade.run({}, {}, {})
  facade.run({}, {}, {})
  assert params.writes.count(("BigModelActive", False)) == 1
  joining.state = 'running'
  facade.run({}, {}, {})
  assert params.values["BigModelActive"] is True


def test_handovers_are_reported_once_so_modeld_resets_its_drop_filter():
  _, _, joining, _, facade = make()
  assert not facade.handed_over()
  joining.handovers = 1
  assert facade.handed_over() and not facade.handed_over()


def test_drop_ratio_reaches_the_joining_model():
  _, _, joining, _, facade = make()
  facade.frame_drop_ratio = 0.02
  assert joining.frame_drop_ratio == 0.02


# -- the adapter against Jetlink's own conformance check (needs a Jetlink checkout: JETLINK_REPO)
def _jetlink():
  repo = os.environ.get('JETLINK_REPO')
  if repo and Path(repo).is_dir() and repo not in sys.path:
    sys.path.insert(0, repo)
  return pytest.importorskip('jetlink.openpilot.interface')


def test_the_adapter_conforms_to_jetlinks_interface_and_api():
  interface = _jetlink()
  from nagaspilot.runtime import jetlink_adapter
  adapter = jetlink_adapter.adapter()
  adapter._log = logging.getLogger('test')  # cloudlog is lazy and needs openpilot
  assert interface.conformance(adapter, interface.Openpilot) == []
  assert adapter.warp_source.built() and adapter.keys.link == 'ngp_bigmodel_link'


def test_the_owner_config_is_standard_library_and_names_this_adapter(monkeypatch, tmp_path):
  _jetlink()
  monkeypatch.setenv('PARAMS_ROOT', str(tmp_path))
  from nagaspilot.runtime import jetlink_adapter
  config = jetlink_adapter.owner_config()
  assert config.params_dir == tmp_path / 'd'
  assert config.adapter == 'nagaspilot.runtime.jetlink_adapter'
  assert (0xADD1, 0x0001) in config.chestnut_ids and (0x3801, 0x0001) in config.chestnut_ids
