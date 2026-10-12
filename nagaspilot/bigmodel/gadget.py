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
AGNOS = os.path.isfile('/AGNOS')
TIMEOUT = 30.0
PERIOD = 2.0
NET_STATUS = Path('/dev/shm/ngp-bigmodel-net')


def run(*args: str, timeout: float = TIMEOUT) -> bool:
  """sudo -n bash SCRIPT *args, on AGNOS. True when it exits 0; otherwise one log line and False. Never raises. Off AGNOS
  a quiet False: only AGNOS has the gadget stack."""
  if not AGNOS:
    return False
  what = ' '.join((SCRIPT.name, *args))
  try:
    result = subprocess.run(['sudo', '-n', 'bash', str(SCRIPT), *args], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
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


class GadgetOwner:
  """One decision per step(): what to ask of the root script, given the setting and whether a Chestnut holds the port."""

  def __init__(self, mode, chestnut, run=run, net_ok=net_ok):
    self._mode, self._chestnut, self._run, self._net_ok = mode, chestnut, run, net_ok
    self.up = False            # the gadget has been created and bound by this owner

  def wanted(self) -> bool:
    try:
      return self._mode() != 0 and not self._chestnut()
    except Exception:
      return False             # unknown never takes the port

  def step(self) -> str:
    """'off' (nothing wanted, port released), 'up' (all in place) or 'working' (a step was taken or failed; try again)."""
    if not self.wanted():
      if self.up:
        self._run('teardown')
        self.up = False
      return 'off'
    if not self.up:
      if not (self._run('gadget') and self._run('bind')):
        return 'working'
      self.up = True
    if not self._net_ok():
      self._run('net')
      if not self._net_ok():
        return 'working'
    return 'up'

  def close(self) -> None:
    if self.up:
      self._run('teardown')
      self.up = False


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
