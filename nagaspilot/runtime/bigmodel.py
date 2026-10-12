"""modeld's side of the big model on a phone over USB (T16): a small hook object between modeld and the NPBM link.

modeld keeps the loop and the small model. This module owns everything else, so the hook in modeld.py is a few
calls: prepare() before modeld goes realtime, attach() once the camera is up, then the facade stands in for the
model. nagaspilot.bigmodel.joining does the join, swap, hold and hand-back; this adapts it to NGP10's loop:

- NGP10 calls model.run(bufs, transforms, inputs, prepare_only); the joining model takes (.., after_enqueue). The facade
  maps them: a dropped camera frame (prepare_only) is a prepare-only run of the small model, and is run normally
  while the big model drives, as upstream does (it has no prepare_only): the server's history advances once per
  frame it is sent, and skipping one would cost a second frame and leave a hand-back one short.
- NGP10's inputs have no `action_t`; the facade builds it as upstream does from the lateral delay and the
  longitudinal actuator delay (the big model's smoothing, not the small model's), which modeld hands over.
- BigModelLoading / BigModelActive for selfdrived (ngp_bigmodel_gate.py), with the same meaning as upstream's
  ChestnutLoading / ChestnutActive: loading while a big model is expected and not yet driving or still proving itself,
  active true while it drives and false once it failed or handed back.
- The default policy (CHESTNUT) reproduces upstream's Chestnut behaviour with the phone in the GPU's place: if the
  cable is connected at start, engagement is held off (loading) for up to BIG_MODEL_TIMEOUT while the phone joins; a
  phone that has not joined by then is not used this drive; and a big model that hands back is not used again until the
  next start (upstream: "fall back to small", never switched back onroad). Joining at any time and rejoining after a
  hand-back is the LATE_JOIN policy.

Not exercised on a device.
"""
import time
from dataclasses import dataclass
import numpy as np

from nagaspilot.controls.ngp_bigmodel_action import action_times
from nagaspilot.runtime import bigmodel_platform as platform
from nagaspilot.runtime.bigmodel_link import UDC_ROOT, cable_connected, host_enumerated  # noqa: F401 (re-exported probes)

from nagaspilot.bigmodel.remote_state import PROVING_FRAMES
BIG_MODEL_TIMEOUT = 60.0  # upstream modeld's: how long a Chestnut load may hold engagement off at the start
ENUMERATION_TIMEOUT = 10.0  # upstream helpers.wait_for_chestnut(): how long a connected cable may take to enumerate


@dataclass(frozen=True)
class BigModelPolicy:
  """When the big model may come and go during a drive."""
  window_s: float      # a connected phone is waited for this long from the start, with engagement held off
  one_way: bool        # after a hand-back the big model is not used again this drive
  late_join: bool      # a phone may join after the window (needs window_s to have passed without one)


# upstream's Chestnut: loaded (or not) at the start, one-way fallback, never switched back onroad
CHESTNUT = BigModelPolicy(window_s=BIG_MODEL_TIMEOUT, one_way=True, late_join=False)
# the small model drives until the phone is there, which may be at any time, and it may rejoin
LATE_JOIN = BigModelPolicy(window_s=0.0, one_way=False, late_join=True)


class NgpSmallModel:
  """NGP10's ModelState as the joining model drives its small model: the attributes it reads and writes, and run() with
  after_enqueue."""

  desire_key = 'desire'

  def __init__(self, inner):
    self._inner = inner
    self.lat_delay = 0.0
    self.PLANPLUS_CONTROL = 1.0
    self.frame_drop_ratio = 0.0
    self.in_control = True

  @property
  def numpy_inputs(self):
    return self._inner.numpy_inputs

  def run(self, bufs, transforms, inputs, after_enqueue=None, prepare_only=False):
    out = self._inner.run(bufs, transforms, inputs, prepare_only)
    if after_enqueue is not None:
      after_enqueue()
    return out

  def __getattr__(self, name):
    if name.startswith('_'):
      raise AttributeError(name)
    return getattr(self._inner, name)


class BigModelFacade:
  """What modeld holds as `model` once the big model is attached."""

  def __init__(self, joining, small, params, fallback_action, policy: BigModelPolicy = CHESTNUT,
               clock=time.monotonic, cable=cable_connected, enumerated=host_enumerated, runnable=lambda: True):
    self._joining = joining
    self._small = small
    self._params = params
    self._fallback = fallback_action
    self.policy = policy
    self._clock = clock
    self._cable = cable
    self._enumerated = enumerated
    self._runnable = runnable    # a big model is built to run (upstream: chestnut_compiled())
    self._started = clock()
    self._cable_seen = False     # a phone's cable was connected during the window: a big model is expected
    self._cable_seen_at = 0.0
    self._host_up = False        # the phone has enumerated us
    self._cable_checked = float('-inf')
    self._given_up = False       # the join was stopped: no phone in time, or a one-way hand-back
    self._load_failed = False    # a phone was expected inside the window and never became the driving model
    self.delays: tuple[float, float] | None = None  # (lateral delay, longitudinal actuator delay), set by modeld
    self._handovers = joining.handovers
    self._big_frames = 0
    self._was_running = False
    self._loading: bool | None = None
    self._active: bool | None = None

  # -- what modeld writes before a frame
  @property
  def frame_drop_ratio(self) -> float:
    return self._joining.frame_drop_ratio

  @frame_drop_ratio.setter
  def frame_drop_ratio(self, value: float) -> None:
    self._joining.frame_drop_ratio = value

  # -- the frame
  @property
  def is_big(self) -> bool:
    return bool(self._joining.chestnut)

  def run(self, bufs, transforms, inputs, prepare_only=False):
    if self.delays is None:
      raise RuntimeError("modeld has not given the big model its delays")
    inputs['action_t'] = np.array(action_times(*self.delays), dtype=np.float32)
    if prepare_only and not self.is_big:
      return self._small.run(bufs, transforms, inputs, None, True)
    out = self._joining.run(bufs, transforms, inputs)
    self._publish()
    return out

  def action_from_output(self, model_output, prev_action, lat_action_t, long_action_t, v_ego):
    fn = self._joining.get_action_from_model if self.is_big else self._fallback
    return fn(model_output, prev_action, lat_action_t, long_action_t, v_ego)

  def handed_over(self) -> bool:
    """Did the driving model change since the last call? modeld resets its dropped-frame filter then, so the
    stall of a swap or a hand-back is not read as lag."""
    changed = self._joining.handovers != self._handovers
    self._handovers = self._joining.handovers
    return changed

  def __getattr__(self, name):
    if name.startswith('_'):
      raise AttributeError(name)
    return getattr(self._joining, name)

  def close(self) -> None:
    self._joining.close()

  # -- BigModelLoading / BigModelActive
  def _give_up(self) -> None:
    """Stop joining: the big model will not be used again this drive (upstream never switches back onroad)."""
    if not self._given_up:
      self._given_up = True
      self._joining.close()

  def _expecting(self, now: float) -> bool:
    """Is a big model expected and not yet driving? Upstream tries one only if it is compiled and the cable is connected at
    start. Here: a model is built, and the cable was seen connected inside the window."""
    if now - self._started >= self.policy.window_s:
      return False
    if now - self._cable_checked >= 0.5:   # the probes are sysfs and file reads: at most twice a second
      self._cable_checked = now
      if not self._runnable():
        return False                       # nothing built to run: nothing is attempted or said (upstream: UNCOMPILED)
      if not self._cable_seen and self._cable():
        self._cable_seen, self._cable_seen_at = True, now
      self._host_up = self._host_up or (self._cable_seen and bool(self._enumerated()))
    return self._cable_seen

  def _enumeration_timed_out(self, now: float) -> bool:
    """The cable was seen and the phone has not enumerated us in ENUMERATION_TIMEOUT: upstream's wait_for_chestnut() raising."""
    return self._cable_seen and not self._host_up and now - self._cable_seen_at >= ENUMERATION_TIMEOUT

  def _publish(self) -> None:
    now = self._clock()
    running = self._joining.big_model_state == 'running'
    self._big_frames = self._big_frames + 1 if running else 0
    proving = running and self._big_frames <= PROVING_FRAMES
    if running:
      self._was_running = True
    window_over = now - self._started >= self.policy.window_s
    waiting = not running and not self._was_running and not self._given_up and self._expecting(now)
    if waiting and self._enumeration_timed_out(now):
      # a cable nobody enumerated over (the app is not open): engagement is released at 10 s, not 60, and it is a failed load
      self._give_up()
      self._load_failed = True
      waiting = False
    if not running:
      if self._was_running and self.policy.one_way:
        self._give_up()
      elif not self._was_running and window_over and not self.policy.late_join:
        # the load window ended without a big model: upstream's load timeout. If a phone was expected this is a failed
        # load (ChestnutActive False); with no cable there was nothing to attempt and nothing is said.
        self._give_up()
        self._load_failed = self._cable_seen
    loading = proving or waiting
    if running:
      active = True
    else:
      active = False if (self._was_running or self._load_failed) else None
    if loading != self._loading:
      self._loading = loading
      self._params.put_bool_nonblocking(platform.LOADING, loading)
    if active != self._active:
      self._active = active
      if active is None:
        self._params.remove(platform.ACTIVE)
      else:
        self._params.put_bool_nonblocking(platform.ACTIVE, active)


class BigModelHook:
  """prepare() and attach() for modeld.main(); both return what modeld should keep doing if the link is off."""

  def __init__(self, params, fallback_action, policy: BigModelPolicy = CHESTNUT, models_dir=None, listener_factory=None):
    self.params = params
    self.fallback_action = fallback_action
    self.policy = policy
    self.models_dir = models_dir
    self.contract = None
    self.joining = None
    self._listener_factory = listener_factory
    self.listener = None
    # stale flags from a previous drive must not read as a big model that is loading or has failed; removing an
    # unset key is a no-op, and this runs once before the frame loop
    params.remove(platform.LOADING)
    params.remove(platform.ACTIVE)

  def _runnable(self) -> bool:
    """Is a big model built to run? The phone's reply can only be sliced with built model metadata; without it engagement is
    never held off for a model that cannot be used."""
    return self.contract is not None

  def mode(self) -> int:
    try:
      return int(self.params.get(platform.LINK, return_default=True) or 0)
    except Exception:
      return 0

  def prepare(self) -> bool:
    """Before modeld goes realtime: read the output layout and start listening for the phone. False leaves modeld unchanged."""
    if self.mode() == 0:
      return False
    from nagaspilot.bigmodel import metadata
    from nagaspilot.bigmodel.listener import Listener
    from openpilot.common.swaglog import cloudlog
    try:
      self.contract = metadata.load(self.models_dir) if self.models_dir else metadata.load()
      if self.contract is None:
        cloudlog.warning("bigmodel: no model metadata is built, staying on the small model")
        return False
      self.listener = (self._listener_factory or Listener)().start()
      return True
    except Exception:
      cloudlog.exception("bigmodel: could not be prepared, staying on the small model")
      self.contract = None
      return False

  def attach(self, small_model, vipc_client):
    """Once the camera is up and the small model is built: the facade, or the small model if it cannot join."""
    if self.contract is None or self.listener is None:
      return small_model
    from openpilot.common.swaglog import cloudlog
    try:
      shim = NgpSmallModel(small_model)
      joining = self._joining(shim)
    except Exception:
      cloudlog.exception("bigmodel: did not attach, staying on the small model")
      return small_model
    self.joining = joining
    return BigModelFacade(joining, shim, self.params, self.fallback_action, policy=self.policy, runnable=self._runnable)

  def _joining(self, shim):
    from nagaspilot.bigmodel.client import Client, LinkError
    from nagaspilot.bigmodel.joining import Joining
    from nagaspilot.bigmodel.remote_state import RemoteModelState
    from nagaspilot.runtime.bigmodel_face import make_action_function
    from nagaspilot.runtime.bigmodel_warp import NgpWarp, reset_function
    from openpilot.common.swaglog import cloudlog
    contract, listener = self.contract, self.listener
    action = make_action_function(self.fallback_action)

    def connect():
      sock = listener.take(0.5)
      if sock is None:
        raise LinkError('no phone has connected')
      client = Client(sock=sock)
      try:
        info = client.connect()
        if not info.ready:
          raise LinkError('the phone has no model ready')
        if (info.vision_floats, info.policy_floats) != (contract.vision_floats, contract.policy_floats):
          got, want = (info.vision_floats, info.policy_floats), (contract.vision_floats, contract.policy_floats)
          raise LinkError(f"the phone's model outputs {got} floats, this build expects {want}")
      except Exception:
        client.close()
        raise
      return client, info

    def build(client, info):
      from openpilot.selfdrive.modeld.parse_model_outputs import Parser
      return RemoteModelState(client, info, NgpWarp(shim.frames), parser=Parser(), vision_slices=contract.vision_slices,
                              policy_slices=contract.policy_slices, get_action_from_model=action, log=cloudlog)

    return Joining(shim, connect, build, reset_function(shim), log=cloudlog, in_control=InControl())


class InControl:
  """modeld's frame thread asks this before every frame: is openpilot in control of the car? True when unknown."""

  def __init__(self):
    self._sm = None

  def __call__(self) -> bool:
    try:
      if self._sm is None:
        import cereal.messaging as messaging
        self._sm = messaging.SubMaster(['carControl', 'selfdriveState'])
      sm = self._sm
      sm.update(0)
      # a SubMaster hands back default (disengaged) messages until the first arrives, and stale ones when a service dies:
      # neither says "not in control", so a swap waits for both services to be seen and alive
      if not all(sm.seen[s] and sm.alive[s] for s in ('carControl', 'selfdriveState')):
        return True
      return bool(sm['selfdriveState'].enabled or sm['carControl'].latActive or sm['carControl'].longActive)
    except Exception:
      return True
