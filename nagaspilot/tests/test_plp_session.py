import json
import pathlib
import socket
import time

from nagaspilot.localization.map_horizon import MapHorizon
from nagaspilot.plp import frame as F, registry as R
from nagaspilot.plp.cable import CablePLP
from nagaspilot.plp.channel import Message
from nagaspilot.plp.hello import Hello
from nagaspilot.plp.locate import LocateSink
from nagaspilot.plp.session import PlpSession

FIXTURE = pathlib.Path(__file__).parent / 'fixtures' / 'map_horizon_navpilot.json'
SERVICES = (('core', 1, 1), ('locate', 1, 1))


def pair(max_payload=64):
  """A phone session and a device session wired back to back, in memory."""
  box: dict[str, PlpSession] = {}
  phone = PlpSession(Hello(services=SERVICES, max_payload=max_payload, link='usb'), lambda b: box['device'].feed(b), role='phone')
  device = PlpSession(Hello(services=SERVICES, max_payload=max_payload, link='usb'), lambda b: box['phone'].feed(b), role='device')
  box['phone'], box['device'] = phone, device
  return phone, device


def locate_msg(name, payload, kind=R.KIND_NOTIFY):
  d = R.BY_NAME[name]
  return Message(kind, d.service, d.message, payload, 1)


def test_hello_then_horizon_through_small_frames():
  phone, device = pair(max_payload=64)
  sink = LocateSink(clock=lambda: 0.0)
  sink.attach(device)
  phone.hello_request()
  assert phone.agreed is not None and device.agreed is not None and phone.agreed.max_payload == 64
  phone.send(locate_msg('locate.map_horizon', FIXTURE.read_bytes()))      # ~2.7 KB in 64-byte frames
  assert sink.horizons_taken == 1 and sink.tracker.current.route_id == json.loads(FIXTURE.read_text())['route_id']


def test_nothing_is_accepted_before_the_hello():
  phone, device = pair()
  sink = LocateSink()
  sink.attach(device)
  phone.send(Message(R.KIND_REQUEST, 8, 1, b'{}', 1, request_id=3))        # a request before any hello
  assert sink.latest_fix is None
  phone.send(locate_msg('locate.map_horizon', FIXTURE.read_bytes()))      # a notify before any hello is dropped silently
  assert sink.horizons_taken == 0


def test_ping_is_answered_with_the_same_request_id():
  phone, device = pair()
  replies = []
  phone.on('core.ping', replies.append)
  phone.hello_request()
  ping = R.BY_NAME['core.ping']
  phone.send(Message(R.KIND_REQUEST, ping.service, ping.message, b'x', 0, request_id=9))
  # the phone's own session routes the response to its handler
  assert [(m.kind, m.request_id, m.payload) for m in replies] == [(R.KIND_RESPONSE, 9, b'x')]


def test_bad_fix_does_not_kill_the_session_and_a_request_gets_an_error():
  phone, device = pair()
  sink = LocateSink()
  sink.attach(device)
  errors = []
  phone.on('locate.gnss_fix', errors.append)
  phone.hello_request()
  phone.send(locate_msg('locate.gnss_fix', b'not json', R.KIND_REQUEST))
  assert sink.latest_fix is None
  good = json.dumps({'t_ms': 1000, 'lat': 13.75, 'lon': 100.5, 'h_acc': 3.0}).encode()
  phone.send(locate_msg('locate.gnss_fix', good))
  assert sink.latest_fix is not None and abs(sink.latest_fix.lat - 13.75) < 1e-9


def test_a_message_going_the_wrong_way_is_refused():
  phone, device = pair()
  phone.hello_request()
  tts = R.BY_NAME['voice.tts']                      # device to phone only
  answers = []
  phone.on('voice.tts', answers.append)
  phone.send(Message(R.KIND_REQUEST, tts.service, tts.message, b'{}', 2, request_id=4))
  assert [(m.kind, json.loads(m.payload)['code']) for m in answers] == [(R.KIND_ERROR, 'wrong_direction')]     # refused, not run as a command


def test_cable_server_serves_a_phone_over_tcp_and_refuses_other_networks():
  sink = LocateSink()
  srv = CablePLP(Hello(services=SERVICES, link='usb'), sink.attach, bind_addr='127.0.0.1', port=0, allowed_prefix='127.')
  srv.start()
  try:
    for _ in range(100):
      if srv.bound_port:
        break
      time.sleep(0.02)
    s = socket.create_connection(('127.0.0.1', srv.bound_port))
    got = F.FrameDecoder()
    s.sendall(F.Frame(R.KIND_REQUEST, 0, 1, Hello(services=SERVICES, link='usb').to_json(), 0, F.ENC_JSON, 1).encode())
    s.settimeout(2)
    frames = []
    while not frames:
      frames = got.feed(s.recv(4096))
    assert frames[0].kind == R.KIND_RESPONSE and Hello.parse(frames[0].payload).link == 'usb'
    s.sendall(F.Frame(R.KIND_NOTIFY, 8, 2, FIXTURE.read_bytes(), 1).encode())
    for _ in range(100):
      if sink.horizons_taken:
        break
      time.sleep(0.02)
    assert sink.horizons_taken == 1
    s.close()
  finally:
    srv.stop()
  other = CablePLP(Hello(), sink.attach, bind_addr='127.0.0.1', port=0, allowed_prefix='192.168.60.')
  other.start()
  try:
    for _ in range(100):
      if other.bound_port:
        break
      time.sleep(0.02)
    c = socket.create_connection(('127.0.0.1', other.bound_port))
    c.settimeout(2)
    assert c.recv(10) == b''                        # refused: closed without a word
  finally:
    other.stop()


def test_fixture_still_parses_as_a_horizon():
  assert MapHorizon.parse(FIXTURE.read_bytes(), 0.0).length_m > 0
