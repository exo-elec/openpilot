"""
SocketCAN message conversion functions compatible with openpilot messaging.
"""
from __future__ import annotations

import socket
import struct
from collections.abc import Iterable, Sequence

import time

import cereal.messaging as messaging
from cereal import log

CANMessageTuple = tuple[int, bytes, int]
RawCanInput = bytes | log.Event | Sequence[bytes] | Sequence[log.Event]

CAN_HEADER_FMT = "=IBB2x"
CAN_HEADER_LEN = struct.calcsize(CAN_HEADER_FMT)
CAN_MAX_DLEN = 8

CAN_SFF_MASK = getattr(socket, "CAN_SFF_MASK", 0x7FF)
CAN_EFF_MASK = getattr(socket, "CAN_EFF_MASK", 0x1FFFFFFF)
CAN_EFF_FLAG = getattr(socket, "CAN_EFF_FLAG", 0x80000000)
CAN_ERR_FLAG = getattr(socket, "CAN_ERR_FLAG", 0x20000000)


def sanitize_can_id(raw_id: int) -> int | None:
    """Return a clean arbitration ID stripped of SocketCAN flags."""
    if raw_id & CAN_ERR_FLAG:
        return None
    if raw_id & CAN_EFF_FLAG:
        return raw_id & CAN_EFF_MASK
    return raw_id & CAN_SFF_MASK


def encode_can_id(address: int) -> int:
    """Return a SocketCAN-ready CAN ID with the proper flag bits."""
    if address < 0:
        raise ValueError(f"CAN address must be non-negative, got {address}")
    if address & ~CAN_EFF_MASK:
        raise ValueError(f"CAN address out of range: 0x{address:x}")
    if address > CAN_SFF_MASK:
        return address | CAN_EFF_FLAG
    return address & CAN_SFF_MASK


def _as_event(capnp_msg: bytes | log.Event) -> log.Event | None:
    if isinstance(capnp_msg, log.Event):
        return capnp_msg
    if isinstance(capnp_msg, (bytes, bytearray)):
        try:
            return log.Event.from_bytes(capnp_msg)
        except Exception:
            return None
    return None


def can_capnp_to_list(can_capnp_data: RawCanInput, msgtype: str = 'can') -> list[CANMessageTuple]:
    """Convert Cap'n Proto CAN events, raw bytes, or iterables thereof to a list of tuples."""
    if isinstance(can_capnp_data, (list, tuple)):
        out: list[CANMessageTuple] = []
        for item in can_capnp_data:
            out.extend(can_capnp_to_list(item, msgtype))
        return out

    event = _as_event(can_capnp_data)
    if event is None:
        return []

    # Some callers pass the whole Event, others pass the specific union element already.
    if event.which() == msgtype:
        entries = getattr(event, msgtype)
    elif hasattr(event, msgtype):
        entries = getattr(event, msgtype)
    else:
        return []

    messages: list[CANMessageTuple] = []
    for msg in entries:
        data_bytes = bytes(msg.dat)
        src_bus = msg.src if hasattr(msg, 'src') else 0
        messages.append((int(msg.address), data_bytes, int(src_bus)))
    return messages


def can_list_to_can_capnp(can_list: Iterable[Sequence], msgtype: str = 'can', *, valid: bool = True):
    """Convert a Python iterable of CAN tuples into a messaging Event."""
    can_list = list(can_list)
    dat = messaging.new_message(msgtype, len(can_list))
    dat.valid = valid
    dat.logMonoTime = int(time.monotonic() * 1e9)

    capnp_entries = getattr(dat, msgtype)
    for i, entry in enumerate(can_list):
        if len(entry) == 4:
            address, _, data, src = entry
        elif len(entry) >= 3:
            address, data, src = entry[0], entry[1], entry[2]
        else:
            raise ValueError(f"CAN entry must have at least 3 elements, got {entry}")

        capnp_entries[i].address = int(address)
        capnp_entries[i].dat = bytes(data)
        capnp_entries[i].src = int(src)
        if hasattr(capnp_entries[i], 'busTime'):
            capnp_entries[i].busTime = 0

    return dat


def socketcan_frame_to_can_message(frame_data: bytes) -> CANMessageTuple:
    """Convert raw SocketCAN frame to CAN tuple (address, data, bus)."""
    if len(frame_data) < CAN_HEADER_LEN:
        raise ValueError(f"Invalid SocketCAN frame: too short ({len(frame_data)} bytes)")

    raw_id, msg_len, _ = struct.unpack(CAN_HEADER_FMT, frame_data[:CAN_HEADER_LEN])
    address = sanitize_can_id(raw_id)
    if address is None:
        raise ValueError("SocketCAN frame contains error flag")
    payload = frame_data[CAN_HEADER_LEN:CAN_HEADER_LEN + msg_len]
    return address, payload, 0


def can_message_to_socketcan_frame(address: int, data: bytes, bus: int = 0) -> bytes:
    """Convert CAN tuple into raw SocketCAN frame bytes."""
    msg_len = len(data)
    if msg_len > CAN_MAX_DLEN:
        raise ValueError(f"CAN message too long: {msg_len} > {CAN_MAX_DLEN}")

    padded = data.ljust(CAN_MAX_DLEN, b'\x00')
    can_id = encode_can_id(address)
    return struct.pack(CAN_HEADER_FMT, can_id, msg_len, 0) + padded
