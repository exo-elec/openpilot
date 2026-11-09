"""Hold the USB-C port as the USB device (and read what the PD controller negotiated), through the kernel's Type-C class.

For a board with a PD controller (exopilot docs/02-HARDWARE/DIRECT_USB_LINK.md): the board is the PD power source and the phone is the sink. A source starts as the
USB host, so once the phone is attached the board asks it for a data-role swap and keeps asking until the port is the device. That is Jetlink's `port hold` / `port device`,
done through `/sys/class/typec/port*/data_role` (writing `device` requests the swap; it only works when the port was registered as dual-role, which the connector node
sets with `data-role = "dual"`). The kernel's `tcpm` does the PD messaging; this only asks and checks. Standard library only; a PC has no such directory and gets 'no_port'.
"""
from __future__ import annotations

import time
from pathlib import Path

TYPEC_ROOT = Path('/sys/class/typec')

NO_PORT = 'no_port'          # no Type-C port in this kernel
NO_PARTNER = 'no_partner'    # nothing attached
DEVICE = 'device'            # the port is the USB device: the gadget can work
SWAPPED = 'swapped'          # a swap was requested this step; check again next step
WAIT = 'wait'                # a swap was requested recently; not asking again yet
FAILED = 'failed'            # the swap was refused or the write failed too many times for this attach


def _active(text: str) -> str | None:
  """'host [device]' -> 'device' (the bracketed word is the current role)."""
  for word in text.split():
    if word.startswith('[') and word.endswith(']'):
      return word[1:-1]
  return None


class TypeCPort:
  def __init__(self, root: Path = TYPEC_ROOT, now=time.monotonic, retry_s: float = 2.0, max_tries: int = 5):
    self.root, self._now, self.retry_s, self.max_tries = Path(root), now, retry_s, max_tries
    self._last = float('-inf')
    self._tries = 0

  def port(self) -> Path | None:
    try:
      for p in sorted(self.root.iterdir()):
        if p.name.startswith('port') and '-' not in p.name:
          return p
    except OSError:
      pass
    return None

  def attached(self, port: Path | None = None) -> bool:
    port = port or self.port()
    return port is not None and (self.root / f'{port.name}-partner').exists()

  def _read(self, port: Path, name: str) -> str | None:
    try:
      return (port / name).read_text().strip()
    except OSError:
      return None

  def data_role(self, port: Path | None = None) -> str | None:
    port = port or self.port()
    text = self._read(port, 'data_role') if port else None
    return _active(text) if text else None

  def power_role(self, port: Path | None = None) -> str | None:
    port = port or self.port()
    text = self._read(port, 'power_role') if port else None
    return _active(text) if text else None

  def status(self) -> dict:
    """What the port is doing, for logs and the status screen."""
    port = self.port()
    if port is None:
      return {'state': NO_PORT}
    return {'state': DEVICE if self.data_role(port) == 'device' else 'host' if self.data_role(port) == 'host' else 'unknown',
            'attached': self.attached(port), 'data_role': self.data_role(port), 'power_role': self.power_role(port),
            'power_operation_mode': self._read(port, 'power_operation_mode')}

  def hold(self) -> str:
    """One step: make the port the USB device if a partner is attached. Cheap to call every gadget-owner step."""
    port = self.port()
    if port is None:
      return NO_PORT
    if not self.attached(port):
      self._tries = 0                # a new attach starts afresh
      return NO_PARTNER
    if self.data_role(port) == 'device':
      self._tries = 0
      return DEVICE
    if self._tries >= self.max_tries:
      return FAILED
    if self._now() - self._last < self.retry_s:
      return WAIT
    self._last = self._now()
    self._tries += 1
    try:
      (port / 'data_role').write_text('device')
    except OSError:
      return FAILED if self._tries >= self.max_tries else WAIT
    return SWAPPED
