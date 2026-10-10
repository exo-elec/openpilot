"""EOP-owned, Tesla-party-only BrownPanda wire protocol. No OpenDBC runtime dependency."""
from enum import StrEnum
from typing import NamedTuple

class Bus(StrEnum):
  party = 'party'
  ap_party = 'ap_party'

class CanData(NamedTuple):
  address: int
  dat: bytes
  src: int
