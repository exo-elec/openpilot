"""NPBM v1: the wire format between the comma and the phone's model server (docs/NAVPILOT_BIGMODEL.md).

Pure: standard library and numpy-free, so the manager-side owner and the tests import it for nothing. Little endian.
Header (20 bytes): magic, version u8, type u8, flags u16, seq u32, length u32, crc32 of the first 16 bytes.
"""
from __future__ import annotations

import json
import struct
import zlib
from typing import NamedTuple

MAGIC = b'NPBM'
VERSION = 1
HEADER = struct.Struct('<4sBBHII')   # 16 bytes, then the crc
HEADER_SIZE = HEADER.size + 4
MAX_PAYLOAD = 4 * 1024 * 1024        # far above a frame (393 KB) or a reply (~75 KB): a bigger length is a broken stream

# message types
HELLO, HELLO_ACK, INFER_REQ, INFER_RESP, PING, PONG, LEAVE, ERROR = range(1, 9)
TYPE_NAMES = {HELLO: 'HELLO', HELLO_ACK: 'HELLO_ACK', INFER_REQ: 'INFER_REQ', INFER_RESP: 'INFER_RESP', PING: 'PING',
              PONG: 'PONG', LEAVE: 'LEAVE', ERROR: 'ERROR'}

# INFER_REQ req_flags
RESET = 1

# INFER_RESP status
OK, NOT_FINITE, NOT_READY, INTERNAL = 0, 1, 2, 3

# what a comma and a server must agree on before the first frame. The comma lists the contracts it can use, the server picks one
# (the first of the comma's list that it supports) and says which in HELLO_ACK.
CONTRACT_SPLIT = 'ngp-v0.10-split'       # NGP10's vision + policy graphs; the comma knows the layout
CONTRACT_QUEUED = 'ngp-spec-queued'      # one combined graph, history as stacked inputs; the phone announces a ModelSpec and the comma allow-lists it
CONTRACT = CONTRACT_SPLIT                # the default, and what the earlier single-contract hello meant
CONTRACTS = (CONTRACT_SPLIT, CONTRACT_QUEUED)
FRAME_SHAPE = (2, 6, 128, 256)       # narrow then wide camera, the newest warped frame of each
FRAME_BYTES = 2 * 6 * 128 * 256      # 393,216
DESIRE_LEN = 8
TRAFFIC_LEN = 2
FPS = 20
DEFAULT_PORT = 5600
COMMA_ADDR = '192.168.60.1'

# why the comma leaves (LEAVE.reason)
LEAVE_LOST, LEAVE_BEHIND, LEAVE_STOPPED = 'lost', 'behind', 'stopped'

REQ_FIXED = struct.Struct(f'<II{DESIRE_LEN}f{TRAFFIC_LEN}f')
RESP_FIXED = struct.Struct('<III')


class ProtocolError(Exception):
  """A broken stream: bad magic, version, crc or length. The connection cannot be trusted past it."""


class Message(NamedTuple):
  type: int
  seq: int
  flags: int
  payload: bytes


def pack(mtype: int, seq: int = 0, payload: bytes = b'', flags: int = 0) -> bytes:
  head = HEADER.pack(MAGIC, VERSION, mtype, flags, seq & 0xFFFFFFFF, len(payload))
  return head + struct.pack('<I', zlib.crc32(head)) + payload


def parse_header(raw: bytes) -> tuple[int, int, int, int]:
  """(type, flags, seq, length) of a header, or ProtocolError."""
  if len(raw) != HEADER_SIZE:
    raise ProtocolError(f'header of {len(raw)} bytes')
  head = raw[:HEADER.size]
  magic, version, mtype, flags, seq, length = HEADER.unpack(head)
  if magic != MAGIC:
    raise ProtocolError('bad magic')
  if version != VERSION:
    raise ProtocolError(f'version {version}, this side speaks {VERSION}')
  if struct.unpack('<I', raw[HEADER.size:])[0] != zlib.crc32(head):
    raise ProtocolError('header crc')
  if mtype not in TYPE_NAMES:
    raise ProtocolError(f'type {mtype}')
  if length > MAX_PAYLOAD:
    raise ProtocolError(f'payload of {length} bytes')
  return mtype, flags, seq, length


def encode_json(obj: dict) -> bytes:
  return json.dumps(obj, separators=(',', ':')).encode()


def decode_json(payload: bytes) -> dict:
  try:
    obj = json.loads(payload.decode())
  except (UnicodeDecodeError, ValueError) as e:
    raise ProtocolError(f'bad json: {e}') from e
  if not isinstance(obj, dict):
    raise ProtocolError('json payload is not an object')
  return obj


def hello_payload(contracts: tuple[str, ...] = (CONTRACT_SPLIT,)) -> bytes:
  return encode_json({'contracts': list(contracts), 'frame': list(FRAME_SHAPE), 'desire': DESIRE_LEN, 'traffic': TRAFFIC_LEN, 'fps': FPS})


def check_hello(obj: dict, supported: tuple[str, ...] = (CONTRACT_SPLIT,)) -> str:
  """Server side: the contract to run, the first of the comma's list that this server supports. Refuses a comma that offers none
  of them or another geometry (the warped frame is the same for every contract)."""
  offered = obj.get('contracts')
  if not isinstance(offered, list) or not all(isinstance(c, str) for c in offered):
    raise ProtocolError('HELLO has no contract list')
  if tuple(obj.get('frame') or ()) != FRAME_SHAPE or obj.get('desire') != DESIRE_LEN or obj.get('traffic') != TRAFFIC_LEN:
    raise ProtocolError('frame geometry differs')
  for c in offered:
    if c in supported:
      return c
  raise ProtocolError(f"contracts {offered!r}, this server runs {list(supported)!r}")


def pack_infer_req(seq: int, frame_id: int, flags: int, desire, traffic, frame) -> bytes:
  """`desire` (8) and `traffic` (2) are float sequences; `frame` is FRAME_BYTES of uint8 (bytes-like)."""
  fixed = REQ_FIXED.pack(frame_id & 0xFFFFFFFF, flags, *[float(x) for x in desire], *[float(x) for x in traffic])
  if len(frame) != FRAME_BYTES:
    raise ValueError(f'frame of {len(frame)} bytes, expected {FRAME_BYTES}')
  return pack(INFER_REQ, seq, fixed + bytes(frame))


def pack_infer_req_packed(seq: int, frame_id: int, flags: int, packed, frame) -> bytes:
  """INFER_REQ for any contract: frame id, flags, the contract's packed float32s, then the warped frame (any length: the contract says)."""
  head = struct.pack(f'<II{len(packed)}f', frame_id & 0xFFFFFFFF, flags, *[float(x) for x in packed])
  return pack(INFER_REQ, seq, head + bytes(frame))


class PackedReq(NamedTuple):
  frame_id: int
  flags: int
  packed: tuple[float, ...]
  frame: memoryview


def unpack_infer_req_packed(payload: bytes, packed_nelem: int, frame_nbytes: int) -> PackedReq:
  size = 8 + packed_nelem * 4 + frame_nbytes
  if len(payload) != size:
    raise ProtocolError(f'INFER_REQ of {len(payload)} bytes, expected {size}')
  vals = struct.unpack_from(f'<II{packed_nelem}f', payload)
  return PackedReq(vals[0], vals[1], vals[2:], memoryview(payload)[8 + packed_nelem * 4:])


class InferReq(NamedTuple):
  frame_id: int
  flags: int
  desire: tuple[float, ...]
  traffic: tuple[float, ...]
  frame: memoryview


def unpack_infer_req(payload: bytes) -> InferReq:
  if len(payload) != REQ_FIXED.size + FRAME_BYTES:
    raise ProtocolError(f'INFER_REQ of {len(payload)} bytes')
  vals = REQ_FIXED.unpack_from(payload)
  return InferReq(vals[0], vals[1], vals[2:2 + DESIRE_LEN], vals[2 + DESIRE_LEN:], memoryview(payload)[REQ_FIXED.size:])


def pack_infer_resp(seq: int, frame_id: int, status: int, server_us: int, out=b'') -> bytes:
  """`out` is float32 little-endian bytes (numpy tobytes), empty unless status is OK."""
  return pack(INFER_RESP, seq, RESP_FIXED.pack(frame_id & 0xFFFFFFFF, status, min(server_us, 0xFFFFFFFF)) + bytes(out))


class InferResp(NamedTuple):
  frame_id: int
  status: int
  server_us: int
  out: memoryview   # float32 bytes


def unpack_infer_resp(payload: bytes) -> InferResp:
  if len(payload) < RESP_FIXED.size or (len(payload) - RESP_FIXED.size) % 4:
    raise ProtocolError(f'INFER_RESP of {len(payload)} bytes')
  frame_id, status, us = RESP_FIXED.unpack_from(payload)
  return InferResp(frame_id, status, us, memoryview(payload)[RESP_FIXED.size:])
