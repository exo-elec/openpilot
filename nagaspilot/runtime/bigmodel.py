"""modeld's side of the big model on a phone over USB (T16): a small hook object between modeld and Jetlink.

modeld keeps the loop and the small model. This module owns everything else, so the hook in modeld.py is a few
calls: prepare() before modeld goes realtime, attach() once the camera is up, then the facade stands in for the
model. Jetlink's joining model (jetlink.openpilot.joining) does the join, swap, hold and hand-back; this adapts it
to NGP10's loop:

- NGP10 calls model.run(bufs, transforms, inputs, prepare_only); Jetlink's takes (.., after_enqueue). The facade
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
  next start (upstream: "fall back to small", never switched back onroad). Jetlink's own behaviour, joining at any time
  and rejoining after a hand-back, is the JETLINK policy.

Not exercised on a device.
"""
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from nagaspilot.controls.ngp_bigmodel_action import action_times
from nagaspilot.runtime import bigmodel_platform as platform

PROVING_FRAMES = 20  # jetlink.openpilot.model_state.PROVING_FRAMES: engagement stays off this long after a swap
BIG_MODEL_TIMEOUT = 60.0  # upstream modeld's: how long a Chestnut load may hold engagement off at the start
UDC_ROOT = Path('/sys/class/udc')


@dataclass(frozen=True)
class BigModelPolicy:
  """When the big model may come and go during a drive."""
  window_s: float      # a connected phone is waited for this long from the start, with engagement held off
  one_way: bool        # after a hand-back the big model is not used again this drive
  late_join: bool      # a phone may join after the window (needs window_s to have passed without one)


# upstream's Chestnut: loaded (or not) at the start, one-way fallback, never switched back onroad
CHESTNUT = BigModelPolicy(window_s=BIG_MODEL_TIMEOUT, one_way=True, late_join=False)
# Jetlink's own: the small model drives until the phone is there, which may be at any time, and it may rejoin
JETLINK = BigModelPolicy(window_s=0.0, one_way=False, late_join=True)


def cable_connected(root: Path = UDC_ROOT) -> bool:
  """Is a USB host attached to the gadget? Upstream's cable_connected(): the cable is there before the host has
  enumerated us. Any device controller state but 'not attached' counts ('powered', 'default', 'addressed',
  'configured'); no controller at all does not."""
  try:
    for udc in root.iterdir():
      if (udc / 'state').read_text().strip() not in ('', 'not attached'):
        return True
  except OSError:
    pass
  return False


class NgpSmallModel:
  """NGP10's ModelState as Jetlink's joining model drives its small model (modeld_v2's face, which stock modeld
  has not): the attributes it reads and writes, and run() with after_enqueue."""

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
               clock=time.monotonic, cable=cable_connected):
    self._joining = joining
    self._small = small
    self._params = params
    self._fallback = fallback_action
    self.policy = policy
    self._clock = clock
    self._cable = cable
    self._started = clock()
    self._cable_seen = False     # a phone's cable was connected during the window: a big model is expected
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
    """Is a big model expected and not yet driving: the cable was seen connected inside the start window?"""
    if now - self._started >= self.policy.window_s:
      return False
    if not self._cable_seen and now - self._cable_checked >= 0.5:   # at most twice a second until it is seen
      self._cable_checked = now
      self._cable_seen = bool(self._cable())
    return self._cable_seen

  def _publish(self) -> None:
    now = self._clock()
    running = self._joining.big_model_state == 'running'
    self._big_frames = self._big_frames + 1 if running else 0
    proving = running and self._big_frames <= PROVING_FRAMES
    if running:
      self._was_running = True
    window_over = now - self._started >= self.policy.window_s
    waiting = not running and not self._was_running and not self._given_up and self._expecting(now)
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

  def __init__(self, params, fallback_action, policy: BigModelPolicy = CHESTNUT):
    self.params = params
    self.fallback_action = fallback_action
    self.policy = policy
    self.jetlink = None
    self.adapter = None
    # stale flags from a previous drive must not read as a big model that is loading or has failed; removing an
    # unset key is a no-op, and this runs once before the frame loop
    params.remove(platform.LOADING)
    params.remove(platform.ACTIVE)

  def mode(self) -> int:
    from nagaspilot.runtime.jetlink_adapter import jetlink_present
    if not jetlink_present():
      return 0  # nothing to run: stay a plain small-model modeld
    try:
      return int(self.params.get(platform.LINK, return_default=True) or 0)
    except Exception:
      return 0

  def prepare(self) -> bool:
    """Before modeld goes realtime: bind Jetlink to this device and let it veto. False leaves modeld unchanged."""
    if self.mode() == 0:
      return False
    from nagaspilot.runtime import jetlink_adapter
    from openpilot.common.swaglog import cloudlog
    try:
      jetlink_adapter._jetlink_path()
      import jetlink.openpilot as jetlink
      self.adapter = jetlink_adapter.adapter()
      self.adapter.fallback_action = self.fallback_action
      self.jetlink = jetlink.bind(self.adapter)
      return self.jetlink.prepare()
    except Exception:
      cloudlog.exception("bigmodel: Jetlink could not be prepared, staying on the small model")
      self.jetlink = None
      return False

  def attach(self, small_model, vipc_client):
    """Once the camera is up and the small model is built: the facade, or the small model if it cannot join. The camera
    size is read only if the link is on (a client reports None before it has buffers)."""
    if self.jetlink is None:
      return small_model
    from openpilot.common.swaglog import cloudlog
    shim = NgpSmallModel(small_model)
    try:
      joining = self.jetlink.attach(shim, vipc_client.width, vipc_client.height)
    except Exception:
      cloudlog.exception("bigmodel: Jetlink did not attach, staying on the small model")
      return small_model
    if joining is None or joining is shim:
      return small_model
    return BigModelFacade(joining, shim, self.params, self.fallback_action, policy=self.policy)
