"""PLP over a USB bulk pipe (Android): the padding rule, copied from Jetlink's link protocol (docs/link-protocol.md, "Padding") and from our NPBM framing.

A bulk transfer ends on a short packet. So the comma pads every frame it sends to a multiple of 16 KB (none ends on a short packet, and the phone's 16 KB reads never hold
the end of one message while waiting for the next), and the phone adds one pad byte when a frame's length is an exact multiple of 512 (the USB 2 packet, which divides USB 3's).
The PLP header carries the length, so the receiver finds the end of each frame and skips the padding: `FrameDecoder` already skips non-magic bytes, and the padding is zeros.

PLP gets its own vendor interface on the comma's composite gadget, beside the big-model one: Jetlink's iOS gadget is already composite, Jetlink never runs two protocols on one
pipe, and a second interface leaves the NPBM hot path (400 KB frames at 20 Hz) untouched. Not built: the second FunctionFS function and the Android claim of two interfaces.
"""
from __future__ import annotations

BURST = 16 * 1024
PACKET = 512


def pad_device_to_phone(frame_bytes: bytes) -> bytes:
  return frame_bytes + bytes(-len(frame_bytes) % BURST)


def pad_phone_to_device(frame_bytes: bytes) -> bytes:
  return frame_bytes + (b'\x00' if len(frame_bytes) % PACKET == 0 else b'')
