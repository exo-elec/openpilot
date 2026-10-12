"""A ModelState that starts as the small model and upgrades to the phone.

modeld hands us the small model it already loaded; the phone is swapped in underneath once the link is up and nothing is
in control, and modeld, which re-reads `model` every frame, sees `modelV2.big` change. A phone is on a cable that comes and
goes with the ignition and the app: joining must never block modeld's frame thread, so connecting is a background thread
that does link IO only; everything that touches the small model happens on the frame thread.

Rules (carried over from the Jetlink port, they are why this is safe):
- never swap while anything is in control: the two models disagree by ~195 m of planned path and the swap costs a frame
- the small model drives the first SMALL_WARMUP_FRAMES of a modeld start: its first frames in a process are slow
- the phone starts from a reset history on the swap frame; the first frames after it are its proof (engagement is held off
  for them by runtime.bigmodel and the gate)
- a phone that falls behind (remote_state.behind), a lost link, or a camera-drop ratio past DROP_LIMIT hands back to the
  small model from a reset history; each failure backs the next attempt off
"""
from __future__ import annotations

import threading
import time

from nagaspilot.bigmodel import protocol as P

REJOIN_DELAY = 5.0
REJOIN_DELAY_MAX = 60.0
REJOIN_DELAY_QUICK = 1.0
QUICK_RETRIES = 3
STABLE_SECONDS = 60.0     # a join that held this long starts a new failure streak
KEEPALIVE_PERIOD = 10.0
PING_TIMEOUT = 2.0
# modeld's share of dropped camera frames past which the phone hands back, short of selfdrived's 1 % modeldLagging line:
# one dropped frame is forgiven, a second within ~6.5 s is not
DROP_LIMIT = 0.0075
SMALL_WARMUP_FRAMES = 3
LOST = 'lost link'
BEHIND = 'phone fell behind'
NOT_UP = 'could not bring up the phone model'
LEAVING = {LOST: P.LEAVE_LOST, BEHIND: P.LEAVE_BEHIND, P.LEAVE_STOPPED: P.LEAVE_STOPPED}


class Joining:
  """Duck-types modeld's ModelState. A read it does not define follows the model that is driving (__getattr__)."""

  def __init__(self, small, connect, build, reset_small=None, *, log, in_control=None, clock=time.monotonic,
               start: bool = True):
    self._small = small
    self._active = small
    self._connect = connect          # () -> (client, info), raises when the phone is not there
    self._build = build              # (client, info) -> RemoteModelState, on the frame thread
    self._reset_small = reset_small
    self._log = log
    self._clock = clock
    self._lock = threading.Lock()
    self._joined = None              # (client, info) handed to the frame thread, consumed by the swap
    self._available = False
    self._big = None
    self._retired = None             # (what, why) for the join thread to say goodbye to and close
    self._rejoin = threading.Event()
    self._rejoin.set()
    self._rejoin_at = 0.0
    self._failures = 0
    self._joined_at = 0.0
    self._drops = self._lags = 0
    self._handovers = 0
    self._lagging = False
    self._frame_drop_ratio = 0.0
    self._small_frames = 0
    self._in_control = True          # in control until told otherwise: a swap never lands on no information
    self._ask_in_control = in_control
    self._stop = threading.Event()
    self._loading = True
    self._thread = threading.Thread(target=self._join_loop, daemon=True)
    if start:
      self._thread.start()

  # -- what modeld and the facade read
  @property
  def big_model_available(self) -> bool:
    return not self._stop.is_set() and self._available and self._active is self._small

  @property
  def chestnut(self) -> bool:
    return getattr(self._active, 'chestnut', False)

  @property
  def handovers(self) -> int:
    return self._handovers

  @property
  def big_model_state(self) -> str:
    if self._stop.is_set():
      return 'unavailable'
    if self._active is not self._small:
      return 'running'
    if self.big_model_available:
      return 'ready'
    return 'retrying' if self._failures else 'joining'

  @property
  def client(self):
    return getattr(self._active, 'client', None)

  @property
  def desire_key(self):
    return self._small.desire_key

  @property
  def numpy_inputs(self):
    return self._small.numpy_inputs

  @property
  def get_action_from_model(self):
    return self._active.get_action_from_model

  @property
  def lat_delay(self):
    return self._active.lat_delay

  @lat_delay.setter
  def lat_delay(self, value):
    self._small.lat_delay = value
    if self._active is not self._small:
      self._active.lat_delay = value

  @property
  def frame_drop_ratio(self) -> float:
    return self._frame_drop_ratio

  @frame_drop_ratio.setter
  def frame_drop_ratio(self, value):
    self._frame_drop_ratio = value
    self._small.frame_drop_ratio = value

  @property
  def in_control(self) -> bool:
    return self._in_control

  @in_control.setter
  def in_control(self, value):
    self._in_control = value
    self._small.in_control = value

  def __getattr__(self, name):
    if name.startswith('_'):
      raise AttributeError(name)   # also keeps __init__ from recursing before _active exists
    return getattr(self._active, name)

  # -- the frame path
  def run(self, bufs, transforms, inputs, after_enqueue=None):
    if self._ask_in_control is not None:
      self.in_control = self._ask_in_control()
    if self._lagging:
      self._lagging = False
      self._demote(BEHIND)
    elif self._active is not self._small and self._frame_drop_ratio > DROP_LIMIT:
      self._log.warning("bigmodel: modeld dropped %.2f %% of camera frames behind the phone, the small model drives from this one",
                        self._frame_drop_ratio * 100)
      self._demote(BEHIND)
    self._maybe_swap()
    if self._active is self._small:
      self._small_frames += 1
      return self._small.run(bufs, transforms, inputs, after_enqueue)
    return self._run_big(bufs, transforms, inputs, after_enqueue)

  def _run_big(self, bufs, transforms, inputs, after_enqueue):
    big = self._big
    try:
      result = big.run(bufs, transforms, inputs, after_enqueue)
    except Exception:
      self._log.exception("bigmodel: the phone model failed mid-drive, back to the small model")
      self._demote(LOST)
      # the frame is re-run on the small model rather than propagated: modeld's own failure fallback is permanent, this one is retryable
      return self._small.run(bufs, transforms, inputs, None)
    if self._loading:
      self._loading = False
      self._log.warning("bigmodel: the phone model joined mid-drive, modelV2.big is now true")
    if big.behind:
      self._lagging = True            # this frame is published as it came; the next is the small model's
      self._handovers += 1
      self._log.warning("bigmodel: the phone %s, the small model drives from the next frame", big.behind)
    return result

  def _maybe_swap(self) -> None:
    if self._joined is None or self._in_control or self._small_frames < SMALL_WARMUP_FRAMES:
      return
    with self._lock:
      joined, self._joined = self._joined, None
      if joined is not None:
        self._available = False
    if joined is None:
      return
    client, info = joined
    try:
      t0 = self._clock()
      big = self._build(client, info)
      big.lat_delay = self._small.lat_delay
      self._log.warning("bigmodel: built the phone model state in %.0f ms", (self._clock() - t0) * 1000)
    except Exception:
      self._log.exception("bigmodel: could not bring up the phone model, staying small")
      with self._lock:
        self._retired = (client, NOT_UP)
      self._back_off()
      return
    self._big = big
    self._joined_at = self._clock()
    self._handovers += 1
    self._active = big

  def _demote(self, why: str) -> None:
    """Back to the small model from a reset history. On the frame thread, so nothing here waits: the join thread says
    goodbye and closes the link moments later."""
    big, self._big = self._big, None
    if why == BEHIND:
      self._lags += 1
    else:
      self._drops += 1
    with self._lock:
      self._retired = (big, why)
    self._back_off()
    self._active = self._small
    self._handovers += 1
    self._loading = True
    if self._reset_small is not None:
      self._reset_small()

  def _back_off(self) -> None:
    held = self._clock() - self._joined_at if self._joined_at else 0.0
    stable = bool(self._joined_at) and held > STABLE_SECONDS
    self._failures = 1 if stable else self._failures + 1
    self._joined_at = 0.0
    if self._failures <= QUICK_RETRIES:
      delay = REJOIN_DELAY_QUICK
    else:
      delay = min(REJOIN_DELAY * 2 ** (self._failures - QUICK_RETRIES - 1), REJOIN_DELAY_MAX)
    self._rejoin_at = self._clock() + delay
    self._rejoin.set()
    self._log.warning("bigmodel: next attempt in %.0f s (failure %d, link held %.0f s, drop %d, lag %d this drive)",
                      delay, self._failures, held, self._drops, self._lags)

  # -- background: link IO only, never the small model
  def _leave(self, retired, why: str) -> None:
    client = getattr(retired, 'client', retired)
    trips = getattr(retired, 'trips', None)
    leave = getattr(client, 'leave', None)
    if leave is None:
      return
    try:
      leave(LEAVING.get(why, P.LEAVE_LOST), drops=self._drops, lags=self._lags, **(trips.summary() if trips else {}))
    except Exception:
      self._log.exception("bigmodel: could not say why the link is left")

  def _close_retired(self, retired, why: str) -> None:
    self._leave(retired, why)
    try:
      retired.close()
    except Exception:
      self._log.exception("bigmodel: closing the retired link")

  def _join_once(self) -> float | None:
    """One pass of the join loop; returns how long to wait before the next pass, None to wait for a rejoin signal."""
    with self._lock:
      retired, self._retired = self._retired, None
    if retired is not None:
      self._close_retired(*retired)
    if self._stop.is_set():
      return None
    left = self._rejoin_at - self._clock()
    if left > 0:
      return left
    self._rejoin.clear()
    try:
      client, info = self._connect()
    except Exception as e:
      self._log.warning("bigmodel: not joined yet (%s), retrying in %.0f s", e, REJOIN_DELAY)
      self._rejoin.set()
      return REJOIN_DELAY
    with self._lock:
      if self._stop.is_set():
        client.close()
        return None
      self._available = True
      self._joined = (client, info)
    self._log.warning("bigmodel: link ready, waiting for a window to swap")
    return None

  def _join_loop(self) -> None:
    from nagaspilot.bigmodel.priority import background_thread
    background_thread()   # off modeld's realtime core: a thread started after config_realtime_process inherits SCHED_FIFO
    while not self._stop.is_set():
      self._rejoin.wait()
      delay = self._join_once()
      if delay is not None and self._stop.wait(delay):
        return
      if self._available:
        self._keep_alive()

  def _keep_alive(self) -> None:
    """Ping a link that is ready and has nowhere to land (a drive with no stop is the whole drive), so a phone that went away
    is found here and not on the swap frame."""
    while not self._stop.is_set():
      if self._rejoin.wait(KEEPALIVE_PERIOD):
        return
      with self._lock:
        joined, self._joined = self._joined, None
      if joined is None:
        return              # swapped in on a frame, or closed under us
      try:
        joined[0].ping(timeout=PING_TIMEOUT)
      except Exception as e:
        self._available = False
        self._log.warning("bigmodel: the link died before it could be used (%s), reopening", e)
        try:
          joined[0].close()
        except Exception:
          self._log.exception("bigmodel: closing the dead link")
        self._back_off()
        return
      with self._lock:
        stopped = self._stop.is_set()
        if not stopped:
          self._joined = joined
      if stopped:
        joined[0].leave(P.LEAVE_STOPPED)
        joined[0].close()
        return

  def close(self) -> None:
    first = not self._stop.is_set()
    self._stop.set()
    self._available = False
    self._rejoin.set()
    with self._lock:
      joined, self._joined = self._joined, None
    if joined is not None:
      if first:
        joined[0].leave(P.LEAVE_STOPPED)
      joined[0].close()
    big, self._big = self._big, None
    if big is not None:
      if first:
        self._leave(big, P.LEAVE_STOPPED)
      big.close()
