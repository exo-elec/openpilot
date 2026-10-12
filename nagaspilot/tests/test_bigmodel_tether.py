import os
import socket

from nagaspilot.bigmodel import gadget as G
from nagaspilot.bigmodel import server_ref as S
from nagaspilot.bigmodel import tether as T
from nagaspilot.bigmodel.client import Client

HEADER = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"


def fake_net(root, **drivers):
  """{ifname: driver}: sysfs-style /sys/class/net/<if>/device/driver -> .../drivers/<driver>."""
  for name, driver in drivers.items():
    dev = root / name / 'device'
    dev.mkdir(parents=True)
    target = root.parent / 'drivers' / driver
    target.mkdir(parents=True, exist_ok=True)
    os.symlink(target, dev / 'driver')


def route(path, *rows):
  path.write_text(HEADER + ''.join(f"{i}\t{d}\t{g}\t{f}\t0\t0\t{m}\t00000000\t0\t0\t0\n" for i, d, g, f, m in rows))


def test_interfaces_are_found_by_kernel_driver_not_by_name(tmp_path):
  net = tmp_path / 'net'
  net.mkdir()
  fake_net(net, wlan0='brcmfmac', enx0a1b='rndis_host', usb0='cdc_ncm', eth0='r8169', eth1='ipheth')
  assert T.tether_interfaces(net) == ['enx0a1b', 'eth1', 'usb0']
  assert T.tether_interfaces(tmp_path / 'nothing') == []


def test_the_gateway_is_the_phone_on_that_interface(tmp_path):
  r = tmp_path / 'route'
  # 0x2AA8C0 little-endian = 192.168.42.x; 0x8129A8C0 -> 192.168.41.129
  route(r, ('wlan0', '00000000', '0100A8C0', '0003', 100), ('usb0', '00000000', '812AA8C0', '0003', 900), ('usb0', '002AA8C0', '00000000', '0001', 900))
  assert T.gateway('usb0', r) == '192.168.42.129'
  assert T.gateway('wlan0', r) == '192.168.0.1'
  assert T.gateway('eth9', r) is None
  assert T.gateway('usb0', tmp_path / 'missing') is None


def test_a_connected_route_without_a_gateway_flag_is_not_a_phone(tmp_path):
  r = tmp_path / 'route'
  route(r, ('usb0', '00000000', '812AA8C0', '0001', 900))   # flags: up only, no RTF_GATEWAY
  assert T.gateway('usb0', r) is None


def test_the_listener_dials_the_phone_and_the_protocol_runs(tmp_path):
  server = S.Server(S.StubBackend(), '127.0.0.1', 0)
  try:
    net = tmp_path / 'net'
    net.mkdir()
    fake_net(net, usb0='rndis_host')
    r = tmp_path / 'route'
    route(r, ('usb0', '00000000', '0100007F', '0003', 900))     # 127.0.0.1
    listener = T.TetherListener(server.port, net, r).start()
    sock = listener.take(2.0)
    assert sock is not None
    with Client(sock=sock) as c:
      assert c.connect().model == 'stub'
  finally:
    server.close()


def test_no_tether_interface_means_no_phone(tmp_path):
  net = tmp_path / 'net'
  net.mkdir()
  fake_net(net, eth0='r8169')
  route(tmp_path / 'route', ('eth0', '00000000', '0100A8C0', '0003', 100))
  listener = T.TetherListener(5600, net, tmp_path / 'route')
  assert listener.phone_address() is None and listener.take(0.3) is None


def test_a_phone_whose_app_is_not_open_is_not_there_yet(tmp_path):
  s = socket.socket()
  s.bind(('127.0.0.1', 0))
  port = s.getsockname()[1]
  s.close()                                                      # nothing listens
  net = tmp_path / 'net'
  net.mkdir()
  fake_net(net, usb0='cdc_ncm')
  route(tmp_path / 'route', ('usb0', '00000000', '0100007F', '0003', 900))
  assert T.TetherListener(port, net, tmp_path / 'route').take(0.3) is None


def test_mode_3_leaves_the_comma_as_host_and_releases_any_gadget():
  class Run:
    calls = []

    def __call__(self, *a, **k):
      Run.calls.append(a[0])
      return True
  box = [2]
  o = G.GadgetOwner(lambda: box[0], lambda: False, run=Run(), net_ok=lambda: True)
  o.step()
  assert o.kind == G.NETWORK
  box[0] = 3
  assert o.step() == 'up' and not o.up and Run.calls[-1] == 'teardown'
  assert o.step() == 'up' and Run.calls.count('teardown') == 1


def test_the_hook_dials_for_mode_3():
  from nagaspilot.runtime.bigmodel import BigModelHook

  class P:
    def get(self, key, return_default=False):
      return 3

    def remove(self, key):
      pass
  assert BigModelHook(P(), lambda *a: None)._default_listener() is T.TetherListener
