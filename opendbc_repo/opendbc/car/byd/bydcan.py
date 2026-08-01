"""Passive BYD CAN checksum support.

Adapted from shemps/byd-atto3-openpilot-port commit
5b34194240bb831719629d2fd095fae5daaed1e0.
"""


def byd_checksum(address: int, sig, dat: bytearray) -> int:
  del address, sig
  return (~sum(dat[:7])) & 0xFF
