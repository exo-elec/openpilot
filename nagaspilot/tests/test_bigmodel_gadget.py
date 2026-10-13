import os
import subprocess
from pathlib import Path

import pytest

from nagaspilot.bigmodel import gadget as G

SCRIPT = Path(G.SCRIPT)


class Run:
  def __init__(self, fail=()):
    self.calls, self.fail = [], set(fail)

  def __call__(self, *args, **kw):
    self.calls.append(args[0])
    return args[0] not in self.fail


def owner(mode=2, run=None, net=None):
  run = run or Run()
  state = {'net': net if net is not None else False}

  def netok():
    return state['net']

  def runner(*a, **k):
    ok = run(*a, **k)
    if a[0] == 'net' and ok:
      state['net'] = True
    return ok
  return G.GadgetOwner(lambda: mode, run=runner, net_ok=netok), run, state


def test_nothing_happens_while_the_setting_is_off():
  o, run, _ = owner(mode=0)
  assert o.step() == 'off' and run.calls == []


def test_it_creates_binds_then_addresses_and_then_is_quiet():
  o, run, _ = owner()
  assert o.step() == 'up'
  assert run.calls == ['gadget', 'bind', 'net']
  assert o.step() == 'up' and run.calls == ['gadget', 'bind', 'net']


def test_a_failed_step_is_retried_next_time():
  run = Run(fail={'bind'})
  o, _, _ = owner(run=run)
  assert o.step() == 'working' and not o.up
  run.fail.clear()
  assert o.step() == 'up' and run.calls[-3:] == ['gadget', 'bind', 'net']


def test_the_netdev_is_redone_when_it_goes_away():
  o, run, state = owner()
  o.step()
  state['net'] = False                      # the phone was replugged and the kernel rebuilt the netdev
  assert o.step() == 'up' and run.calls[-1] == 'net'


def test_turning_it_off_releases_the_port():
  flags = {'mode': 2}
  run = Run()
  o = G.GadgetOwner(lambda: flags['mode'], run=run, net_ok=lambda: True)
  o.step()
  flags['mode'] = 0
  assert o.step() == 'off' and run.calls[-1] == 'teardown' and not o.up
  assert o.step() == 'off' and run.calls.count('teardown') == 1


def test_an_unreadable_setting_never_takes_the_port():
  def boom():
    raise OSError('params')
  o = G.GadgetOwner(boom, run=Run(), net_ok=lambda: True)
  assert o.wanted() == 0


def test_close_releases_only_what_it_took():
  o, run, _ = owner(mode=0)
  o.close()
  assert run.calls == []
  o, run, _ = owner()
  o.step()
  o.close()
  assert run.calls[-1] == 'teardown'


def test_off_a_device_run_is_a_quiet_false(monkeypatch):
  monkeypatch.setattr(G.platform, 'on_device', lambda: False)
  assert G.run('gadget') is False


def test_root_runs_the_script_directly_and_others_through_sudo(monkeypatch):
  calls = []

  class Done:
    returncode = 0
    stderr = ''
  monkeypatch.setattr(G.platform, 'on_device', lambda: True)
  monkeypatch.setattr(G.subprocess, 'run', lambda cmd, **k: calls.append(cmd) or Done())
  monkeypatch.setattr(G.os, 'geteuid', lambda: 0)
  assert G.run('gadget') is True and calls[-1][0] == 'bash'
  monkeypatch.setattr(G.os, 'geteuid', lambda: 1000)
  assert G.run('gadget', '--bulk') is True and calls[-1][:3] == ['sudo', '-n', 'bash'] and calls[-1][-1] == '--bulk'


# -- the root script against a fake configfs
def sh(tmp_path, *args):
  env = dict(os.environ, NGP_TEST='1', NGP_CONFIGFS=str(tmp_path / 'cfg'), NGP_UDC_DIR=str(tmp_path / 'udc'),
             NGP_RUN_DIR=str(tmp_path / 'run'))
  return subprocess.run(['bash', str(SCRIPT), *args], env=env, capture_output=True, text=True)


@pytest.fixture
def fake(tmp_path):
  (tmp_path / 'cfg' / 'usb_gadget').mkdir(parents=True)
  (tmp_path / 'udc' / 'a600000.usb').mkdir(parents=True)
  (tmp_path / 'run').mkdir()
  return tmp_path


def test_the_script_builds_an_ncm_gadget(fake):
  r = sh(fake, 'gadget')
  assert r.returncode == 0, r.stderr
  g = fake / 'cfg' / 'usb_gadget' / 'ngpbigmodel'
  assert (g / 'functions' / 'ncm.usb0').is_dir()
  assert (g / 'configs' / 'c.1' / 'ncm.usb0').is_symlink()
  assert (g / 'idVendor').read_text().strip() == '0x1d6b'
  assert (g / 'bcdUSB').read_text().strip() == '0x0320'
  assert (g / 'strings' / '0x409' / 'serialnumber').read_text().strip()
  assert sh(fake, 'gadget').returncode == 0           # idempotent


def test_bind_writes_the_controller_name_and_unbind_clears_it(fake):
  assert sh(fake, 'bind').returncode != 0              # no gadget yet
  sh(fake, 'gadget')
  assert sh(fake, 'bind').returncode == 0
  g = fake / 'cfg' / 'usb_gadget' / 'ngpbigmodel'
  assert (g / 'UDC').read_text().strip() == 'a600000.usb'
  assert sh(fake, 'unbind').returncode == 0
  assert (g / 'UDC').read_text().strip() == ''


def test_the_script_refuses_to_fight_another_gadget(fake):
  other = fake / 'cfg' / 'usb_gadget' / 'adb'
  other.mkdir()
  (other / 'UDC').write_text('a600000.usb\n')
  r = sh(fake, 'gadget')
  assert r.returncode != 0 and "'adb'" in r.stderr


def test_no_controller_is_an_error_with_a_reason(fake):
  (fake / 'udc' / 'a600000.usb').rmdir()
  r = sh(fake, 'gadget')
  assert r.returncode != 0 and 'controller' in r.stderr


def test_net_before_bind_reports_no_netdev_and_after_it_the_address(fake):
  sh(fake, 'gadget')
  assert sh(fake, 'net').returncode == 0
  assert (fake / 'run' / 'ngp-bigmodel-net').read_text().startswith('error')
  g = fake / 'cfg' / 'usb_gadget' / 'ngpbigmodel'
  (g / 'functions' / 'ncm.usb0' / 'ifname').write_text('usb0\n')
  assert sh(fake, 'net').returncode == 0
  assert (fake / 'run' / 'ngp-bigmodel-net').read_text().strip() == 'ok 192.168.60.1 usb0'
  assert G.net_ok(fake / 'run' / 'ngp-bigmodel-net')


def test_teardown_removes_the_gadget_and_status(fake):
  sh(fake, 'gadget')
  sh(fake, 'bind')
  assert sh(fake, 'teardown').returncode == 0
  assert not (fake / 'cfg' / 'usb_gadget' / 'ngpbigmodel').exists()
  assert 'gadget=no' in sh(fake, 'status').stdout


def test_an_unknown_command_is_a_usage_error(fake):
  assert sh(fake, 'frobnicate').returncode == 2


def test_the_script_is_not_runnable_as_a_normal_user_without_the_test_hook(fake):
  if os.geteuid() == 0:
    pytest.skip('root')
  env = dict(os.environ, NGP_CONFIGFS=str(fake / 'cfg'), NGP_UDC_DIR=str(fake / 'udc'), NGP_RUN_DIR=str(fake / 'run'))
  env.pop('NGP_TEST', None)
  r = subprocess.run(['bash', str(SCRIPT), 'gadget'], env=env, capture_output=True, text=True)
  assert r.returncode != 0 and 'root' in r.stderr


def test_the_script_builds_a_bulk_gadget_for_android(fake):
  r = sh(fake, 'gadget', '--bulk')
  assert r.returncode == 0, r.stderr
  g = fake / 'cfg' / 'usb_gadget' / 'ngpbigmodel'
  assert (g / 'functions' / 'ffs.ngpbigmodel').is_dir()
  assert (g / 'configs' / 'c.1' / 'ffs.ngpbigmodel').is_symlink()
  assert not (g / 'functions' / 'ncm.usb0').exists()


def test_switching_between_iphone_and_android_needs_a_teardown(fake):
  assert sh(fake, 'gadget').returncode == 0
  r = sh(fake, 'gadget', '--bulk')
  assert r.returncode != 0 and 'teardown' in r.stderr
  assert sh(fake, 'teardown').returncode == 0
  assert sh(fake, 'gadget', '--bulk').returncode == 0
  r = sh(fake, 'gadget')
  assert r.returncode != 0 and 'teardown' in r.stderr


def test_an_unknown_gadget_option_is_a_usage_error(fake):
  assert sh(fake, 'gadget', '--wat').returncode == 2


# -- the Android (bulk) mode of the owner
class FakeControl:
  def __init__(self, fail=False):
    self.fail, self.opened, self.closed, self.unbound, self.polled = fail, 0, 0, 0, 0

  def open(self, bind=True):
    self.opened += 1
    if self.fail:
      raise OSError('descriptors refused')

  def events(self):
    self.polled += 1
    return []

  def unbind(self):
    self.unbound += 1

  def close(self):
    self.closed += 1


def bulk_owner(mode_box, control=None):
  run = Run()
  made = control or FakeControl()
  o = G.GadgetOwner(lambda: mode_box[0], run=run, net_ok=lambda: False, control_factory=lambda: made)
  return o, run, made


def test_mode_1_builds_the_bulk_gadget_and_holds_ep0_without_a_network_step():
  o, run, control = bulk_owner([1])
  assert o.step() == 'up' and o.kind == G.BULK
  assert run.calls == ['gadget'] and control.opened == 1
  assert o.step() == 'up' and control.polled == 2 and run.calls == ['gadget']      # no net step, ep0 serviced each time


def test_a_failed_descriptor_write_tears_down_and_retries():
  control = FakeControl(fail=True)
  o, run, _ = bulk_owner([1], control=control)
  assert o.step() == 'working' and not o.up
  assert run.calls == ['gadget', 'teardown'] and control.closed == 1


def test_changing_the_setting_swaps_the_function():
  box = [2]
  made = FakeControl()
  run = Run()
  o = G.GadgetOwner(lambda: box[0], run=run, net_ok=lambda: True, control_factory=lambda: made)
  assert o.step() == 'up' and o.kind == G.NETWORK
  box[0] = 1
  assert o.step() == 'up' and o.kind == G.BULK
  assert run.calls == ['gadget', 'bind', 'teardown', 'gadget']
  box[0] = 2
  o.step()
  assert made.unbound == 1 and made.closed == 1 and run.calls[-3:] == ['teardown', 'gadget', 'bind']


def test_turning_it_off_releases_the_bulk_gadget_too():
  box = [1]
  made = FakeControl()
  o = G.GadgetOwner(lambda: box[0], run=Run(), net_ok=lambda: True, control_factory=lambda: made)
  o.step()
  box[0] = 0
  assert o.step() == 'off' and made.unbound == 1 and made.closed == 1 and not o.up
