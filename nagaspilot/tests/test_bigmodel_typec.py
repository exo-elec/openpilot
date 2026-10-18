from pathlib import Path

from nagaspilot.bigmodel import typec as T


class Clock:
  def __init__(self):
    self.t = 100.0

  def __call__(self):
    return self.t


def make(tmp_path: Path, data='[host] device', power='[source] sink', partner=True):
  root = tmp_path / 'typec'
  (root / 'port0').mkdir(parents=True)
  (root / 'port0' / 'data_role').write_text(data)
  (root / 'port0' / 'power_role').write_text(power)
  (root / 'port0' / 'power_operation_mode').write_text('usb_power_delivery')
  if partner:
    (root / 'port0-partner').mkdir()
  return root


def test_no_typec_class_is_no_port_and_a_missing_partner_is_not_an_error(tmp_path):
  assert T.TypeCPort(tmp_path / 'nothing').hold() == T.NO_PORT
  root = make(tmp_path, partner=False)
  p = T.TypeCPort(root)
  assert p.hold() == T.NO_PARTNER and p.status()['attached'] is False


def test_the_roles_are_read_from_the_bracketed_word(tmp_path):
  p = T.TypeCPort(make(tmp_path, data='host [device]', power='source [sink]'))
  assert p.data_role() == 'device' and p.power_role() == 'sink'
  assert T.TypeCPort(make(tmp_path / 'b', data='[host] device', power='[source] sink')).data_role() == 'host'


def test_a_host_port_is_asked_to_become_the_device_and_a_device_port_is_left_alone(tmp_path):
  root = make(tmp_path)
  p = T.TypeCPort(root, now=Clock())
  assert p.hold() == T.SWAPPED
  assert (root / 'port0' / 'data_role').read_text() == 'device'      # the request the kernel turns into a DR_SWAP
  (root / 'port0' / 'data_role').write_text('host [device]')        # the kernel completed it
  assert p.hold() == T.DEVICE
  assert p.status()['state'] == T.DEVICE and p.status()['power_role'] == 'source'


def test_the_swap_is_not_asked_for_in_a_tight_loop_and_gives_up_after_a_few_tries(tmp_path):
  clock = Clock()
  root = make(tmp_path)
  p = T.TypeCPort(root, now=clock, retry_s=2.0, max_tries=3)
  assert p.hold() == T.SWAPPED
  (root / 'port0' / 'data_role').write_text('[host] device')         # the phone refused: still host
  assert p.hold() == T.WAIT
  for _ in range(2):
    clock.t += 2.5
    assert p.hold() == T.SWAPPED
    (root / 'port0' / 'data_role').write_text('[host] device')
  clock.t += 2.5
  assert p.hold() == T.FAILED                                       # stops asking for this attach
  (root / 'port0-partner').rmdir()
  assert p.hold() == T.NO_PARTNER                                   # an unplug resets it
  (root / 'port0-partner').mkdir()
  clock.t += 2.5
  assert p.hold() == T.SWAPPED


def test_a_port_that_cannot_swap_reports_failure_not_an_exception(tmp_path):
  root = make(tmp_path)
  (root / 'port0' / 'data_role').unlink()
  (root / 'port0' / 'data_role').mkdir()          # writing to a directory raises OSError, like a read-only attribute
  p = T.TypeCPort(root, now=Clock(), max_tries=1)
  assert p.hold() == T.FAILED
