import threading
import time

import pytest

from nagaspilot.plp import frame as F, ble_stream as B, registry as R
from nagaspilot.plp.channel import Message
from nagaspilot.plp.hello import Hello
from nagaspilot.plp.session import PlpSession


def test_split_respects_mtu_minus_att_overhead():
  assert B.usable_payload(185) == 182
  parts = B.split(bytes(range(256)) * 2, 100)
  assert all(len(p) <= 97 for p in parts) and b''.join(parts) == bytes(range(256)) * 2
  with pytest.raises(ValueError):
    B.split(b'x', 3)


def test_usable_threshold():
  s = B.BleStream(lambda c: True)
  assert not s.plp_usable()            # the default MTU 23 is NCP territory
  s.set_mtu(185)
  assert s.plp_usable()


def run_until_idle(s, got, expect_chunks):
  s.start()
  for _ in range(500):
    if len(got) >= expect_chunks:
      break
    time.sleep(0.01)
  s.stop()


def test_two_frames_never_interleave_and_decode_in_order():
  got = []
  s = B.BleStream(lambda c: got.append(c) or True)
  s.set_mtu(70)
  frames = [F.Frame(R.KIND_NOTIFY, 8, 2, bytes([i]) * 150, F.CH_BULK).encode() for i in (1, 2)]
  for fr in frames:
    s.send(fr)
  run_until_idle(s, got, sum(len(B.split(f, 70)) for f in frames))
  assert b''.join(got) == frames[0] + frames[1]
  d = F.FrameDecoder()
  out = []
  for c in got:
    out += d.feed(c)
  assert [f.payload[0] for f in out] == [1, 2]


def test_a_full_stack_queue_is_waited_out_then_the_stream_goes_on():
  got, refusals = [], [3]

  def notify(c):
    if refusals[0] > 0:
      refusals[0] -= 1
      return False
    got.append(c)
    return True

  s = B.BleStream(notify, sleep=lambda t: None)
  s.set_mtu(185)
  s.send(b'abc')
  run_until_idle(s, got, 1)
  assert got == [b'abc']


def test_a_link_blocked_too_long_is_ended():
  dead = threading.Event()
  t = [0.0]

  def clock():
    t[0] += 1.0
    return t[0]

  s = B.BleStream(lambda c: False, on_dead=dead.set, clock=clock, sleep=lambda x: None)
  s.send(b'abc')
  s.start()
  assert dead.wait(2)
  s.stop()
  assert s.dropped_frames == 1


def test_a_session_runs_over_the_ble_stream_with_small_mtu():
  """Phone and device sessions joined by two BLE streams; the horizon fixture goes through a 70-byte MTU."""
  import pathlib
  from nagaspilot.plp.locate import LocateSink
  fixture = (pathlib.Path(__file__).parent / 'fixtures' / 'map_horizon_navpilot.json').read_bytes()
  sessions = {}
  d2p = B.BleStream(lambda c: sessions['phone'].feed(c) or True)
  p2d = B.BleStream(lambda c: sessions['device'].feed(c) or True)
  for s in (d2p, p2d):
    s.set_mtu(70)
    s.start()
  svc = (('core', 1, 1), ('locate', 1, 1))
  sessions['phone'] = PlpSession(Hello(services=svc, max_payload=512, link='ble'), p2d.send, role='phone')
  sessions['device'] = PlpSession(Hello(services=svc, max_payload=512, link='ble'), d2p.send, role='device')
  sink = LocateSink(clock=lambda: 0.0)
  sink.attach(sessions['device'])
  sessions['phone'].hello_request()
  d = R.BY_NAME['locate.map_horizon']
  for _ in range(200):
    if sessions['phone'].agreed:
      break
    time.sleep(0.01)
  sessions['phone'].send(Message(R.KIND_NOTIFY, d.service, d.message, fixture, F.CH_BULK))
  for _ in range(300):
    if sink.horizons_taken:
      break
    time.sleep(0.01)
  for s in (d2p, p2d):
    s.stop()
  assert sink.horizons_taken == 1
