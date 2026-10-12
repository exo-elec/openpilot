import logging

import numpy as np
import pytest

from nagaspilot.runtime import bigmodel_platform as platform
from nagaspilot.runtime.bigmodel import (BIG_MODEL_TIMEOUT, CHESTNUT, ENUMERATION_TIMEOUT, LATE_JOIN, PROVING_FRAMES, BigModelFacade,
                                         NgpSmallModel)
from nagaspilot.runtime.bigmodel_link import cable_connected, host_enumerated

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
    self.closed = 0
    self.get_action_from_model = lambda *a: 'big-action'

  @property
  def big_model_state(self):
    return self.state

  def run(self, bufs, transforms, inputs, after_enqueue=None):
    self.seen_inputs.append(dict(inputs))
    return {'big': self.chestnut}

  def close(self):
    self.closed += 1


def make(policy=LATE_JOIN):
  inner = FakeInner()
  small = NgpSmallModel(inner)
  joining = FakeJoining(small)
  params = FakeParams()
  facade = BigModelFacade(joining, small, params, lambda *a: 'small-action', policy=policy)
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


def in_control_with(sm):
  from nagaspilot.runtime.bigmodel import InControl
  a = InControl()
  a._sm = sm
  return a


def test_in_control_is_true_before_any_message_arrives():
  assert in_control_with(FakeSub(seen=False))() is True


def test_in_control_is_true_when_a_service_died():
  assert in_control_with(FakeSub(alive=False))() is True


def test_in_control_follows_the_messages_once_they_are_live():
  assert in_control_with(FakeSub())() is False
  assert in_control_with(FakeSub(enabled=True))() is True
  assert in_control_with(FakeSub(lat=True))() is True
  assert in_control_with(FakeSub(lon=True))() is True


def test_in_control_is_true_when_the_read_fails():
  class Broken(FakeSub):
    def update(self, t):
      raise OSError('socket')
  assert in_control_with(Broken())() is True


class Quiet:
  """Accepts any call and any attribute, answers None: a progress sink and a client that is only ever closed."""

  def __call__(self, *args, **kwargs):
    return None

  def __getattr__(self, name):
    if name.startswith('__'):
      raise AttributeError(name)
    return self


# -- the real pieces together: the joining model over the shim, behind the facade
def test_a_swap_and_a_handback_through_the_real_joining_model(monkeypatch):
  import threading
  import time

  from nagaspilot.bigmodel import joining as joining_module
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

  def connect():
    joined.set()
    return Quiet(), 'spec'

  state = joining_module.Joining(shim, connect, lambda client, spec: big, None,
                                  log=logging.getLogger('test'), in_control=lambda: in_control[0])
  try:
    params = FakeParams()
    facade = BigModelFacade(state, shim, params, lambda *a: 'small-action', policy=LATE_JOIN)
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


def test_with_the_link_off_the_hook_touches_neither_the_camera_client_nor_any_param_but_a_clear():
  from nagaspilot.runtime.bigmodel import BigModelHook

  class Exploding:
    @property
    def width(self):
      raise AssertionError('the camera size must not be read while the link is off')

    height = width

  class OffParams(FakeParams):
    def get(self, key, return_default=False):
      assert key == platform.LINK
      return 0

  params = OffParams()
  hook = BigModelHook(params, lambda *a: None)
  assert hook.prepare() is False
  small = object()
  assert hook.attach(small, Exploding()) is small
  # only the clear of stale flags at start: no put_bool, nothing else
  assert params.writes == [(platform.LOADING, None), (platform.ACTIVE, None)]


# -- upstream's Chestnut behaviour with the phone in the GPU's place (the CHESTNUT policy)
class Clock:
  def __init__(self):
    self.t = 100.0

  def __call__(self):
    return self.t


def chestnut(cable=True, enumerated=True, runnable=True):
  inner = FakeInner()
  small = NgpSmallModel(inner)
  joining = FakeJoining(small)
  params = FakeParams()
  clock = Clock()
  probes = {'on': cable, 'enumerated': enumerated, 'runnable': runnable}
  facade = BigModelFacade(joining, small, params, lambda *a: 'small-action', policy=CHESTNUT, clock=clock,
                          cable=lambda: probes['on'], enumerated=lambda: probes['enumerated'],
                          runnable=lambda: probes['runnable'])
  facade.delays = (0.2, 0.15)
  return joining, params, facade, clock, probes


def test_cable_connected_follows_upstreams_cc_orientation_and_falls_back_to_the_controller_state(tmp_path):
  cc = tmp_path / 'typec_cc_orientation'
  udc_root = tmp_path / 'udc'
  assert not cable_connected(udc_root, cc)                       # nothing to read at all
  udc = udc_root / 'fc000000.usb'
  udc.mkdir(parents=True)
  for state, expected in (('not attached', False), ('', False), ('powered', True), ('default', True),
                          ('addressed', True), ('configured', True)):
    (udc / 'state').write_text(state + '\n')
    assert cable_connected(udc_root, cc) is expected, state
  # upstream's signal: the CC pin says a cable is there while the controller still says nothing (the role negotiation)
  (udc / 'state').write_text('not attached\n')
  cc.write_text('0\n')
  assert not cable_connected(udc_root, cc)
  cc.write_text('1\n')
  assert cable_connected(udc_root, cc)
  cc.write_text('2\n')
  assert cable_connected(udc_root, cc)


def test_the_host_has_enumerated_us_only_when_the_controller_is_configured(tmp_path):
  assert not host_enumerated(tmp_path / 'none')
  udc = tmp_path / 'fc000000.usb'
  udc.mkdir()
  for state, expected in (('not attached', False), ('powered', False), ('addressed', False), ('configured', True)):
    (udc / 'state').write_text(state + '\n')
    assert host_enumerated(tmp_path) is expected, state


def test_the_chestnut_policy_is_upstreams_numbers():
  assert BIG_MODEL_TIMEOUT == 60.0
  assert CHESTNUT.window_s == 60.0 and CHESTNUT.one_way and not CHESTNUT.late_join
  assert LATE_JOIN.window_s == 0.0 and not LATE_JOIN.one_way and LATE_JOIN.late_join


def test_with_the_cable_connected_engagement_is_held_off_while_the_phone_joins():
  joining, params, facade, clock, _ = chestnut(cable=True)
  joining.state = 'joining'
  facade.run({}, {}, {})
  assert params.values[LOADING] is True and ACTIVE not in params.values
  clock.t += 30
  facade.run({}, {}, {})
  assert params.values[LOADING] is True            # still waiting, inside the window
  joining.state = 'running'
  for _ in range(PROVING_FRAMES + 1):
    facade.run({}, {}, {})
  assert params.values[LOADING] is False and params.values[ACTIVE] is True


def test_with_no_cable_nothing_is_awaited_and_nothing_is_said_even_after_the_window():
  joining, params, facade, clock, _ = chestnut(cable=False)
  joining.state = 'joining'
  facade.run({}, {}, {})
  assert params.values.get(LOADING) is False
  clock.t += BIG_MODEL_TIMEOUT + 1
  facade.run({}, {}, {})
  assert joining.closed == 1                         # upstream: no chestnut, no attempt: this drive stays small
  assert ACTIVE not in params.values and params.values[LOADING] is False


def test_a_phone_that_has_not_joined_when_the_window_ends_is_a_failed_load_and_is_not_used_later():
  joining, params, facade, clock, _ = chestnut(cable=True)
  joining.state = 'joining'
  facade.run({}, {}, {})
  clock.t += BIG_MODEL_TIMEOUT + 1
  facade.run({}, {}, {})
  # upstream: ChestnutActive = (model is not None) -> False, and loading ends
  assert params.values[ACTIVE] is False and params.values[LOADING] is False
  assert joining.closed == 1
  facade.run({}, {}, {})
  assert joining.closed == 1                          # given up once, not repeatedly


def test_a_cable_first_seen_after_the_window_is_ignored():
  joining, params, facade, clock, cable = chestnut(cable=False)
  facade.run({}, {}, {})
  clock.t += BIG_MODEL_TIMEOUT + 1
  cable['on'] = True
  facade.run({}, {}, {})
  assert params.values[LOADING] is False and ACTIVE not in params.values


def test_a_cable_connected_a_little_after_start_but_inside_the_window_is_waited_for():
  joining, params, facade, clock, cable = chestnut(cable=False)
  joining.state = 'joining'
  facade.run({}, {}, {})
  assert params.values[LOADING] is False
  clock.t += 20
  cable['on'] = True
  facade.run({}, {}, {})
  assert params.values[LOADING] is True


def test_a_hand_back_is_one_way_the_join_is_stopped_and_active_stays_false():
  joining, params, facade, clock, _ = chestnut(cable=True)
  joining.state = 'running'
  facade.run({}, {}, {})
  joining.state = 'ready'            # handed back to the small model
  facade.run({}, {}, {})
  assert params.values[ACTIVE] is False and joining.closed == 1
  joining.state = 'running'          # even if a host were there again, the join has been stopped
  facade.run({}, {}, {})
  assert joining.closed == 1


def test_the_late_join_policy_never_stops_the_join_and_takes_a_rejoin():
  joining, params, facade, clock, _ = chestnut(cable=True)
  facade.policy = LATE_JOIN
  joining.state = 'running'
  facade.run({}, {}, {})
  joining.state = 'ready'
  facade.run({}, {}, {})
  joining.state = 'running'
  facade.run({}, {}, {})
  assert joining.closed == 0 and params.values[ACTIVE] is True


def test_chestnut_policy_through_the_real_joining_model_a_hand_back_is_not_followed_by_a_rejoin(monkeypatch):
  import threading
  import time

  from nagaspilot.bigmodel import joining as joining_module
  monkeypatch.setattr(joining_module, 'SMALL_WARMUP_FRAMES', 0)

  class Big:
    chestnut = True
    client = None
    behind = None
    vision_input_names = ['img', 'big_img']
    lat_delay = 0.0
    get_action_from_model = staticmethod(lambda *a: 'big-action')

    def run(self, bufs, transforms, inputs, after_enqueue=None):
      return {'big': True}

    def close(self):
      pass

  big = Big()
  inner = FakeInner()
  shim = NgpSmallModel(inner)
  connects = []
  joined = threading.Event()

  def connect():
    connects.append(1)
    joined.set()
    return Quiet(), 'spec'

  state = joining_module.Joining(shim, connect, lambda client, spec: big, None,
                                  log=logging.getLogger('test'), in_control=lambda: False)
  try:
    params = FakeParams()
    clock = Clock()
    facade = BigModelFacade(state, shim, params, lambda *a: 'small-action', policy=CHESTNUT, clock=clock, cable=lambda: True)
    facade.delays = (0.2, 0.15)
    inputs = {'desire': np.zeros(8, np.float32), 'traffic_convention': np.zeros(2, np.float32)}
    assert joined.wait(5)
    deadline = time.monotonic() + 5
    while state._joined is None and time.monotonic() < deadline:
      time.sleep(0.001)
    # engagement is held off while the phone is awaited, then the big model drives from the first frame
    assert facade.run({}, {}, dict(inputs)) == {'big': True}
    assert params.values[LOADING] is True
    for _ in range(PROVING_FRAMES):
      facade.run({}, {}, dict(inputs))
    assert params.values[LOADING] is False and params.values[ACTIVE] is True
    # the big model falls behind: handed back, and not rejoined although the host is still there and a quick rejoin is due
    big.behind = 'held 5 frames in a row'
    facade.run({}, {}, dict(inputs))
    assert facade.run({}, {}, dict(inputs)) == {'small': True}
    assert params.values[ACTIVE] is False
    seen = len(connects)
    time.sleep(joining_module.REJOIN_DELAY_QUICK + 0.6)
    assert len(connects) == seen
    assert facade.run({}, {}, dict(inputs)) == {'small': True} and not facade.is_big
  finally:
    state.close()
    state._thread.join(5)


def test_with_no_model_built_nothing_is_awaited_even_with_the_cable_connected():
  joining, params, facade, clock, _ = chestnut(cable=True, runnable=False)
  joining.state = 'joining'
  facade.run({}, {}, {})
  assert params.values.get(LOADING) is False                      # upstream: not compiled -> no attempt, no loading
  clock.t += BIG_MODEL_TIMEOUT + 1
  facade.run({}, {}, {})
  assert ACTIVE not in params.values and params.values[LOADING] is False   # and no model error either


def test_a_cable_that_never_enumerates_releases_engagement_at_ten_seconds_as_a_failed_load():
  joining, params, facade, clock, _ = chestnut(cable=True, enumerated=False)
  joining.state = 'joining'
  facade.run({}, {}, {})
  assert params.values[LOADING] is True
  clock.t += ENUMERATION_TIMEOUT - 1
  facade.run({}, {}, {})
  assert params.values[LOADING] is True
  clock.t += 2
  facade.run({}, {}, {})                                          # upstream's wait_for_chestnut(10) raised
  assert params.values[LOADING] is False and params.values[ACTIVE] is False and joining.closed == 1


def test_a_cable_that_enumerated_is_waited_for_the_whole_window_not_ten_seconds():
  joining, params, facade, clock, probes = chestnut(cable=True, enumerated=False)
  joining.state = 'joining'
  facade.run({}, {}, {})
  clock.t += 5
  probes['enumerated'] = True                                     # the app opened and the host enumerated us
  facade.run({}, {}, {})
  clock.t += 30
  facade.run({}, {}, {})
  assert params.values[LOADING] is True and joining.closed == 0   # 35 s in, still awaiting the model
  clock.t += 30
  facade.run({}, {}, {})
  assert params.values[ACTIVE] is False and joining.closed == 1   # 65 s: the window ended without a big model


def test_the_hook_counts_a_model_as_built_only_when_the_metadata_is(tmp_path):
  import pickle

  from nagaspilot.runtime.bigmodel import BigModelHook
  hook = BigModelHook(FakeParams(), lambda *a: None, models_dir=tmp_path)
  assert hook._runnable() is False          # nothing read yet: unknown never holds engagement off
  from nagaspilot.bigmodel import metadata
  assert metadata.load(tmp_path) is None     # no files
  for name, size in (('vision', 12), ('policy', 8)):
    with open(tmp_path / f'driving_{name}_metadata.pkl', 'wb') as f:
      pickle.dump({'output_slices': {name: slice(0, size)}, 'output_shapes': {'outputs': (1, size)}}, f)
  hook.contract = metadata.load(tmp_path)
  assert hook._runnable() is True
  assert (hook.contract.vision_floats, hook.contract.policy_floats) == (12, 8)
  with open(tmp_path / 'driving_policy_metadata.pkl', 'wb') as f:
    f.write(b'not a pickle')
  assert metadata.load(tmp_path) is None     # unreadable counts as not built


# -- prepare() and attach() with a metadata directory and a fake listener
@pytest.fixture
def stub_swaglog(monkeypatch):
  import sys
  import types
  mod = types.ModuleType('openpilot.common.swaglog')
  mod.cloudlog = logging.getLogger('test-cloudlog')
  monkeypatch.setitem(sys.modules, 'openpilot.common.swaglog', mod)


def write_metadata(path):
  import pickle
  for name, size in (('vision', 12), ('policy', 8)):
    with open(path / f'driving_{name}_metadata.pkl', 'wb') as f:
      pickle.dump({'output_slices': {name: slice(0, size)}, 'output_shapes': {'outputs': (1, size)}}, f)


class FakeListener:
  started = 0

  def start(self):
    FakeListener.started += 1
    return self

  def take(self, timeout):
    return None

  def close(self):
    pass


class LinkParams(FakeParams):
  def get(self, key, return_default=False):
    return 1 if key == platform.LINK else None


def test_prepare_does_nothing_with_the_link_off(stub_swaglog, tmp_path):
  from nagaspilot.runtime.bigmodel import BigModelHook
  write_metadata(tmp_path)
  FakeListener.started = 0
  hook = BigModelHook(FakeParams(), lambda *a: None, models_dir=tmp_path, listener_factory=FakeListener)
  assert hook.prepare() is False and hook.contract is None and FakeListener.started == 0


def test_prepare_without_built_metadata_stays_on_the_small_model(stub_swaglog, tmp_path):
  from nagaspilot.runtime.bigmodel import BigModelHook
  FakeListener.started = 0
  hook = BigModelHook(LinkParams(), lambda *a: None, models_dir=tmp_path, listener_factory=FakeListener)
  assert hook.prepare() is False and hook.contract is None and FakeListener.started == 0
  small = object()
  assert hook.attach(small, object()) is small


def test_prepare_then_attach_gives_the_facade_over_a_joining_model(stub_swaglog, tmp_path):
  from nagaspilot.runtime.bigmodel import BigModelFacade, BigModelHook
  write_metadata(tmp_path)
  FakeListener.started = 0
  hook = BigModelHook(LinkParams(), lambda *a: None, models_dir=tmp_path, listener_factory=FakeListener)
  assert hook.prepare() is True and FakeListener.started == 1 and hook._runnable()
  small = FakeInner()
  small.full_features_buffer = np.ones((1, 4, 2), np.float32)
  small.full_desire = np.ones((1, 4, 2), np.float32)
  small.prev_desire = np.ones(2, np.float32)
  small.frames = {}
  facade = hook.attach(small, object())
  try:
    assert isinstance(facade, BigModelFacade) and facade.policy is CHESTNUT
    assert facade.big_model_state in ('joining', 'retrying')
    assert facade.is_big is False
  finally:
    hook.joining.close()
