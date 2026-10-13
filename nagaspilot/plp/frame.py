"""PLP v5 framing: a 16-byte header, a payload, a CRC-32C over both.

    magic u16 'PL' | version u8 | kind u8 | flags u8 | channel u8 | service u8 | message u8 | encoding u8 | reserved u8 | request id u16 | payload length u32
    payload (length bytes)
    CRC-32C u32 over header and payload

All integers big-endian. A message larger than the link can carry is split into frames of the same header with the MORE flag on all but the last; frames of different
channels may interleave, frames of one channel's message may not. The decoder never raises: a bad frame is counted and the decoder resyncs on the next magic.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = 0x504C
VERSION = 5
HEADER = struct.Struct('>HBBBBBBBBHI')
HEADER_SIZE = HEADER.size
TRAILER_SIZE = 4
MAX_FRAME_PAYLOAD = 1 << 20       # a decoder never accepts a bigger frame, whatever was negotiated

FLAG_MORE = 0x01                 # more fragments of this message follow
FLAG_CRITICAL = 0x02             # a receiver that does not know this message must answer with an error, not drop it
FLAG_ACK_REQ = 0x04              # reserved for the map channel
KNOWN_FLAGS = FLAG_MORE | FLAG_CRITICAL | FLAG_ACK_REQ

CH_CONTROL, CH_REALTIME, CH_INTERACTIVE, CH_BULK = 0, 1, 2, 3
ENC_RAW, ENC_JSON, ENC_PROTOBUF, ENC_CBOR = 0, 1, 2, 3


def _make_crc32c_table() -> tuple[int, ...]:
  table = []
  for i in range(256):
    c = i
    for _ in range(8):
      c = (c >> 1) ^ 0x82F63B78 if c & 1 else c >> 1
    table.append(c)
  return tuple(table)


_CRC_TABLE = _make_crc32c_table()


def crc32c(data: bytes, crc: int = 0) -> int:
  crc ^= 0xFFFFFFFF
  for b in data:
    crc = _CRC_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
  return crc ^ 0xFFFFFFFF


@dataclass(frozen=True)
class Frame:
  kind: int
  service: int
  message: int
  payload: bytes = b''
  channel: int = CH_INTERACTIVE
  encoding: int = ENC_JSON
  request_id: int = 0
  flags: int = 0

  @property
  def more(self) -> bool:
    return bool(self.flags & FLAG_MORE)

  @property
  def critical(self) -> bool:
    return bool(self.flags & FLAG_CRITICAL)

  def encode(self) -> bytes:
    if len(self.payload) > MAX_FRAME_PAYLOAD:
      raise ValueError('frame payload too large')
    head = HEADER.pack(MAGIC, VERSION, self.kind, self.flags, self.channel, self.service, self.message, self.encoding, 0, self.request_id, len(self.payload))
    body = head + self.payload
    return body + struct.pack('>I', crc32c(body))


class FrameDecoder:
  """Feed bytes as they arrive; `feed` returns the complete, valid frames. Counters say what was thrown away."""

  def __init__(self) -> None:
    self._buf = bytearray()
    self.bad_crc = 0
    self.bad_header = 0
    self.skipped_bytes = 0

  def feed(self, data: bytes) -> list[Frame]:
    self._buf += data
    out: list[Frame] = []
    while True:
      i = self._buf.find(b'PL')
      if i < 0:
        keep = 1 if self._buf[-1:] == b'P' else 0     # a magic may be split across two feeds
        self.skipped_bytes += len(self._buf) - keep
        del self._buf[:len(self._buf) - keep]
        return out
      if i:
        self.skipped_bytes += i
        del self._buf[:i]
      if len(self._buf) < HEADER_SIZE:
        return out
      _, ver, kind, flags, channel, service, message, encoding, rsv, rid, length = HEADER.unpack_from(self._buf)
      if ver != VERSION or length > MAX_FRAME_PAYLOAD or flags & ~KNOWN_FLAGS or rsv or kind > 3:
        self.bad_header += 1
        del self._buf[:1]
        continue
      total = HEADER_SIZE + length + TRAILER_SIZE
      if len(self._buf) < total:
        return out
      body = bytes(self._buf[:HEADER_SIZE + length])
      (crc,) = struct.unpack_from('>I', self._buf, HEADER_SIZE + length)
      if crc != crc32c(body):
        self.bad_crc += 1
        del self._buf[:1]
        continue
      out.append(Frame(kind, service, message, body[HEADER_SIZE:], channel, encoding, rid, flags))
      del self._buf[:total]
