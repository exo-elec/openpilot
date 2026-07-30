"""Dev-PC stub for pandad_api_impl Cython extension.

Delete this file before building with SCons — the compiled .so replaces it.
Provides pure-Python CAN serialization using cereal capnp.
"""
import cereal.messaging as messaging


def can_list_to_can_capnp(can_msgs, msgtype='can', valid=True):
  """Serialize list of (address, data, src) tuples to capnp bytes."""
  dat = messaging.new_message(msgtype, size=len(can_msgs), valid=valid)
  frames = dat.init(msgtype, len(can_msgs))
  for i, (addr, data, src) in enumerate(can_msgs):
    frames[i].address = addr
    frames[i].dat = bytes(data)
    frames[i].src = src
  return dat.to_bytes()


def can_capnp_to_list(strings, msgtype='can'):
  """Deserialize capnp bytes to list of (nanos, [(address, data, src), ...])."""
  result = []
  for s in strings:
    msg = messaging.log.Event.from_bytes(s)
    can_data = getattr(msg, msgtype)
    frames = [(f.address, bytes(f.dat), f.src) for f in can_data]
    result.append((msg.logMonoTime, frames))
  return result
