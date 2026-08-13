# SocketD - SocketCAN daemon for EnhancedOpenPilot
# Drop-in replacement for pandad using native Linux SocketCAN

from openpilot.system.socketd.can_capnp import can_capnp_to_list, can_list_to_can_capnp
from openpilot.system.socketd.socketd import cansend

# Pandad API compatibility aliases
# FrogPilot/OpenPilot expect: (address, busTime, dat, src) 4-tuple
def can_capnp_to_can_list(can, src_filter=None):
  """Convert Cap'n Proto CAN events to list of tuples (pandad-compatible format).

  Returns list of (address, busTime, dat, src) tuples for compatibility
  with the original pandad API.
  """
  ret = []
  for msg in can:
    if src_filter is None or msg.src in src_filter:
      ret.append((msg.address, msg.busTime, msg.dat, msg.src))
  return ret

# Export functions for compatibility with pandad API
__all__ = ['can_capnp_to_list', 'can_capnp_to_can_list', 'can_list_to_can_capnp', 'cansend']
