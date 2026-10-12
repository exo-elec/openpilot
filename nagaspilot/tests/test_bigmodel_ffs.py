import os
import struct
import threading
import time

import pytest

from nagaspilot.bigmodel import ffs
from nagaspilot.bigmodel import framing as F
from nagaspilot.bigmodel import protocol as P


def test_descriptors_follow_the_functionfs_v2_layout():
  d = ffs.build_descriptors()
  magic, length, flags = struct.unpack_from('<III', d)
  assert magic == 3 and length == len(d) and flags == 7
  assert struct.unpack_from('<III', d, 12) == (3, 3, 5)         # counts first: fs 3, hs 3, ss 5 descriptors
  fs = d[24:24 + 9 + 7 + 7]
  assert fs[:2] == bytes([9, 4]) and fs[9 + 2] == ffs.EP_OUT and fs[9 + 7 + 2] == ffs.EP_IN
  assert fs[4] == 2 and fs[5:8] == bytes(ffs.VENDOR_CLASS)     # two endpoints, vendor class
  assert b'\x06\x30\x0f' in d                                    # the SuperSpeed companion with a 16 KB burst
  assert len(d) == 12 + 12 + 23 + 23 + (9 + 7 + 6 + 7 + 6)


def test_strings_are_one_english_string():
  s = ffs.build_strings('x')
  assert struct.unpack_from('<IIII', s) == (2, len(s), 1, 1)
  assert s.endswith(b'\x09\x04x\0')


def udc(root, state):
  d = root / 'a600000.usb'
  d.mkdir(exist_ok=True)
  (d / 'state').write_text(state + '\n')


def test_configured_reads_the_controller_state(tmp_path):
  assert ffs.udc_state(tmp_path / 'none') is None and not ffs.configured(tmp_path / 'none')
  udc(tmp_path, 'addressed')
  assert not ffs.configured(tmp_path)
  udc(tmp_path, 'configured')
  assert ffs.configured(tmp_path)


def test_endpoint_files_are_not_opened_until_the_host_configures(tmp_path):
  udc(tmp_path, 'powered')
  calls = []
  with pytest.raises(TimeoutError):
    ffs.FfsEndpoint.open(str(tmp_path), tmp_path, timeout=0.2, sleep=lambda s: time.sleep(0.01))
  assert calls == []                      # ep1/ep2 do not even exist here: opening them would have raised OSError instead


def test_control_writes_descriptors_then_strings_then_binds(tmp_path):
  mount = tmp_path / 'mnt'
  mount.mkdir()
  (mount / 'ep0').write_bytes(b'')
  gadget = tmp_path / 'g'
  gadget.mkdir()
  (gadget / 'UDC').write_text('')
  udcs = tmp_path / 'udc'
  (udcs / 'a600000.usb').mkdir(parents=True)
  c = ffs.FfsControl(str(mount), str(gadget), udcs)
  c.open()
  c.close()
  assert (mount / 'ep0').read_bytes() == ffs.build_descriptors() + ffs.build_strings()
  assert (gadget / 'UDC').read_text() == 'a600000.usb'
  c.unbind()
  assert (gadget / 'UDC').read_text().strip() == ''


def test_events_are_decoded_nonblocking(tmp_path):
  r, w = os.pipe()
  c = ffs.FfsControl()
  c.ep0 = r
  os.write(w, ffs.FfsControl.EVENT.pack(b'\0' * 8, ffs.FfsControl.ENABLE) + ffs.FfsControl.EVENT.pack(b'\0' * 8, ffs.FfsControl.DISABLE))
  assert c.events() == [ffs.FfsControl.ENABLE, ffs.FfsControl.DISABLE]
  assert c.events() == []
  os.close(r), os.close(w)


def endpoint_pair():
  """An endpoint over two pipes, and the 'host' ends: (host writes -> comma reads), (comma writes -> host reads)."""
  to_comma_r, to_comma_w = os.pipe()
  from_comma_r, from_comma_w = os.pipe()
  return ffs.FfsEndpoint(to_comma_r, from_comma_w), to_comma_w, from_comma_r


def test_endpoint_reads_with_a_deadline_and_hands_back_partial_chunks():
  ep, host_w, _ = endpoint_pair()
  with pytest.raises(TimeoutError):
    ep.read(10, 0.05)
  os.write(host_w, b'abcdefgh')
  assert ep.read(3, 1) == b'abc' and ep.read(100, 1) == b'defgh'
  ep.close()


def test_endpoint_writes_reach_the_host_in_order():
  ep, _, host_r = endpoint_pair()
  data = bytes(range(256)) * 100
  got = bytearray()
  t = threading.Thread(target=lambda: [got.extend(os.read(host_r, 65536)) for _ in iter(lambda: len(got) < len(data), False)], daemon=True)
  t.start()
  ep.write(data)
  t.join(2)
  assert bytes(got) == data
  ep.close()


def test_a_closed_host_end_is_a_connection_error():
  ep, host_w, _ = endpoint_pair()
  os.close(host_w)
  with pytest.raises(ConnectionError):
    ep.read(10, 1)
  ep.close()


def test_the_listener_gives_a_bulk_socket_once_per_enumeration(tmp_path):
  udc(tmp_path, 'configured')
  made = []

  def opener(mount, root, timeout):
    ep, _, _ = endpoint_pair()
    made.append(ep)
    return ep
  listener = ffs.FfsListener(str(tmp_path), tmp_path, opener).start()
  s = listener.take(1)
  assert isinstance(s, F.BulkSocket) and len(made) == 1
  assert listener.take(0.1) is None                 # still the same enumeration: no second open
  udc(tmp_path, 'default')                           # the host left
  assert listener.take(0.1) is not None and len(made) == 2
  for ep in made:
    ep.close()


def test_the_listener_returns_none_when_the_host_never_configures(tmp_path):
  def opener(mount, root, timeout):
    raise TimeoutError
  assert ffs.FfsListener(str(tmp_path), tmp_path, opener).take(0.1) is None


def test_the_protocol_runs_over_endpoint_files_with_bulk_framing():
  """Client -> BulkSocket -> FfsEndpoint(pipes) -> a host thread speaking the phone's framing -> the reference server."""
  import numpy as np

  from nagaspilot.bigmodel import server_ref as S
  from nagaspilot.bigmodel.client import Client
  ep, host_w, host_r = endpoint_pair()

  class HostEnd:
    def read(self, n, timeout):
      return os.read(host_r, n)

    def write(self, data):
      os.write(host_w, data)

    def close(self):
      pass
  server = S.Server(S.StubBackend(), listen=False)
  threading.Thread(target=server.serve, args=(F.HostBulkSocket(HostEnd()),), daemon=True).start()
  with Client(sock=F.BulkSocket(ep)) as c:
    assert c.connect().model == 'stub'
    seq = c.send_frame(1, P.RESET, [0] * 8, (1, 0), bytes([4]) * P.FRAME_BYTES)
    r = c.recv_response(seq, 3.0)
    assert r is not None and np.isfinite(np.frombuffer(r.out, np.float32)).all()
