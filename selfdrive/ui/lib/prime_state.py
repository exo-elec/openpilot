from enum import IntEnum
import os

from openpilot.common.params import Params


class PrimeType(IntEnum):
  UNKNOWN = -2,
  UNPAIRED = -1,
  NONE = 0,
  MAGENTA = 1,
  LITE = 2,
  BLUE = 3,
  MAGENTA_NEW = 4,
  PURPLE = 5,


class PrimeState:
  """EOP: No cloud account / prime subscription. Always offline."""

  def __init__(self):
    self._params = Params()
    self.prime_type: PrimeType = self._load_initial_state()

  def _load_initial_state(self) -> PrimeType:
    prime_type_str = os.getenv("PRIME_TYPE") or self._params.get("PrimeType")
    try:
      if prime_type_str is not None:
        return PrimeType(int(prime_type_str))
    except (ValueError, TypeError):
      pass
    return PrimeType.UNKNOWN

  def get_type(self) -> PrimeType:
    return self.prime_type

  def is_prime(self) -> bool:
    # EOP: No prime subscription. Always False.
    return False
