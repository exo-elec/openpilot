import json

import pytest

from nagaspilot.plp import channel as C
from nagaspilot.plp import dispatch, frame as F, hello as H, registry as R


def roundtrip(frames: bytes):
  d = F.FrameDecoder()
  return d.feed(frames), d


def test_crc32c_check_value():
  assert F.crc32c(b'123456789') == 0xE3069283


def test_frame_roundtrip_and_header_size():
  f = F.Frame(R.KIND_REQUEST, 3, 3, b'{"a":1}', F.CH_INTERACTIVE, F.ENC_JSON, 7)
  raw = f.encode()
  assert len(raw) == F.HEADER_SIZE + 7 + 4 and F.HEADER_SIZE == 16
  out, d = roundtrip(raw)
  assert out == [f] and d.bad_crc == 0


def test_split_across_feeds_including_the_magic():
  raw = F.Frame(0, 2, 1, b'x').encode()
  d = F.FrameDecoder()
  got = []
  for i in range(len(raw)):
    got += d.feed(raw[i:i + 1])
  assert len(got) == 1


def test_garbage_and_corrupt_frames_are_skipped_then_decoding_resyncs():
  good = F.Frame(0, 2, 1, b'ok').encode()
  bad = bytearray(F.Frame(0, 2, 1, b'no').encode())
  bad[-5] ^= 0xFF
  d = F.FrameDecoder()
  out = d.feed(b'\x00\x01junk' + bytes(bad) + good)
  assert [f.payload for f in out] == [b'ok'] and d.bad_crc == 1 and d.skipped_bytes > 0


def test_header_is_covered_by_the_crc():
  raw = bytearray(F.Frame(0, 2, 1, b'ok').encode())
  raw[6] ^= 0x01                                  # service
  assert roundtrip(bytes(raw))[0] == []


def test_unknown_flags_and_oversize_are_rejected():
  raw = bytearray(F.Frame(0, 2, 1, b'ok').encode())
  raw[4] = 0x80
  assert roundtrip(bytes(raw))[0] == []
  with pytest.raises(ValueError):
    F.Frame(0, 2, 1, bytes(F.MAX_FRAME_PAYLOAD + 1)).encode()


def drain(o: C.Outbox):
  d, r, out = F.FrameDecoder(), C.Reassembler(), []
  while (b := o.next_frame()):
    for fr in d.feed(b):
      if (m := r.push(fr)):
        out.append(m)
  return out


def test_fragmentation_roundtrip():
  o = C.Outbox(7)
  payload = bytes(range(256)) * 3
  o.send(C.Message(R.KIND_NOTIFY, 8, 2, payload, channel=F.CH_BULK))
  (m,) = drain(o)
  assert m.payload == payload and m.service == 8 and m.message == 2


def test_urgent_channel_overtakes_a_bulk_message_at_a_frame_boundary():
  o = C.Outbox(4)
  o.send(C.Message(R.KIND_NOTIFY, 8, 2, b'B' * 40, channel=3))
  first = o.next_frame()                           # bulk starts
  o.send(C.Message(R.KIND_NOTIFY, 2, 1, b'speed', channel=1))
  d = F.FrameDecoder()
  order = [f.channel for f in d.feed(first)]
  while (b := o.next_frame()):
    order += [f.channel for f in d.feed(b)]
  assert order[0] == 3 and order[1] == 1           # the urgent frame is the very next one
  assert order.count(3) == 10


def test_bulk_is_not_starved():
  o = C.Outbox(1)
  o.send(C.Message(R.KIND_NOTIFY, 8, 2, b'BB', channel=3))
  for _ in range(40):
    o.send(C.Message(R.KIND_NOTIFY, 2, 1, b'v', channel=1))
  chans = []
  d = F.FrameDecoder()
  while (b := o.next_frame()):
    chans += [f.channel for f in d.feed(b)]
  assert chans.index(3) <= C.STARVATION_LIMIT + 1


def test_interleaved_channels_reassemble_independently():
  r = C.Reassembler()
  a = [F.Frame(0, 8, 2, b'AA', 3, flags=F.FLAG_MORE), F.Frame(0, 8, 2, b'aa', 3)]
  b = [F.Frame(0, 2, 1, b'1', 1)]
  assert r.push(a[0]) is None
  assert r.push(b[0]).payload == b'1'
  assert r.push(a[1]).payload == b'AAaa'


def test_message_over_limit_and_mismatched_fragment_are_dropped():
  r = C.Reassembler(max_message=4)
  assert r.push(F.Frame(0, 8, 2, b'123', 3, flags=F.FLAG_MORE)) is None
  assert r.push(F.Frame(0, 8, 2, b'456', 3)) is None and r.dropped == 1
  r = C.Reassembler()
  r.push(F.Frame(0, 8, 2, b'x', 3, flags=F.FLAG_MORE))
  m = r.push(F.Frame(0, 2, 1, b'y', 3))             # a different message began on the same channel
  assert m.payload == b'y' and r.dropped == 1


def test_registry_has_unique_keys_and_every_v4_type_once():
  codes = [c for d in R.MESSAGES.values() for c, _ in d.v4]
  assert len(codes) == len(set(codes))
  assert set(codes) | set(R.V4_SPECIAL.values()) >= {0x00, 0x01, 0x10, 0x20, 0x2E, 0x40, 0x41, 0x50, 0x60, 0x64, 0x70, 0x0602, 0x0610, 0x0620, 0x0621}


def test_v4_mapping_both_ways():
  d, kind = R.v4_to_v5(0x0621)
  assert d.full_name == 'locate.map_horizon' and kind == R.KIND_NOTIFY
  assert R.v5_to_v4(d.service, d.message, kind) == 0x0621
  d, kind = R.v4_to_v5(0x34)
  assert d.full_name == 'vehicle.info' and kind == R.KIND_RESPONSE
  assert R.v5_to_v4(d.service, d.message, R.KIND_REQUEST) == 0x24
  assert R.v4_to_v5(0x7FFF) is None and R.v4_to_v5(0x30) is None


def test_tts_is_device_to_phone():
  assert R.BY_NAME['voice.tts'].direction == 'd2p'      # v4 listed it with the phone-to-device commands


def mk(link='usb', **kw):
  return H.Hello(services=(('locate', 1, 2), ('nav', 1, 1), ('voice', 1, 1)), link=link, **kw)


def test_hello_roundtrip_and_negotiation():
  a = mk(max_payload=60000)
  assert H.Hello.parse(a.to_json()) == a
  b = H.Hello(max_payload=500, services=(('nav', 1, 2), ('locate', 2, 3), ('auth', 1, 1)), link='ble')
  ag = H.negotiate(a, b)
  assert ag.version == 5 and ag.max_payload == 500
  assert ag.services == {'nav': 1, 'locate': 2}           # voice/auth not on both sides; locate 2 is the common top
  assert H.negotiate(H.Hello(min_version=6, max_version=7), a) is None


def test_hello_rejects_junk_and_ignores_unknown_services():
  with pytest.raises(ValueError):
    H.Hello.parse(b'{}')
  ag = H.negotiate(H.Hello(services=(('martian', 1, 1),)), H.Hello(services=(('martian', 1, 1),)))
  assert ag.services == {}


def test_unknown_messages_critical_and_requests_get_errors():
  req = C.Message(R.KIND_REQUEST, 99, 1, b'', request_id=5)
  e = dispatch.unknown_reply(req)
  assert e.kind == R.KIND_ERROR and e.request_id == 5 and json.loads(e.payload)['code'] == 'unsupported'
  assert dispatch.unknown_reply(C.Message(R.KIND_NOTIFY, 99, 1)) is None
  assert dispatch.unknown_reply(C.Message(R.KIND_NOTIFY, 99, 1, critical=True)).kind == R.KIND_ERROR


def test_golden_frames_match_the_bytes_navpilot_decodes():
  import pathlib
  g = json.loads((pathlib.Path(__file__).parent / 'fixtures' / 'plp_golden.json').read_text())
  assert F.crc32c(b'123456789') == g['crc32c_123456789']
  for c in g['frames']:
    f = F.Frame(c['kind'], c['service'], c['message'], bytes.fromhex(c['payload_hex']), c['channel'], c['encoding'], c['request_id'], c['flags'])
    assert f.encode().hex() == c['hex'], c['name']
    assert F.FrameDecoder().feed(bytes.fromhex(c['hex'])) == [f]


def test_golden_hello_json_is_byte_identical():
  import pathlib
  g = json.loads((pathlib.Path(__file__).parent / 'fixtures' / 'plp_golden.json').read_text())
  h = H.Hello(max_payload=512, max_message=65536, services=(('locate', 1, 2), ('nav', 1, 1)), link='ble')
  assert h.to_json().decode() == g['hello']['json']
