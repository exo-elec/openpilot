import time

from nagaspilot.plp import frame as F, registry as R
from nagaspilot.plp.hello import Hello
from openpilot.system.bluetoothd import plp_gatt


class FakeTx:
  def __init__(self):
    self.sent = []

  def notify_chunk(self, chunk):
    self.sent.append(chunk)
    return True


def connect():
  p = plp_gatt.PlpGATT()
  p._tx = FakeTx()
  p._on_phone_connect()
  return p


def wait(cond):
  for _ in range(200):
    if cond():
      return
    time.sleep(0.01)


def test_hello_over_the_characteristics_and_disconnect_drops_the_session():
  p = connect()
  hello = Hello(services=plp_gatt.SERVICES, link='ble').to_json()
  p._on_write(F.Frame(R.KIND_REQUEST, 0, 1, hello, 0, F.ENC_JSON, 1).encode(), 185)
  wait(lambda: p._tx.sent)
  d = F.FrameDecoder()
  frames = [f for c in p._tx.sent for f in d.feed(c)]
  assert frames[0].kind == R.KIND_RESPONSE and Hello.parse(frames[0].payload).link == 'ble'
  assert p.session.agreed is not None
  p._on_phone_disconnect()
  assert p.session is None


def test_uuids_differ_from_the_ncp_service_and_a_write_before_subscribe_is_ignored():
  from openpilot.system.bluetoothd import ble_gatt
  assert plp_gatt.PLP_SERVICE_UUID != ble_gatt.NUS_SERVICE_UUID
  assert plp_gatt.PLP_APP_PATH != ble_gatt.GATT_APP_PATH
  p = plp_gatt.PlpGATT()
  p._on_write(b'junk', 185)           # no session yet: nothing happens
  assert p.session is None


def test_a_new_subscription_replaces_the_old_session():
  p = connect()
  first = p.session
  p._on_phone_connect()
  assert p.session is not first
  p._on_phone_disconnect()
