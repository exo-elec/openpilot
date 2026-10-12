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


def owner(mode=1, chestnut=False, run=None, net=None):
  run = run or Run()
  state = {'net': net if net is not None else False}

  def netok():
    return state['net']

  def runner(*a, **k):
    ok = run(*a, **k)
    if a[0] == 'net' and ok:
      state['net'] = True
    return ok
  return G.GadgetOwner(lambda: mode, lambda: chestnut, run=runner, net_ok=netok), run, state


def test_nothing_happens_while_the_setting_is_off_or_a_chestnut_holds_the_port():
  o, run, _ = owner(mode=0)
  assert o.step() == 'off' and run.calls == []
  o, run, _ = owner(chestnut=True)
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


def test_turning_it_off_or_a_chestnut_arriving_releases_the_port():
  flags = {'mode': 1, 'chestnut': False}
  run = Run()
  o = G.GadgetOwner(lambda: flags['mode'], lambda: flags['chestnut'], run=run, net_ok=lambda: True)
  o.step()
  flags['chestnut'] = True
  assert o.step() == 'off' and run.calls[-1] == 'teardown' and not o.up
  assert o.step() == 'off' and run.calls.count('teardown') == 1


def test_an_unreadable_setting_never_takes_the_port():
  def boom():
    raise OSError('params')
  o = G.GadgetOwner(boom, lambda: False, run=Run(), net_ok=lambda: True)
  assert o.wanted() is False


def test_close_releases_only_what_it_took():
  o, run, _ = owner(mode=0)
  o.close()
  assert run.calls == []
  o, run, _ = owner()
  o.step()
  o.close()
  assert run.calls[-1] == 'teardown'


def test_off_a_device_run_is_a_quiet_false(monkeypatch):
  monkeypatch.setattr(G, 'AGNOS', False)
  assert G.run('gadget') is False


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
