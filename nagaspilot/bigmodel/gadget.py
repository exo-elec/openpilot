"""bigmodeld: hold the comma's USB-C port as a network gadget for the phone while the link is on.

The root work is `scripts/ngp-bigmodel-root.sh` under `sudo -n`; this is the policy around it. The owner does nothing while
the setting is off, stands aside for a Chestnut (the port cannot serve both), and otherwise keeps the gadget created,
bound and addressed, redoing whichever step is missing: the netdev only appears after the bind, and a replugged phone
can make the kernel rebuild it. Standard library only: manager runs this as a resident process.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
from pathlib import Path

from nagaspilot.runtime import bigmodel_platform as platform

log = logging.getLogger('nagaspilot.bigmodel.gadget')

SCRIPT = Path(__file__).resolve().parent / 'scripts' / 'ngp-bigmodel-root.sh'
TIMEOUT = 30.0
PERIOD = 2.0
NET_STATUS = Path('/dev/shm/ngp-bigmodel-net')


def run(*args: str, timeout: float = TIMEOUT) -> bool:
  """The root script, as root: directly when this process is root (the ExoPilot boards), else `sudo -n` (a comma). True when it
  exits 0; otherwise one log line and False. Never raises. Off a device (a PC) a quiet False: only a device has a gadget stack."""
  if not platform.on_device():
    return False
  what = ' '.join((SCRIPT.name, *args))
  prefix = [] if os.geteuid() == 0 else ['sudo', '-n']
  try:
    result = subprocess.run([*prefix, 'bash', str(SCRIPT), *args], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            timeout=timeout, text=True, errors='replace')
  except subprocess.TimeoutExpired:
    log.exception("%s timed out after %.0f s", what, timeout)
    return False
  except Exception:
    log.exception("%s did not run", what)
    return False
  if result.returncode == 0:
    return True
  lines = (result.stderr or '').strip().splitlines()
  log.error("%s failed (exit %d): %s", what, result.returncode, lines[-1] if lines else 'no output')
  return False


def net_ok(path: Path = NET_STATUS) -> bool:
  try:
    return path.read_text().startswith('ok ')
  except OSError:
    return False


# ngp_bigmodel_link: 2 iPhone (the comma is the USB device, NCM), 1 Android (bulk interface), 3 tethering (the comma is the host)
NETWORK, BULK, TETHER = 2, 1, 3


class GadgetOwner:
  """One decision per step(): what to ask of the root script, given the setting and whether a Chestnut holds the port.

  The setting picks the function: 2 is CDC-NCM for an iPhone, 1 the FunctionFS bulk interface an Android phone reads as USB
  host, and 3 (tethering) no function at all: the comma stays the USB host and any gadget is released. Changing it tears the
  gadget down and builds the other one. For bulk this process also holds ep0 (descriptors, bind, events) for as long as the
  gadget is up: closing ep0 would take the function with it."""

  def __init__(self, mode, chestnut, run=run, net_ok=net_ok, control_factory=None):
    self._mode, self._chestnut, self._run, self._net_ok = mode, chestnut, run, net_ok
    self._control_factory = control_factory
    self.kind: int | None = None   # NETWORK or BULK while up
    self.control = None

  @property
  def up(self) -> bool:
    return self.kind is not None

  def wanted(self) -> int:
    """0 when nothing is wanted: off, a Chestnut on the port, or an unreadable setting (unknown never takes the port)."""
    try:
      m = int(self._mode())
      return 0 if m == 0 or self._chestnut() else (NETWORK if m == NETWORK else TETHER if m == TETHER else BULK)
    except Exception:
      return 0

  def _release(self) -> None:
    if self.control is not None:
      try:
        self.control.unbind()
        self.control.close()
      finally:
        self.control = None
    self._run('teardown')
    self.kind = None

  def _make_control(self):
    if self._control_factory is not None:
      return self._control_factory()
    from nagaspilot.bigmodel.ffs import FfsControl
    return FfsControl(gadget='/sys/kernel/config/usb_gadget/ngpbigmodel')

  def step(self) -> str:
    """'off' (nothing wanted, port released), 'up' (all in place) or 'working' (a step was taken or failed; try again)."""
    want = self.wanted()
    if self.up and want != self.kind:
      self._release()
    if not want:
      return 'off'
    if want == TETHER:
      return 'up'    # the comma is the host: no gadget, nothing for this process to hold. The phone's interface is modeld's to find
    if not self.up:
      if want == NETWORK:
        if not (self._run('gadget') and self._run('bind')):
          return 'working'
      else:
        if not self._run('gadget', '--bulk'):
          return 'working'
        control = self._make_control()
        try:
          control.open(bind=True)
        except Exception:
          log.exception("could not write the FunctionFS descriptors and bind")
          control.close()
          self._run('teardown')
          return 'working'
        self.control = control
      self.kind = want
    if want == BULK:
      self.control.events()      # keep ep0 serviced
      return 'up'
    if not self._net_ok():
      self._run('net')
      if not self._net_ok():
        return 'working'
    return 'up'

  def close(self) -> None:
    if self.up:
      self._release()


def main() -> None:
  from openpilot.common.params import Params
  params = Params()

  def mode() -> int:
    return int(params.get(platform.LINK, return_default=True) or 0)

  owner = GadgetOwner(mode, platform.egpu_in_use)
  stop = threading.Event()
  signal.signal(signal.SIGTERM, lambda *_: stop.set())
  signal.signal(signal.SIGINT, lambda *_: stop.set())
  try:
    while not stop.is_set():
      owner.step()
      stop.wait(PERIOD)
  finally:
    owner.close()


if __name__ == '__main__':
  main()
