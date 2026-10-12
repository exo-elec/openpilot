import pytest

from nagaspilot.bigmodel import joining as J
from nagaspilot.bigmodel import protocol as P


class Clock:
  def __init__(self):
    self.t = 1000.0

  def __call__(self):
    return self.t


class Log:
  def __init__(self):
    self.lines = []

  def warning(self, msg, *a):
    self.lines.append(msg % a if a else msg)

  def exception(self, msg, *a):
    self.lines.append('EXC ' + msg)


class Small:
  desire_key = 'desire'
  numpy_inputs = {}
  lat_delay = 0.0
  frame_drop_ratio = 0.0
  in_control = True
  get_action_from_model = 'small action'

  def __init__(self):
    self.runs = 0

  def run(self, bufs, transforms, inputs, after_enqueue=None):
    self.runs += 1
    return 'small'


class Client:
  def __init__(self, ping_error=None):
    self.dead = False
    self.left = []
    self.closed = 0
    self.pings = 0
    self.ping_error = ping_error

  def leave(self, reason, **stats):
    self.left.append((reason, stats))

  def close(self):
    self.closed += 1

  def ping(self, timeout=0):
    self.pings += 1
    if self.ping_error:
      raise self.ping_error


class Big:
  chestnut = True
  get_action_from_model = 'big action'
  lat_delay = 0.0

  def __init__(self, client, fail=False):
    self.client, self.fail, self.behind, self.trips = client, fail, None, None
    self.runs = 0

  def run(self, bufs, transforms, inputs, after_enqueue=None):
    self.runs += 1
    if self.fail:
      raise OSError('link gone')
    return 'big'

  def close(self):
    self.client.close()


def make(in_control=False, connect=None, build=None):
  clock, small, log = Clock(), Small(), Log()
  resets = []
  clients = []

  def default_connect():
    c = Client()
    clients.append(c)
    return c, object()

  j = J.Joining(small, connect or default_connect, build or (lambda c, i: Big(c)), lambda: resets.append(1), log=log,
                in_control=lambda: in_control_flag[0], clock=clock, start=False)
  in_control_flag = [in_control]
  j.test = type('T', (), dict(clock=clock, small=small, log=log, resets=resets, clients=clients, ctl=in_control_flag))
  return j


def frame(j):
  return j.run({}, {}, {})


def join(j):
  assert j._join_once() is None
  assert j.big_model_state == 'ready'


def warm(j):
  for _ in range(J.SMALL_WARMUP_FRAMES):
    assert frame(j) == 'small'


def test_the_small_model_drives_until_a_phone_is_joined():
  j = make()
  assert frame(j) == 'small' and j.big_model_state == 'joining' and not j.chestnut


def test_a_joined_phone_swaps_in_after_the_warmup_frames_and_not_while_in_control():
  j = make(in_control=True)
  join(j)
  for _ in range(5):
    assert frame(j) == 'small'
  j.test.ctl[0] = False
  assert frame(j) == 'big'
  assert j.big_model_state == 'running' and j.chestnut and j.handovers == 1
  assert j.get_action_from_model == 'big action'


def test_the_swap_waits_for_the_small_models_first_frames():
  j = make()
  join(j)
  assert [frame(j) for _ in range(J.SMALL_WARMUP_FRAMES + 1)] == ['small'] * J.SMALL_WARMUP_FRAMES + ['big']


def test_a_failure_mid_drive_reruns_the_frame_on_the_small_model_and_resets_it():
  j = make(build=lambda c, i: Big(c, fail=True))
  join(j)
  warm(j)
  assert frame(j) == 'small'
  assert j.big_model_state == 'retrying' and j.test.resets == [1] and j.handovers == 2
  assert j.chestnut is False


def test_a_phone_that_fell_behind_publishes_its_frame_then_hands_back_on_the_next():
  holder = {}

  def build(c, i):
    holder['big'] = Big(c)
    return holder['big']
  j = make(build=build)
  join(j)
  warm(j)
  assert frame(j) == 'big'
  holder['big'].behind = 'held too many'
  assert frame(j) == 'big'                    # published as it came
  assert frame(j) == 'small'
  assert j.handovers == 3 and j.test.resets == [1]


def test_a_camera_drop_ratio_past_the_limit_hands_back_before_the_frame():
  j = make()
  join(j)
  warm(j)
  assert frame(j) == 'big'
  j.frame_drop_ratio = J.DROP_LIMIT * 2
  assert frame(j) == 'small'
  assert j.test.small.frame_drop_ratio == J.DROP_LIMIT * 2


def test_a_drop_ratio_while_the_small_model_drives_does_nothing():
  j = make()
  join(j)
  j.frame_drop_ratio = 0.5
  assert j.handovers == 0


def test_backoff_is_quick_three_times_then_doubles_to_the_cap():
  j = make()
  delays = []
  for _ in range(8):
    before = j.test.clock()
    j._back_off()
    delays.append(j._rejoin_at - before)
  assert delays == [1, 1, 1, 5, 10, 20, 40, 60]


def test_a_join_that_held_a_minute_starts_a_new_streak():
  j = make()
  for _ in range(5):
    j._back_off()
  j._joined_at = j.test.clock()
  j.test.clock.t += J.STABLE_SECONDS + 1
  j._back_off()
  assert j._failures == 1


def test_join_waits_out_the_backoff():
  j = make()
  j._back_off()
  assert j._join_once() == pytest.approx(1.0)
  j.test.clock.t += 1.5
  assert j._join_once() is None and j.big_model_state == 'ready'


def test_a_phone_that_is_not_there_is_retried_not_raised():
  def refuse():
    raise OSError('no route')
  j = make(connect=refuse)
  assert j._join_once() == J.REJOIN_DELAY and j._rejoin.is_set()
  assert any('not joined yet' in l for l in j.test.log.lines)


def test_a_build_failure_stays_small_and_closes_the_link_through_the_join_thread():
  def bad(c, i):
    raise RuntimeError('metadata mismatch')
  j = make(build=bad)
  join(j)
  warm(j)
  assert frame(j) == 'small' and j.big_model_state == 'retrying'
  j._rejoin_at = 0
  j._join_once()
  assert j.test.clients[0].left[0][0] == P.LEAVE_LOST
  assert j.test.clients[0].closed == 1


def test_a_demoted_link_is_told_why_and_closed():
  j = make(build=lambda c, i: Big(c, fail=True))
  join(j)
  warm(j)
  frame(j)
  j._rejoin_at = 0
  j._join_once()
  first = j.test.clients[0]
  assert first.left and first.left[0][0] == P.LEAVE_LOST and first.closed == 1


def test_keepalive_ping_failure_discards_the_link_and_backs_off():
  c = Client(ping_error=OSError('dead'))
  j = make(connect=lambda: (c, object()))
  join(j)
  j._rejoin.clear()
  # one keepalive pass, as the thread runs it
  j._rejoin.wait = lambda timeout=None: False
  j._keep_alive()
  assert j.big_model_state == 'retrying' and c.closed == 1 and j._failures == 1


def test_close_says_stopped_and_closes_every_link():
  j = make()
  join(j)
  c = j.test.clients[0]
  j.close()
  assert c.left[0][0] == P.LEAVE_STOPPED and c.closed == 1 and j.big_model_state == 'unavailable'
  j.close()
  assert c.closed == 1                          # closing twice says nothing twice


def test_closing_a_driving_phone_leaves_with_the_trip_summary():
  holder = {}

  def build(c, i):
    big = Big(c)
    big.trips = type('Tr', (), {'summary': lambda self: {'frames': 9, 'held': 1}})()
    holder['big'] = big
    return big
  j = make(build=build)
  join(j)
  warm(j)
  frame(j)
  j.close()
  reason, stats = j.test.clients[0].left[0]
  assert reason == P.LEAVE_STOPPED and stats['frames'] == 9


def test_in_control_is_asked_before_every_frame_and_lands_on_the_small_model():
  j = make(in_control=True)
  frame(j)
  assert j.test.small.in_control is True
  j.test.ctl[0] = False
  frame(j)
  assert j.test.small.in_control is False


def test_reads_follow_the_model_that_drives():
  j = make()
  join(j)
  j.lat_delay = 0.2
  assert j.test.small.lat_delay == 0.2
  warm(j)
  frame(j)
  assert j._big.lat_delay == 0.2                # set from the small model at the swap
  j.lat_delay = 0.3
  assert j._big.lat_delay == 0.3 and j.test.small.lat_delay == 0.3
  with pytest.raises(AttributeError):
    j._nothing
