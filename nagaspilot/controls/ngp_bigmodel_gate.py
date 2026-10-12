"""Engagement gate for the big driving model (phone over USB, or the eGPU).

Pure policy, like ngp_lead_departure: inputs are the two runtime params modeld writes and one liveness
flag, output says which events selfdrived should raise. Mirrors upstream's selfdrived handling of the
Chestnut model (UsbGpuLoading / UsbGpuActive), so both big-model sources share one contract:

- loading: the big model is joining or proving. Engaging is refused ("Big Model Loading", NO_ENTRY).
- active: True while the big model drives, False once it handed back after having driven or failed to
  load, None when it was never started (the param is removed at modeld start). A transition to False
  announces "Big Model Failed" once; if the big model had been driving while engaged, it also
  soft-disables, so the driver is never left on a different model without being asked. Once the
  driver is disengaged that is over (as in upstream's selfdrived): the small model is available
  for a later engagement without another soft-disable.
- model_dead: modelV2 was seen and has stopped (the big model stalled the frame loop).

modeld writes BigModelActive=False only after the big model has been active or failed to load, never
for a link that simply is not there.
"""
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class BigModelGateOut:
  loading: bool = False
  failed: bool = False         # the big model is in a failed state now
  announce_failed: bool = False  # first cycle of that state: raise the permanent alert
  soft_disable: bool = False   # it was driving and failed: raise the soft-disable alert


def tier_flag(*values: bool | None) -> bool | None:
  """Combine the same flag from several big-model sources (phone link, eGPU): True if any is True,
  else False if any is False, else None when none has been written."""
  if any(v is True for v in values):
    return True
  if any(v is False for v in values):
    return False
  return None


# upstream selfdrived's `warmup_sec`: how long after a big-model load ends (or fails) the liveness, localizer and frame-drop
# checks are held back, so a swap's stall is not read as a system fault
SETTLING_S = 5.0

# How often the params are read. selfdrived runs at 100 Hz and the flags only change at a swap or a hand-back, so
# 4 Hz keeps a branch with no big-model source from paying file reads every cycle.
READ_PERIOD = 0.25

# Param names read on NGP10 (the phone link, then the eGPU). A branch with another naming rule passes its own.
LOADING_KEYS = ("BigModelLoading", "EgpuDrivingLoading")
ACTIVE_KEYS = ("BigModelActive", "EgpuDrivingActive")


class NGPBigModelGate:
  def __init__(self, loading_keys: tuple[str, ...] = LOADING_KEYS, active_keys: tuple[str, ...] = ACTIVE_KEYS):
    self._loading_keys = loading_keys
    self._active_keys = active_keys
    self._failed = False
    self._was_active = False
    self._read_at = float('-inf')
    self._flags: tuple[bool, bool | None] = (False, None)
    self._loading = False
    self._ready_t = float('-inf')

  def update(self, loading: bool, active: bool | None, enabled: bool = True, model_dead: bool = False,
             now: float | None = None) -> BigModelGateOut:
    t = time.monotonic() if now is None else now
    if self._loading and not loading:
      self._ready_t = t                    # the load ended: upstream's `big_model_ready_t`
    self._loading = bool(loading)
    if active:
      self._was_active = True
    out = self._step(loading, active, model_dead)
    if out.announce_failed:
      self._ready_t = t                    # and a failure restarts it
    if not enabled and not (model_dead and bool(active)):
      self._was_active = False
    return out

  def _step(self, loading: bool, active: bool | None, model_dead: bool) -> BigModelGateOut:
    failed = active is False or (model_dead and bool(active))
    announce = failed and not self._failed
    self._failed = failed
    return BigModelGateOut(loading=bool(loading), failed=failed, announce_failed=announce,
                           soft_disable=failed and self._was_active)

  def events(self, get, enabled: bool, model_dead: bool = False, now: float | None = None) -> list[str]:
    """EventName names to raise this cycle. `get` is Params().get (BOOL params decode to True/False/None); the loading
    flags of every big-model source are combined, as are the active flags. The params are read every READ_PERIOD
    seconds (`now` is time.monotonic() unless a test supplies it), the rest of the cycles reuse the last reading."""
    t = time.monotonic() if now is None else now
    if t - self._read_at >= READ_PERIOD:
      self._read_at = t
      self._flags = (any(bool(get(k)) for k in self._loading_keys),
                     tier_flag(*(get(k) for k in self._active_keys)))
    out = self.update(self._flags[0], self._flags[1], enabled, model_dead, t)
    return (["bigModelLoading"] if out.loading else []) + (["bigModelFailed"] if out.announce_failed or out.soft_disable else [])

  def settling(self, now: float | None = None) -> bool:
    """Upstream's `big_model_settling`: a big model is loading, or the load ended (or failed) within SETTLING_S. While it
    is true selfdrived holds back the liveness (commIssue), localizer and frame-drop checks."""
    t = time.monotonic() if now is None else now
    return self._loading or t < self._ready_t + SETTLING_S
