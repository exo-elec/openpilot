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
- BigModelLoading / BigModelActive for selfdrived (ngp_bigmodel_gate.py): loading while the big model proves
  itself right after a swap (engagement held off), active while it drives, false once it has handed back.

Not exercised on a device.
"""
import numpy as np

from nagaspilot.controls.ngp_bigmodel_action import action_times
from nagaspilot.runtime import bigmodel_platform as platform

PROVING_FRAMES = 20  # jetlink.openpilot.model_state.PROVING_FRAMES: engagement stays off this long after a swap


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

  def __init__(self, joining, small, params, fallback_action):
    self._joining = joining
    self._small = small
    self._params = params
    self._fallback = fallback_action
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
  def _publish(self) -> None:
    running = self._joining.big_model_state == 'running'
    self._big_frames = self._big_frames + 1 if running else 0
    loading = running and self._big_frames <= PROVING_FRAMES
    if running:
      active = True
      self._was_running = True
    else:
      active = False if self._was_running else None
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

  def __init__(self, params, fallback_action):
    self.params = params
    self.fallback_action = fallback_action
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
    return BigModelFacade(joining, shim, self.params, self.fallback_action)
