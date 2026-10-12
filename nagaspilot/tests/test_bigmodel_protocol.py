import struct
import zlib

import pytest

from nagaspilot.bigmodel import protocol as P


def test_header_is_20_bytes_and_golden():
  raw = P.pack(P.PING, 7)
  assert len(raw) == P.HEADER_SIZE == 20
  head = struct.pack('<4sBBHII', b'NPBM', 1, P.PING, 0, 7, 0)
  assert raw == head + struct.pack('<I', zlib.crc32(head))
  assert P.parse_header(raw) == (P.PING, 0, 7, 0)


@pytest.mark.parametrize('mutate', [
  lambda b: b'XXXX' + b[4:],
  lambda b: b[:4] + bytes([2]) + b[5:],                 # version
  lambda b: b[:-1] + bytes([b[-1] ^ 1]),                # crc
  lambda b: b[:5] + bytes([99]) + b[6:],                # type (crc now wrong too)
])
def test_a_damaged_header_is_a_broken_stream(mutate):
  with pytest.raises(P.ProtocolError):
    P.parse_header(mutate(P.pack(P.PING, 1)))


def test_an_absurd_length_is_refused():
  head = struct.pack('<4sBBHII', b'NPBM', 1, P.INFER_REQ, 0, 1, P.MAX_PAYLOAD + 1)
  with pytest.raises(P.ProtocolError):
    P.parse_header(head + struct.pack('<I', zlib.crc32(head)))


def test_infer_req_round_trips():
  frame = bytes(range(256)) * (P.FRAME_BYTES // 256)
  raw = P.pack_infer_req(3, 99, P.RESET, [0, 1, 0, 0, 0, 0, 0, 0], [1, 0], frame)
  mtype, flags, seq, length = P.parse_header(raw[:P.HEADER_SIZE])
  assert (mtype, seq) == (P.INFER_REQ, 3) and length == len(raw) - P.HEADER_SIZE
  req = P.unpack_infer_req(raw[P.HEADER_SIZE:])
  assert (req.frame_id, req.flags) == (99, P.RESET)
  assert req.desire == (0, 1, 0, 0, 0, 0, 0, 0) and req.traffic == (1, 0)
  assert bytes(req.frame) == frame


def test_infer_req_wrong_sizes_are_refused():
  with pytest.raises(ValueError):
    P.pack_infer_req(1, 1, 0, [0] * 8, [0, 0], b'short')
  with pytest.raises(P.ProtocolError):
    P.unpack_infer_req(b'\0' * 10)


def test_infer_resp_round_trips_and_rejects_ragged_floats():
  raw = P.pack_infer_resp(5, 11, P.OK, 1234, b'\0\0\x80\x3f' * 3)
  resp = P.unpack_infer_resp(raw[P.HEADER_SIZE:])
  assert (resp.frame_id, resp.status, resp.server_us) == (11, P.OK, 1234)
  assert bytes(resp.out) == b'\0\0\x80\x3f' * 3
  with pytest.raises(P.ProtocolError):
    P.unpack_infer_resp(raw[P.HEADER_SIZE:-1])


def test_hello_checks_contract_and_geometry():
  P.check_hello(P.decode_json(P.hello_payload()))
  with pytest.raises(P.ProtocolError):
    P.check_hello({'contract': 'other', 'frame': list(P.FRAME_SHAPE), 'desire': 8, 'traffic': 2})
  with pytest.raises(P.ProtocolError):
    P.check_hello({'contract': P.CONTRACT, 'frame': [2, 6, 64, 256], 'desire': 8, 'traffic': 2})


def test_json_must_be_an_object():
  with pytest.raises(P.ProtocolError):
    P.decode_json(b'[1]')
  with pytest.raises(P.ProtocolError):
    P.decode_json(b'\xff')
