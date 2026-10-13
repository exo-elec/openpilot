"""A ModelState whose vision and policy run on the phone: one inference per camera frame over the NPBM link.

Everything that touches the car stays on the comma: cameras, calibration, the warp (the small model's own OpenCL frames),
the parser, the action. The phone is a function: the newest warped frame of each camera plus `desire` and
`traffic_convention` in, the raw vision and policy outputs out. It keeps the frame, feature and desire history
(server_ref.State is the reference); this side sends RESET on its first frame so a phone is never trusted across a restart.

Timing rules, carried over from the Jetlink port and kept because they are what makes a slow host safe:
- a reply not back HOLD_FRAME into the frame is not waited for; the previous output is published again (a held frame) and the
  late reply is read at the start of the next frame
- HOLDS_IN_A_ROW held frames, or more than HOLDS_ALLOWED within HOLD_WINDOW, make the model `behind` and Joining hands back
- in the first PROVING_FRAMES after a swap a single held frame is behind, except the first SETTLING_FRAMES (the reset frame
  and a cold phone's warm-up); a host that answers nothing for LOST_AFTER is a lost link (LinkError)
"""
from __future__ import annotations

import math
import os
import time
from collections import deque
from collections.abc import Callable

import numpy as np

from nagaspilot.bigmodel import protocol as P
from nagaspilot.bigmodel.client import Client, LinkError, ServerInfo

SEND_RAW_PRED = os.getenv('SEND_RAW_PRED')

HOLD_FRAME = 0.046        # how far into a frame run() waits for the reply before holding
LOST_AFTER = 0.2          # a request unanswered this long: the link is gone, not slow
HOLDS_IN_A_ROW = 5
HOLDS_ALLOWED = 20
HOLD_WINDOW = 10.0
PROVING_FRAMES = 20       # engagement is held off this long after a swap (runtime.bigmodel.PROVING_FRAMES)
SETTLING_FRAMES = 3
BEHIND_ROW = f'held {HOLDS_IN_A_ROW} frames in a row'
BEHIND_RATE = f'held more than {HOLDS_ALLOWED} frames in {HOLD_WINDOW:.0f} s'
BEHIND_PROVING = 'held a frame while proving itself'


class Trips:
  """Frame timing over the link, summarised for the LEAVE message. Bounded to the last minute at 20 Hz."""

  def __init__(self, clock=time.monotonic):
    self.frames = self.held = self.over = 0
    self._clock = clock
    self.started = clock()
    self._whole: deque[float] = deque(maxlen=1200)
    self._server: deque[float] = deque(maxlen=1200)

  def record(self, whole_s: float, server_us: int) -> None:
    self.frames += 1
    self.over += whole_s > 0.05
    self._whole.append(whole_s * 1e3)
    self._server.append(server_us / 1e3)

  def summary(self) -> dict:
    out = {'frames': self.frames, 'over': self.over, 'held': self.held, 'span_s': round(self._clock() - self.started, 1)}
    if self._whole:
      w = sorted(self._whole)
      out.update(p50_ms=round(w[len(w) // 2], 1), p99_ms=round(w[int(.99 * (len(w) - 1))], 1), max_ms=round(w[-1], 1),
                 server_ms=round(sum(self._server) / len(self._server), 1))
    return out


class RemoteModelState:
  """Duck-types modeld's ModelState for the phone. `warp` is a bigmodel_warp.NgpWarp (start_buffers / wait / output)."""

  chestnut = True            # modelV2.big: same role as a Chestnut's
  vision_input_names = ['img', 'big_img']
  desire_key = 'desire'

  def __init__(self, client: Client, info: ServerInfo, warp, *, parser, vision_slices: dict, policy_slices: dict,
               get_action_from_model: Callable, log, clock=time.monotonic):
    self.client, self.info, self.warp = client, info, warp
    self.parser = parser
    self.vision_slices, self.policy_slices = vision_slices, policy_slices
    self.get_action_from_model = get_action_from_model
    self.lat_delay = 0.0
    self.PLANPLUS_CONTROL = 1.0
    self.frame_drop_ratio = 0.0
    self.in_control = True
    self._log, self._clock = log, clock
    self.trips = Trips(clock)
    self.behind: str | None = None
    self._frame_id = 0
    self._need_reset = True
    self._pending: tuple[int, int, float] | None = None   # (seq, frame_id, sent_at) of an unanswered request
    self._last: dict | None = None                        # the newest parsed output, republished by a held frame
    self._holds: deque[float] = deque()
    self._holds_in_a_row = 0
    self._frames_since_swap = 0

  # -- what is sent and what comes back
  def _flags(self) -> int:
    return P.RESET if self._need_reset else 0

  def _packed(self, inputs: dict) -> np.ndarray:
    """The floats that go with the frame: the desire and traffic_convention the small model gets."""
    return np.concatenate([np.asarray(inputs['desire'], np.float32).reshape(-1), np.asarray(inputs['traffic_convention'], np.float32).reshape(-1)])

  def _parse(self, out: np.ndarray) -> dict:
    v = self.info.vision_floats
    vision, policy = out[:v], out[v:]
    vd = self.parser.parse_vision_outputs({k: vision[np.newaxis, s] for k, s in self.vision_slices.items()})
    pd = self.parser.parse_policy_outputs({k: policy[np.newaxis, s] for k, s in self.policy_slices.items()})
    combined = {**vd, **pd}
    if SEND_RAW_PRED:
      combined['raw_pred'] = out.copy()
    return combined

  def _take(self, resp: P.InferResp) -> dict:
    """The parsed output of a reply. A refused frame raises (Jetlink's NOT_FINITE -> LinkError): the comma then demotes to the small model,
    as it does for a Chestnut that returns a non-finite output. The phone has kept its last good state."""
    if resp.status == P.NOT_FINITE:
      raise LinkError('the phone refused the frame: its output was not finite')
    if resp.status != P.OK:
      raise LinkError(f'the phone failed the frame (status {resp.status})')
    out = np.frombuffer(resp.out, np.float32)
    if out.size != self.info.vision_floats + self.info.policy_floats:
      raise LinkError(f'reply of {out.size} floats, the phone promised {self.info.vision_floats + self.info.policy_floats}')
    if not np.isfinite(out).all():
      raise LinkError('the reply is not finite')
    return self._parse(out)

  # -- the frame
  def run(self, bufs, transforms, inputs, after_enqueue=None, prepare_only=False):
    t0 = self._clock()
    self.warp.start_buffers(bufs, transforms)
    self.warp.wait()
    if after_enqueue is not None:
      after_enqueue()
    if prepare_only:
      return None
    self._frames_since_swap += 1
    if self._pending is not None:
      # the previous frame's reply was late. It is read now; its output is newer than the one held, so it replaces it
      seq, _, sent = self._pending
      resp = self.client.recv_response(seq, max(t0 + HOLD_FRAME - self._clock(), 0.0))
      if resp is None:
        self._check_alive(sent)
        return self._hold()           # still not back: this frame is held and nothing new is sent
      self._pending = None
      self._last = self._take(resp)
    flags = self._flags()
    self._frame_id += 1
    seq = self.client.send_frame_packed(self._frame_id, flags, self._packed(inputs), self.warp.output)
    self._need_reset = False
    sent = self._clock()
    self._pending = (seq, self._frame_id, sent)
    first = self._last is None        # nothing to hold yet: wait for it (a cold phone's first frame is slow)
    resp = self.client.recv_response(seq, LOST_AFTER if first else max(t0 + HOLD_FRAME - sent, 0.0))
    if resp is None:
      if first:
        raise LinkError('no reply to the first frame')
      self._check_alive(sent)
      return self._hold()
    self._pending = None
    parsed = self._take(resp)
    self._holds_in_a_row = 0
    self._last = parsed
    self.trips.record(self._clock() - t0, resp.server_us)
    return parsed

  def _check_alive(self, sent: float) -> None:
    age = self._clock() - sent
    if age > LOST_AFTER:
      raise LinkError('the phone has not answered for %.0f ms' % (age * 1e3))

  def _hold(self) -> dict:
    """Publish the previous output again, and judge whether the host is behind."""
    now = self._clock()
    self.trips.held += 1
    self._holds.append(now)
    while self._holds and now - self._holds[0] > HOLD_WINDOW:
      self._holds.popleft()
    self._holds_in_a_row += 1
    if self.behind is None and self._frames_since_swap > SETTLING_FRAMES:
      if self._frames_since_swap <= PROVING_FRAMES:
        self.behind = BEHIND_PROVING
      elif self._holds_in_a_row >= HOLDS_IN_A_ROW:
        self.behind = BEHIND_ROW
      elif len(self._holds) > HOLDS_ALLOWED:
        self.behind = BEHIND_RATE
    return self._last

  def close(self) -> None:
    self.client.close()


class RemoteSpecModelState(RemoteModelState):
  """A combined (spec'd) model on the phone: one graph, the layout announced by the phone and allow-listed by the comma (client.py).

  The comma packs `desire` (the pulse), `traffic_convention` and `action_t` as the spec lays them out, sends them with the newest warped
  frames, and slices the reply (the output less hidden_state) with the spec's slices. `parse` is the output parser (modeld's
  Parser.parse_outputs). Everything about timing, holds and hand-back is the base class's.
  """

  def __init__(self, client: Client, info: ServerInfo, warp, *, parse: Callable[[dict], dict], get_action_from_model: Callable, log,
               clock=time.monotonic):
    if info.spec is None:
      raise ValueError('a spec contract needs the phone\'s model spec')
    self.spec = info.spec
    self._parse_outputs = parse
    self._prev_desire = np.zeros(self.spec.packed_shapes['desire'][0], np.float32)
    super().__init__(client, info, warp, parser=None, vision_slices={}, policy_slices={}, get_action_from_model=get_action_from_model, log=log,
                     clock=clock)

  def reset_history(self) -> None:
    self._prev_desire[:] = 0

  def _packed(self, inputs: dict) -> np.ndarray:
    width = self.spec.packed_shapes['desire'][0]
    desire = np.asarray(inputs['desire'], np.float32).reshape(-1)
    if desire.size != width:
      raise LinkError(f'desire of {desire.size} floats, the model takes {width}')
    desire = desire.copy()
    desire[0] = 0
    # the model decides when an action is done, so desire is a pulse on the rising edge
    pulse = np.where(desire - self._prev_desire > .99, desire, 0).astype(np.float32)
    self._prev_desire[:] = desire
    parts = {'desire': pulse, 'traffic_convention': np.asarray(inputs['traffic_convention'], np.float32).reshape(-1),
             'action_t': np.asarray(inputs['action_t'], np.float32).reshape(-1)}
    out = []
    for name, shape in self.spec.packed_shapes.items():
      if parts[name].size != math.prod(shape):
        raise LinkError(f'{name} of {parts[name].size} floats, the model takes {math.prod(shape)}')
      out.append(parts[name])
    return np.concatenate(out)

  def _flags(self) -> int:
    # like Jetlink's client: with the raw output being logged, ask for the whole vector so the log carries the real hidden state, not zeros
    return super()._flags() | (P.WANT_HIDDEN if SEND_RAW_PRED else 0)

  def _parse(self, out: np.ndarray) -> dict:
    """Jetlink's path: the full-length output (zeros where the hidden state stays on the phone), cut by the spec's own output_slices, then the
    fork's Parser.parse_outputs. A new dict per call: the parser's softmax works in place, so a held frame republishes the dict it parsed."""
    whole = self.spec.whole_output(out, whole=bool(SEND_RAW_PRED))
    combined = self._parse_outputs({k: whole[np.newaxis, s] for k, s in self.spec.output_slices.items()})
    if SEND_RAW_PRED:
      combined['raw_pred'] = whole.copy()
    return combined

  def _take(self, resp: P.InferResp) -> dict:
    if resp.status == P.NOT_FINITE:
      raise LinkError('the phone refused the frame: its output was not finite')
    if resp.status != P.OK:
      raise LinkError(f'the phone failed the frame (status {resp.status})')
    out = np.frombuffer(resp.out, np.float32)
    expect = self.spec.output_nelem if SEND_RAW_PRED else self.spec.reply_nelem
    if out.size != expect:
      raise LinkError(f'reply of {out.size} floats, the model promised {expect}')
    if not np.isfinite(out).all():
      raise LinkError('the reply is not finite')
    return self._parse(out)
