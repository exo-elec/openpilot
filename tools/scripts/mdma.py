#!/usr/bin/env python3

import sys
import time
from threading import Thread
from argparse import ArgumentParser

from pyftdi.ftdi import Ftdi
from pyftdi.term import Terminal
from pyftdi.eeprom import FtdiEeprom

# On linux, you might want to add a udev rule like this:
# echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="3801", ATTR{idProduct}=="3d3a", MODE="0666"' | sudo tee /etc/udev/rules.d/99-mdma.rules
# sudo udevadm control --reload-rules; sudo udevadm trigger

class MDMA:
  VID = 0x3801
  PID = 0x3d3a
  CBUS = {
    'AUX_EN': ((1 << 0), False),
    'VIN_EN': ((1 << 1), True),
    'WDOG_DISABLE_N': ((1 << 2), False),
    'STM_VBUS_DISABLE': ((1 << 3), False),
  }

  @staticmethod
  def provision(device_url: str = 'ftdi://ftdi:230x/1'):
    ftdi = Ftdi()
    ftdi.open_from_url(device_url)
    eeprom = FtdiEeprom()
    eeprom.connect(ftdi)

    print("Provisioning FTDI device for MDMA...")
    eeprom.set_property('vendor_id', MDMA.VID)
    eeprom.set_property('product_id', MDMA.PID)
    for i in range(4):
      eeprom.set_property(f'cbus_func_{i}', 'GPIO')

    if eeprom.commit(dry_run=False):
      eeprom.reset_device()
    ftdi.reset()
    print("Provisioning complete.")

  def __init__(self, baudrate: int = 115200):
    self.baudrate = baudrate
    self.ftdi = None
    self.provisioned = False

  def __enter__(self):
    self.open()
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    self.close()

  def open(self):
    self.ftdi = Ftdi()
    self.ftdi.open(self.VID, self.PID)
    self.ftdi.set_baudrate(self.baudrate)
    self.ftdi.set_line_property(8, 1, 'N')

    # setup gpio
    outputs = 0
    for mask, default_value in self.CBUS.values():
      self.ftdi.set_cbus_direction(mask, mask)
      outputs |= mask if default_value else 0x00
    self.ftdi.set_cbus_gpio(outputs)

  def close(self):
    self.ftdi.close()

  def read(self) -> bytes:
    return self.ftdi.read_data(256).decode('utf-8', errors='replace')

  def terminal(self):
    term = Terminal()
    stop = False
    term.init(fullterm=False)
    sys.stdout.flush()

    def _read_thread():
      nonlocal stop
      try:
        while not stop:
          sys.stdout.buffer.write(self.ftdi.read_data(4096))
          sys.stdout.flush()
      except Exception:
        pass

    def _write_thread():
      nonlocal stop
      try:
        while not stop:
          char = term.getkey()
          if ord(char) == 0x2: # CTRL-B
            break
          self.ftdi.write_data(char)
      except KeyboardInterrupt:
        pass
      finally:
        stop = True
        term.reset()

    print("Entering terminal mode. Press CTRL-B to exit.")
    print("---------------------------------------------")

    read_thread = Thread(target=_read_thread, daemon=True)
    read_thread.start()

    write_thread = Thread(target=_write_thread, daemon=True)
    write_thread.start()
    write_thread.join()

    # reset scroll region
    sys.stdout.write("\x1b7\x1b[r\x1b[?6l\x1b8")
    sys.stdout.flush()

if __name__ == "__main__":
  parser = ArgumentParser(description="MDMA FTDI utility")
  parser.add_argument('--provision', action='store_true', help="Provision the FTDI device for MDMA use")
  parser.add_argument('--terminal', '-t', action='store_true', help="Run terminal")
  args = parser.parse_args()

  if args.provision:
    MDMA.provision()
    sys.exit(0)

  with MDMA() as mdma:
    if args.terminal:
      mdma.terminal()


