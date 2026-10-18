"""PLP v5 framing: a 16-byte header, its CRC-32C, the payload, and (on links that need it) a CRC-32C of the payload.

    magic u16 'PL' | version u8 | kind u8 | flags u8 | channel u8 | service u8 | message u8 | encoding u8 | reserved u8 | request id u16 | payload length u32
    header CRC-32C u32           (over the 16 header bytes: a corrupt length is caught before any payload is waited for)
    payload (length bytes)
    payload CRC-32C u32          (absent when the NO_PAYLOAD_CRC flag is set)

All integers big-endian. The header CRC follows the design of Jetlink's link header and our NPBM, which check the header and leave the payload to USB and TCP; the payload CRC
is kept for BLE, where a dropped notification can shift a frame boundary under an intact header. A message larger than the link can carry is split into frames of the same
header with the MORE flag on all but the last; frames of different channels may interleave, frames of one channel's message may not. The decoder never raises: a bad frame is
counted and the decoder resyncs on the next magic.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = 0x504C
VERSION = 5
HEADER = struct.Struct('>HBBBBBBBBHI')
HEADER_SIZE = HEADER.size
HEADER_CRC_SIZE = 4
PAYLOAD_CRC_SIZE = 4
MAX_FRAME_PAYLOAD = 1 << 20       # a decoder never accepts a bigger frame, whatever was negotiated

FLAG_MORE = 0x01                 # more fragments of this message follow
FLAG_CRITICAL = 0x02             # a receiver that does not know this message must answer with an error, not drop it
FLAG_ACK_REQ = 0x04              # reserved for the map channel
FLAG_NO_PAYLOAD_CRC = 0x08       # the payload CRC is absent (the receiver said its link does not need it)
KNOWN_FLAGS = FLAG_MORE | FLAG_CRITICAL | FLAG_ACK_REQ | FLAG_NO_PAYLOAD_CRC

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

  @property
  def payload_crc(self) -> bool:
    return not self.flags & FLAG_NO_PAYLOAD_CRC

  def encode(self) -> bytes:
    if len(self.payload) > MAX_FRAME_PAYLOAD:
      raise ValueError('frame payload too large')
    head = HEADER.pack(MAGIC, VERSION, self.kind, self.flags, self.channel, self.service, self.message, self.encoding, 0, self.request_id, len(self.payload))
    out = head + struct.pack('>I', crc32c(head)) + self.payload
    if self.payload_crc:
      out += struct.pack('>I', crc32c(self.payload))
    return out


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
      if len(self._buf) < HEADER_SIZE + HEADER_CRC_SIZE:
        return out
      (hcrc,) = struct.unpack_from('>I', self._buf, HEADER_SIZE)
      if hcrc != crc32c(bytes(self._buf[:HEADER_SIZE])):
        self.bad_header += 1
        del self._buf[:1]
        continue
      has_pcrc = not flags & FLAG_NO_PAYLOAD_CRC
      start = HEADER_SIZE + HEADER_CRC_SIZE
      total = start + length + (PAYLOAD_CRC_SIZE if has_pcrc else 0)
      if len(self._buf) < total:
        return out
      payload = bytes(self._buf[start:start + length])
      if has_pcrc:
        (pcrc,) = struct.unpack_from('>I', self._buf, start + length)
        if pcrc != crc32c(payload):
          self.bad_crc += 1
          del self._buf[:1]
          continue
      out.append(Frame(kind, service, message, payload, channel, encoding, rid, flags))
      del self._buf[:total]
