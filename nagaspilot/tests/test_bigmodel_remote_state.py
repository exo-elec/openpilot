import numpy as np
import pytest

from nagaspilot.bigmodel import protocol as P
from nagaspilot.bigmodel import remote_state as R
from nagaspilot.bigmodel.client import LinkError, ServerInfo


class Clock:
  def __init__(self):
    self.t = 100.0

  def __call__(self):
    return self.t


class FakeClient:
  """Replies on a virtual clock: a frame's reply is ready `latency` after it was sent."""

  def __init__(self, clock, latency=0.01):
    self.clock, self.latency = clock, latency
    self.sent = []
    self.ready = {}
    self.status = P.OK
    self.floats = 7
    self.dead = False
    self.closed = False

  def send_frame(self, frame_id, flags, desire, traffic, frame):
    seq = len(self.sent) + 1
    self.sent.append(dict(frame_id=frame_id, flags=flags, desire=list(desire), traffic=list(traffic), n=len(frame)))
    latency = self.latency(len(self.sent)) if callable(self.latency) else self.latency
    self.ready[seq] = (self.clock.t + latency, frame_id)
    return seq

  def recv_response(self, seq, timeout):
    at, frame_id = self.ready[seq]
    if self.clock.t + timeout >= at:
      self.clock.t = max(self.clock.t, at)
      out = np.full(self.floats, float(frame_id), np.float32).tobytes() if self.status == P.OK else b''
      return P.InferResp(frame_id, self.status, 1234, memoryview(out))
    self.clock.t += timeout
    return None

  def close(self):
    self.closed = True


class FakeWarp:
  output = bytes(P.FRAME_BYTES)

  def __init__(self):
    self.calls = 0

  def start_buffers(self, bufs, transforms):
    self.calls += 1

  def wait(self):
    pass


class FakeParser:
  def parse_vision_outputs(self, d):
    return {'hidden_state': d['hidden'].copy()}

  def parse_policy_outputs(self, d):
    return {'plan': d['plan'].copy()}


class Log:
  def warning(self, *a, **k): pass
  def exception(self, *a, **k): pass


def make(clock=None, latency=0.01):
  clock = clock or Clock()
  client = FakeClient(clock, latency)
  state = R.RemoteModelState(client, ServerInfo({'ok': True, 'vision_floats': 4, 'policy_floats': 3, 'ready': True}), FakeWarp(),
                             parser=FakeParser(), vision_slices={'hidden': slice(0, 4)}, policy_slices={'plan': slice(0, 3)},
                             get_action_from_model=lambda *a: 'action', log=Log(), clock=clock)
  return state, client, clock


def INPUTS():
  return {'desire': np.zeros(8, np.float32), 'traffic_convention': np.array([[1, 0]], np.float32)}


def run(state, **kw):
  return state.run({}, {}, INPUTS(), **kw)


def test_a_fresh_frame_is_parsed_with_the_comma_s_slices():
  state, client, _ = make()
  out = run(state)
  assert out['hidden_state'][0].tolist() == [1, 1, 1, 1] and out['plan'][0].tolist() == [1, 1, 1]
  assert client.sent[0]['n'] == P.FRAME_BYTES and client.sent[0]['traffic'] == [1, 0]


def test_the_first_frame_carries_reset_and_no_other():
  state, client, _ = make()
  for _ in range(3):
    run(state)
  assert [s['flags'] for s in client.sent] == [P.RESET, 0, 0]


def test_a_dropped_camera_frame_is_prepare_only_and_sends_nothing():
  state, client, _ = make()
  assert run(state, prepare_only=True) is None
  assert client.sent == [] and state.warp.calls == 1


def test_a_late_reply_holds_the_previous_output_and_is_read_by_the_next_frame():
  state, client, clock = make(latency=lambda n: 0.07 if n == 2 else 0.01)
  first = run(state)
  held = run(state)
  assert held is first and state.trips.held == 1
  clock.t += 0.05
  fresh = run(state)                       # reads frame 2's reply (stale, newer than held), then sends and gets frame 3's
  assert fresh['plan'][0].tolist() == [3, 3, 3]
  assert len(client.sent) == 3 and state.behind is None


def test_a_frame_still_unanswered_next_frame_is_held_without_sending():
  state, client, clock = make(latency=lambda n: 0.15 if n == 2 else 0.01)
  run(state)
  run(state)
  clock.t += 0.01
  run(state)
  assert len(client.sent) == 2             # one outstanding at a time
  assert state.trips.held == 2


def test_the_first_frame_waits_for_a_cold_phone_instead_of_holding():
  state, _, _ = make(latency=0.15)
  assert run(state)['plan'][0].tolist() == [1, 1, 1]


def test_no_answer_for_a_long_time_is_a_lost_link():
  state, _, _ = make(latency=lambda n: 0.01 if n == 1 else 10)
  run(state)
  run(state)
  with pytest.raises(LinkError, match='not answered'):
    for _ in range(10):
      state.run({}, {}, INPUTS())


def test_no_answer_to_the_first_frame_is_an_error():
  state, _, _ = make(latency=10)
  with pytest.raises(LinkError):
    run(state)


def hold_frames(state, n, clock):
  for _ in range(n):
    clock.t += 0.06
    run(state)


def test_five_holds_in_a_row_after_proving_is_behind():
  state, client, clock = make(latency=lambda n: 0.01 if n <= R.PROVING_FRAMES + 1 else 0.1)
  for _ in range(R.PROVING_FRAMES + 1):
    run(state)
  assert state.behind is None
  hold_frames(state, R.HOLDS_IN_A_ROW - 1, clock)
  assert state.behind is None
  hold_frames(state, 1, clock)
  assert state.behind == R.BEHIND_ROW


def test_one_held_frame_while_proving_is_behind_but_not_the_settling_frames():
  state, _, clock = make(latency=lambda n: 0.07 if n in (2, 8) else 0.01)
  run(state)
  run(state)                                  # frame 2 held: inside SETTLING_FRAMES, forgiven
  assert state.behind is None
  clock.t += 0.05
  for _ in range(5):
    run(state)
  assert state.behind is None
  run(state)                                  # frame 8, a held one
  assert state.behind == R.BEHIND_PROVING


def test_an_occasional_hold_after_proving_is_a_tail_not_behind():
  state, client, clock = make(latency=lambda n: 0.07 if n % 7 == 0 else 0.01)
  for _ in range(60):
    clock.t += 0.05
    run(state)
    if state._frames_since_swap <= R.PROVING_FRAMES:
      state.behind = None
  assert state.trips.held >= 5 and state.behind is None


def test_many_holds_in_a_window_is_behind_even_if_never_in_a_row():
  state, _, clock = make(latency=lambda n: 0.07 if n % 2 == 0 else 0.01)
  state._frames_since_swap = R.PROVING_FRAMES
  for _ in range(60):
    clock.t += 0.01
    run(state)
    if state.behind:
      break
  assert state.behind == R.BEHIND_RATE


def test_a_non_finite_frame_is_held_not_published():
  state, client, _ = make()
  first = run(state)
  client.status = P.NOT_FINITE
  assert run(state) is first and state.trips.held == 1


def test_a_refused_first_frame_is_an_error():
  state, client, _ = make()
  client.status = P.NOT_FINITE
  with pytest.raises(LinkError):
    run(state)


def test_a_reply_that_is_not_the_promised_size_is_an_error():
  state, client, _ = make()
  client.floats = 9
  with pytest.raises(LinkError, match='promised'):
    run(state)


def test_a_phone_failure_status_is_an_error():
  state, client, _ = make()
  client.status = P.INTERNAL
  with pytest.raises(LinkError):
    run(state)


def test_a_nan_in_the_reply_is_an_error():
  state, client, _ = make()
  orig = client.recv_response

  def nan(seq, timeout):
    r = orig(seq, timeout)
    return P.InferResp(r.frame_id, r.status, r.server_us, memoryview(np.full(7, np.nan, np.float32).tobytes()))
  client.recv_response = nan
  with pytest.raises(LinkError, match='finite'):
    run(state)


def test_after_enqueue_is_called_once_per_frame():
  state, _, _ = make()
  calls = []
  run(state, after_enqueue=lambda: calls.append(1))
  assert calls == [1]


def test_trips_summary_reports_percentiles():
  state, _, clock = make()
  for _ in range(5):
    run(state)
  s = state.trips.summary()
  assert s['frames'] == 5 and s['held'] == 0 and 'p99_ms' in s and s['server_ms'] == pytest.approx(1.2, abs=.1)
