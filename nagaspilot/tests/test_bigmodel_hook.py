import logging
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from nagaspilot.runtime import bigmodel_platform as platform
from nagaspilot.runtime.bigmodel import PROVING_FRAMES, BigModelFacade, NgpSmallModel

LOADING, ACTIVE = platform.LOADING, platform.ACTIVE


class FakeParams:
  def __init__(self):
    self.values = {}
    self.writes = []
    self.nonblocking = []

  def put_bool(self, key, value):
    self.values[key] = value
    self.writes.append((key, value))

  def put_bool_nonblocking(self, key, value):
    self.nonblocking.append(key)
    self.put_bool(key, value)

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
  facade.delays = (0.2, 0.15)
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


def test_action_times_are_built_as_upstream_does_from_the_delays():
  _, _, joining, _, facade = make()
  facade.delays = (0.2, 0.15)
  facade.run({}, {}, {'desire': np.zeros(8)})
  got = joining.seen_inputs[0]['action_t']
  assert got.dtype == np.float32
  # lateral: 0.2 + 0.0 (the big model's smoothing) + 0.05 + 0.025; longitudinal: 0.15 + 0.3 + 0.075
  assert list(got) == pytest.approx([0.275, 0.525])


def test_running_without_the_delays_is_an_error_not_a_wrong_input():
  _, _, _, _, facade = make()
  facade.delays = None
  with pytest.raises(RuntimeError):
    facade.run({}, {}, {})


def test_a_dropped_frame_is_prepare_only_on_the_small_model_and_a_normal_frame_for_the_big_one():
  inner, _, joining, _, facade = make()
  assert facade.run({}, {}, {}, prepare_only=True) == {'small': True}
  assert inner.runs == [True]
  joining.chestnut = True
  # upstream has no prepare_only: the big model runs every frame it is given, so its history stays one a frame
  assert facade.run({}, {}, {}, prepare_only=True) == {'big': True}
  assert inner.runs == [True]  # the small model was not run again
  assert len(joining.seen_inputs) == 1  # only the big frame went through the joining model


def test_the_action_function_follows_the_model_that_drives():
  _, _, joining, _, facade = make()
  assert facade.action_from_output({}, None, 0, 0, 0) == 'small-action'
  joining.chestnut = True
  assert facade.action_from_output({}, None, 0, 0, 0) == 'big-action'


def test_loading_blocks_engagement_for_the_proving_frames_then_active_stays_true():
  _, _, joining, params, facade = make()
  facade.run({}, {}, {})
  assert params.values.get(LOADING) is False and ACTIVE not in params.values
  joining.state = 'running'
  for _ in range(PROVING_FRAMES):
    facade.run({}, {}, {})
  assert params.values[LOADING] is True and params.values[ACTIVE] is True
  facade.run({}, {}, {})
  assert params.values[LOADING] is False and params.values[ACTIVE] is True


def test_a_handback_after_driving_sets_active_false_once_and_never_for_a_link_that_never_joined():
  _, _, joining, params, facade = make()
  for _ in range(3):
    facade.run({}, {}, {})
  assert (ACTIVE, False) not in params.writes
  joining.state = 'running'
  facade.run({}, {}, {})
  joining.state = 'ready'
  facade.run({}, {}, {})
  facade.run({}, {}, {})
  assert params.writes.count((ACTIVE, False)) == 1
  joining.state = 'running'
  facade.run({}, {}, {})
  assert params.values[ACTIVE] is True


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
  assert adapter.warp_source.built() and adapter.keys.link == platform.LINK


def test_the_owner_config_is_standard_library_and_names_this_adapter(monkeypatch, tmp_path):
  _jetlink()
  monkeypatch.setenv('PARAMS_ROOT', str(tmp_path))
  from nagaspilot.runtime import jetlink_adapter
  config = jetlink_adapter.owner_config()
  assert config.params_dir == tmp_path / 'd'
  assert config.adapter == 'nagaspilot.runtime.jetlink_adapter'
  assert (0xADD1, 0x0001) in config.chestnut_ids and (0x3801, 0x0001) in config.chestnut_ids


def test_param_writes_on_the_frame_thread_do_not_block():
  _, _, joining, params, facade = make()
  joining.state = 'running'
  facade.run({}, {}, {})
  assert set(params.nonblocking) == {LOADING, ACTIVE}


# -- in_control: the check that keeps a swap off a steering frame must fail towards "in control"
class FakeSub:
  def __init__(self, seen=True, alive=True, enabled=False, lat=False, lon=False):
    self.seen = {'carControl': seen, 'selfdriveState': seen}
    self.alive = {'carControl': alive, 'selfdriveState': alive}
    self.msgs = {'selfdriveState': type('S', (), {'enabled': enabled})(),
                 'carControl': type('C', (), {'latActive': lat, 'longActive': lon})()}

  def update(self, t):
    pass

  def __getitem__(self, k):
    return self.msgs[k]


def adapter_with(sm):
  _jetlink()
  from nagaspilot.runtime import jetlink_adapter
  a = jetlink_adapter.adapter()
  a._sm = sm
  return a


def test_in_control_is_true_before_any_message_arrives():
  assert adapter_with(FakeSub(seen=False)).in_control() is True


def test_in_control_is_true_when_a_service_died():
  assert adapter_with(FakeSub(alive=False)).in_control() is True


def test_in_control_follows_the_messages_once_they_are_live():
  assert adapter_with(FakeSub()).in_control() is False
  assert adapter_with(FakeSub(enabled=True)).in_control() is True
  assert adapter_with(FakeSub(lat=True)).in_control() is True
  assert adapter_with(FakeSub(lon=True)).in_control() is True


def test_in_control_is_true_when_the_read_fails():
  class Broken(FakeSub):
    def update(self, t):
      raise OSError('socket')
  assert adapter_with(Broken()).in_control() is True


def test_without_a_jetlink_checkout_the_link_is_not_started(monkeypatch, tmp_path):
  monkeypatch.setenv('JETLINK_REPO', str(tmp_path))
  from nagaspilot.runtime import jetlink_adapter
  assert not jetlink_adapter.jetlink_present()
  (tmp_path / 'jetlink').mkdir()
  assert jetlink_adapter.jetlink_present()


class Quiet:
  """Accepts any call and any attribute, answers None: a progress sink and a client that is only ever closed."""

  def __call__(self, *args, **kwargs):
    return None

  def __getattr__(self, name):
    if name.startswith('__'):
      raise AttributeError(name)
    return self


# -- the real pieces together: Jetlink's JoiningModelState over the shim, behind the facade
def test_a_swap_and_a_handback_through_jetlinks_real_joining_model(monkeypatch):
  _jetlink()
  import threading
  import time

  from jetlink.openpilot import joining as joining_module
  monkeypatch.setattr(joining_module, 'SMALL_WARMUP_FRAMES', 0)

  class Big:
    chestnut = True
    client = None
    behind = None
    vision_input_names = ['img', 'big_img']
    lat_delay = 0.0
    get_action_from_model = staticmethod(lambda *a: 'big-action')

    def __init__(self):
      self.inputs = []

    def run(self, bufs, transforms, inputs, after_enqueue=None):
      self.inputs.append(dict(inputs))
      return {'big': True}

    def close(self):
      pass

  big = Big()
  inner = FakeInner()
  shim = NgpSmallModel(inner)
  in_control = [True]
  joined = threading.Event()

  def connect(should_stop=None):
    joined.set()
    return Quiet(), 'spec'

  state = joining_module.JoiningModelState(shim, connect, lambda client, spec: big, None,
                                           progress=Quiet(), log=logging.getLogger('test'),
                                           in_control=lambda: in_control[0])
  try:
    params = FakeParams()
    facade = BigModelFacade(state, shim, params, lambda *a: 'small-action')
    facade.delays = (0.2, 0.15)
    inputs = {'desire': np.zeros(8, np.float32), 'traffic_convention': np.zeros(2, np.float32)}
    deadline = time.monotonic() + 5
    while state._joined is None and time.monotonic() < deadline:
      time.sleep(0.001)
    assert state._joined is not None, 'never joined'

    # engaged: the small model keeps driving and a dropped frame is prepare-only on it
    assert facade.run({}, {}, dict(inputs)) == {'small': True}
    assert facade.run({}, {}, dict(inputs), prepare_only=True) == {'small': True}
    assert inner.runs == [False, True] and not facade.is_big

    # disengaged: the swap lands, the big model drives and engagement is held off while it proves itself
    in_control[0] = False
    assert facade.run({}, {}, dict(inputs)) == {'big': True}
    assert facade.is_big and facade.action_from_output({}, None, 0, 0, 0) == 'big-action'
    assert params.values[LOADING] is True and params.values[ACTIVE] is True
    assert list(big.inputs[0]['action_t']) == pytest.approx([0.275, 0.525])
    assert facade.handed_over()
    for _ in range(PROVING_FRAMES):
      facade.run({}, {}, dict(inputs))
    assert params.values[LOADING] is False

    # a dropped frame while the big model drives still runs it
    before = len(big.inputs)
    assert facade.run({}, {}, dict(inputs), prepare_only=True) == {'big': True}
    assert len(big.inputs) == before + 1

    # the big model falls behind: this frame is its last, the next is the small model's, and the gate is told
    big.behind = 'held 5 frames in a row'
    assert facade.run({}, {}, dict(inputs)) == {'big': True}
    in_control[0] = True
    assert facade.run({}, {}, dict(inputs)) == {'small': True}
    assert not facade.is_big
    assert params.values[ACTIVE] is False
    assert facade.handed_over()
  finally:
    state.close()
    state._thread.join(5)


def test_a_static_board_tells_jetlink_before_it_is_imported(monkeypatch):
  from nagaspilot.runtime import jetlink_adapter
  monkeypatch.setenv('JETLINK_PLATFORM', 'placeholder')  # registers the undo for what the adapter writes
  monkeypatch.delenv('JETLINK_PLATFORM')
  monkeypatch.setattr(platform, 'USB_PLATFORM', 'agnos')
  jetlink_adapter._jetlink_path()
  assert 'JETLINK_PLATFORM' not in os.environ
  monkeypatch.setattr(platform, 'USB_PLATFORM', 'static')
  jetlink_adapter._jetlink_path()
  assert os.environ['JETLINK_PLATFORM'] == 'static'


def test_the_provisioning_run_gets_the_platform_too(monkeypatch):
  _jetlink()
  from nagaspilot.runtime import jetlink_adapter
  monkeypatch.setattr(platform, 'USB_PLATFORM', 'static')
  assert jetlink_adapter.owner_config().env['JETLINK_PLATFORM'] == 'static'
  monkeypatch.setattr(platform, 'USB_PLATFORM', 'agnos')
  assert 'JETLINK_PLATFORM' not in jetlink_adapter.owner_config().env
