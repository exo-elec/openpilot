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

# what a comma and a server must agree on before the first frame
CONTRACT = 'ngp-v0.10-split'
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


def hello_payload() -> bytes:
  return encode_json({'contract': CONTRACT, 'frame': list(FRAME_SHAPE), 'desire': DESIRE_LEN, 'traffic': TRAFFIC_LEN, 'fps': FPS})


def check_hello(obj: dict) -> None:
  """Server side: refuse a comma that speaks another contract or geometry."""
  if obj.get('contract') != CONTRACT:
    raise ProtocolError(f"contract {obj.get('contract')!r}, this server runs {CONTRACT!r}")
  if tuple(obj.get('frame') or ()) != FRAME_SHAPE or obj.get('desire') != DESIRE_LEN or obj.get('traffic') != TRAFFIC_LEN:
    raise ProtocolError('frame geometry differs')


def pack_infer_req(seq: int, frame_id: int, flags: int, desire, traffic, frame) -> bytes:
  """`desire` (8) and `traffic` (2) are float sequences; `frame` is FRAME_BYTES of uint8 (bytes-like)."""
  fixed = REQ_FIXED.pack(frame_id & 0xFFFFFFFF, flags, *[float(x) for x in desire], *[float(x) for x in traffic])
  if len(frame) != FRAME_BYTES:
    raise ValueError(f'frame of {len(frame)} bytes, expected {FRAME_BYTES}')
  return pack(INFER_REQ, seq, fixed + bytes(frame))


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
