#!/usr/bin/env python3
"""Pigeon GNSS Daemon for U-blox GPS modules.

Supports:
- RK3588 (ExoPilot 01M): NEO-M8U (UDR) - Dead reckoning, tunnels

Handles dynamic baud rate negotiation, AssistNow A-GPS injections,
and high-rate message configuration.
"""
from __future__ import annotations

import sys
import time
import signal
import struct
import requests
import urllib.parse
import threading
from datetime import datetime, timezone

UTC = timezone.utc  # datetime.UTC alias is 3.11+; keep 3.10 dev PCs working

from cereal import messaging
from openpilot.common.time_helpers import system_time_valid
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware import HARDWARE, RK3588
# GPIO configuration (simplified for direct use)
class GPIOConfig:
    pin: int
    direction: str

class GPIODirection:
    OUTPUT = "out"
    INPUT = "in"

# U-blox Protocol Constants
UBLOX_ACK = b"\xb5\x62\x05\x01\x02\x00"
UBLOX_NACK = b"\xb5\x62\x05\x00\x02\x00"
UBLOX_SOS_ACK = b"\xb5\x62\x09\x14\x08\x00\x02\x00\x00\x00\x01\x00\x00\x00"
UBLOX_SOS_NACK = b"\xb5\x62\x09\x14\x08\x00\x02\x00\x00\x00\x00\x00\x00\x00"
UBLOX_BACKUP_RESTORE_MSG = b"\xb5\x62\x09\x14\x08\x00\x03"
UBLOX_ASSIST_ACK = b"\xb5\x62\x13\x60\x08\x00"


class TTYPigeon:
  """Low-level serial wrapper for UBX protocol."""
  def __init__(self, baudrate: int = 9600):
    self.gps_hal = HARDWARE.get_gps_hal()
    self.gps_hal.initialize()
    self.gps_hal.set_baud(baudrate)

  def send(self, dat: bytes) -> None:
    self.gps_hal._gps_serial.write(dat)

  def receive(self) -> bytes:
    dat = b''
    while len(dat) < 0x1000:
      d = self.gps_hal._gps_serial.read(0x40)
      dat += d
      if len(d) == 0:
        break
    return dat

  def set_baud(self, baud: int) -> None:
    self.gps_hal.set_baud(baud)

  def wait_for_ack(self, ack: bytes = UBLOX_ACK, nack: bytes = UBLOX_NACK, timeout: float = 0.5) -> bool:
    dat = b''
    st = time.monotonic()
    while True:
      dat += self.receive()
      if ack in dat:
        return True
      elif nack in dat:
        return False
      elif time.monotonic() - st > timeout:
        raise TimeoutError('No response from ublox')
      time.sleep(0.001)

  def send_with_ack(self, dat: bytes, ack: bytes = UBLOX_ACK, nack: bytes = UBLOX_NACK) -> None:
    self.send(dat)
    self.wait_for_ack(ack, nack)


class PigeonD:
  """Pigeon GNSS Daemon - Multi-platform GPS driver."""

  def __init__(self):
    self.params = Params()
    self.pm = messaging.PubMaster(['ubloxRaw'])

    self.pigeon: TTYPigeon | None = None
    self.running = False
    self._pin_mapping = None

    # Platform detection
    self.is_rk3588 = RK3588

    try:
      self._pin_mapping = HARDWARE.get_pin_mapping()
    except Exception:
      pass

  def set_power(self, enabled: bool) -> None:
    """Manage GPS hardware power via HAL GPIO."""
    if self._pin_mapping is None:
      return

    try:
      gpio = HARDWARE.get_gpio_hal()
      config = GPIOConfig(direction=GPIODirection.OUTPUT)
      
      for pin_name in ['UBLOX_SAFEBOOT_N', 'GNSS_PWR_EN', 'UBLOX_RST_N']:
        try:
          pin = self._pin_mapping.get_pin_number(pin_name)
          gpio.init(pin, config)
          # PWR and RST follow 'enabled', SAFEBOOT always True
          val = enabled if pin_name != 'UBLOX_SAFEBOOT_N' else True
          gpio.set(pin, val)
        except (AttributeError, ValueError):
          continue
    except Exception as e:
      cloudlog.warning(f"PigeonD: GPIO control failed: {e}")

  def _init_baudrate_m8u(self):
    """Dynamic baud rate negotiation for NEO-M8U (RK3588)."""
    self.pigeon.set_baud(9600)
    # $PUBX,41,1,0007,0003,115200,0*1E\r\n
    self.pigeon.send(b"\x24\x50\x55\x42\x58\x2C\x34\x31\x2C\x31\x2C\x30\x30\x30\x37\x2C\x30\x30\x30\x33\x2C\x31\x31\x35\x32\x30\x30\x2C\x30\x2A\x31\x45\x0D\x0A")
    time.sleep(0.1)
    self.pigeon.set_baud(115200)

  def _configure_m8u(self) -> bool:
    """Apply NEO-M8U specific configuration (RK3588)."""
    for _ in range(10):
      try:
        # CFG-MSG rates
        for msg in [
          b"\xb5\x62\x06\x01\x03\x00\x01\x07\x01\x13\x51", # NAV-PVT
          b"\xb5\x62\x06\x01\x03\x00\x01\x14\x01\x14\x53", # NAV-HNR
          b"\xb5\x62\x06\x01\x03\x00\x10\x10\x01\x1c\x67", # ESF-STATUS
        ]:
          self.pigeon.send_with_ack(msg)
        return True
      except TimeoutError:
        continue
    return False

  def _assistnow_a_gps(self):
    """Inject AssistNow A-GPS data."""
    try:
      if not system_time_valid():
        return
      token = self.params.get('UbloxAssistNowToken')
      if not token:
        return
      
      lat = int(float(self.params.get('LastGPSLatitude') or 0.0) * 1e7)
      lon = int(float(self.params.get('LastGPSLongitude') or 0.0) * 1e7)
      alt = int(float(self.params.get('LastGPSAltitude') or 0.0) * 100)
      
      url = f"https://online-live1.services.u-blox.com/GetOnlineData.ashx?token={token};gnss=gps,glo,gal,bds;datatype=eph,alm,aux,pos;lat={lat};lon={lon};alt={alt};pacc=10000"
      r = requests.get(url, timeout=10)

      if r.status_code == 200 and len(r.content) > 0:
        self._send_assist_msg(r.content)
        cloudlog.info("PigeonD: AssistNow data injected")
    except Exception as e:
      cloudlog.warning(f"PigeonD: AssistNow failed: {e}")

  def _send_assist_msg(self, data: bytes):
    """Send AssistNow message with proper checksum."""
    msg = b"\xb5\x62\x13\x40\x00\x00" + data
    A, B = 0, 0
    for b in msg[2:]:
      A = (A + b) % 256
      B = (B + A) % 256
    self.pigeon.send_with_ack(msg + bytes([A, B]), ack=UBLOX_ASSIST_ACK)

  def run(self):
    """Main daemon loop."""
    self.set_power(False)
    time.sleep(0.1)
    self.set_power(True)
    time.sleep(0.5)

    # Initialize NEO-M8U
    self.pigeon = TTYPigeon(baudrate=9600)
    self._init_baudrate_m8u()
    if not self._configure_m8u():
      cloudlog.error("PigeonD: NEO-M8U configuration failed")
      return
    cloudlog.info("PigeonD: NEO-M8U configured")

    self.params.put_bool('UbloxAvailable', True)
    self.running = True
    last_almanac = time.monotonic()

    while self.running:
      # Read GPS data
      dat = self.pigeon.receive()
      if len(dat) > 0:
        if dat[0] == 0x00:
          # Reinit baudrate on error
          self._init_baudrate_m8u()
          continue

        # Publish GPS data
        msg = messaging.new_message('ubloxRaw', len(dat), valid=True)
        msg.ubloxRaw = dat[:]
        self.pm.send('ubloxRaw', msg)

        # Periodic almanac save
        if (time.monotonic() - last_almanac) > 300:
          self.pigeon.send(b"\xB5\x62\x09\x14\x04\x00\x00\x00\x00\x00\x21\xEC")
          last_almanac = time.monotonic()
      else:
        time.sleep(0.001)

  def stop(self):
    self.running = False
    if self.pigeon:
      self.pigeon.send(b"\xB5\x62\x06\x04\x04\x00\x00\x00\x08\x00\x16\x74")
    self.set_power(False)


def main():
  daemon = PigeonD()
  
  def handler(sig, frame):
    daemon.stop()
    sys.exit(0)
  
  signal.signal(signal.SIGINT, handler)
  daemon.run()

if __name__ == "__main__":
  main()
