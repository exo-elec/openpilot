#!/usr/bin/env python3
"""Pigeon GNSS Daemon for U-blox GPS modules.

Supports:
- RK3588 (ExoPilot 01M): NEO-M8U (UDR) - Dead reckoning, tunnels

This is an application-level driver: the UART device name and GPIO pin numbers
come from the closed exopilot HAL; the UBX protocol handling lives here.
"""
from __future__ import annotations

import sys
import time
import signal
import struct
import requests
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import serial

from cereal import messaging
from openpilot.common.time_helpers import system_time_valid
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware import HARDWARE, RK3588

UTC = timezone.utc

# U-blox Protocol Constants
UBLOX_ACK = b"\xb5\x62\x05\x01\x02\x00"
UBLOX_NACK = b"\xb5\x62\x05\x00\x02\x00"
UBLOX_SOS_ACK = b"\xb5\x62\x09\x14\x08\x00\x02\x00\x00\x00\x01\x00\x00\x00"
UBLOX_SOS_NACK = b"\xb5\x62\x09\x14\x08\x00\x02\x00\x00\x00\x00\x00\x00\x00"
UBLOX_BACKUP_RESTORE_MSG = b"\xb5\x62\x09\x14\x08\x00\x03"
UBLOX_ASSIST_ACK = b"\xb5\x62\x13\x60\x08\x00"


def _get_gps_device() -> str:
  """Return the GPS UART device for this board."""
  try:
    return HARDWARE.UART["GPS"]["device"]
  except Exception:
    return "/dev/ttyS7"


def _get_gps_pin(name: str) -> int | None:
  """Return GPIO number for a named GPS control pin, or None if not present."""
  try:
    return HARDWARE.GPIO[name]["num"]
  except Exception:
    return None


def _gpio_set(pin: int, value: bool) -> None:
  """Set a GPIO output via sysfs (board-level pin number)."""
  try:
    if not Path(f"/sys/class/gpio/gpio{pin}").exists():
      with open("/sys/class/gpio/export", "w") as f:
        f.write(str(pin))
    with open(f"/sys/class/gpio/gpio{pin}/direction", "w") as f:
      f.write("out")
    with open(f"/sys/class/gpio/gpio{pin}/value", "w") as f:
      f.write("1" if value else "0")
  except Exception:
    pass


def set_power(enabled: bool) -> None:
  """Power-cycle the u-blox module using HAL GPIO names."""
  safeboot = _get_gps_pin('UBLOX_SAFEBOOT_N')
  pwr = _get_gps_pin('GNSS_PWR_EN')
  rst = _get_gps_pin('UBLOX_RST_N')

  if safeboot is not None:
    _gpio_set(safeboot, True)
  if pwr is not None:
    _gpio_set(pwr, enabled)
  if rst is not None:
    _gpio_set(rst, enabled)


def add_ubx_checksum(msg: bytes) -> bytes:
  A = B = 0
  for b in msg[2:]:
    A = (A + b) % 256
    B = (B + A) % 256
  return msg + bytes([A, B])


def get_assistnow_messages(token: bytes, lat: int = 0, lon: int = 0, alt: int = 0) -> list[bytes]:
  """Fetch split AssistNow Online messages from u-blox."""
  r = requests.get(
    "https://online-live2.services.u-blox.com/GetOnlineData.ashx",
    params=urllib.parse.urlencode({
      'token': token,
      'gnss': 'gps,glo,gal,bds',
      'datatype': 'eph,alm,aux',
      'lat': lat,
      'lon': lon,
      'alt': alt,
      'pacc': 10000,
    }, safe=':,'),
    timeout=10
  )
  r.raise_for_status()
  dat = r.content

  msgs: list[bytes] = []
  while len(dat) > 0:
    if dat[:2] != b"\xb5\x62" or len(dat) < 6:
      break
    msg_len = 6 + (dat[5] << 8 | dat[4]) + 2
    if msg_len > len(dat):
      break
    msgs.append(dat[:msg_len])
    dat = dat[msg_len:]
  return msgs


class TTYPigeon:
  """Low-level serial wrapper for UBX protocol."""
  def __init__(self, baudrate: int = 9600):
    self.device = _get_gps_device()
    self.tty = serial.Serial(self.device, baudrate=baudrate, timeout=0)

  def send(self, dat: bytes) -> None:
    self.tty.write(dat)

  def receive(self) -> bytes:
    dat = b''
    while len(dat) < 0x1000:
      d = self.tty.read(0x40)
      dat += d
      if len(d) == 0:
        break
    return dat

  def set_baud(self, baud: int) -> None:
    self.tty.baudrate = baud

  def wait_for_ack(self, ack: bytes = UBLOX_ACK, nack: bytes = UBLOX_NACK, timeout: float = 0.5) -> bool:
    dat = b''
    st = time.monotonic()
    while True:
      dat += self.receive()
      if ack in dat:
        return True
      if nack in dat:
        cloudlog.error("PigeonD: received NACK from ublox")
        return False
      if time.monotonic() - st > timeout:
        raise TimeoutError('No response from ublox')
      time.sleep(0.001)

  def send_with_ack(self, dat: bytes, ack: bytes = UBLOX_ACK, nack: bytes = UBLOX_NACK) -> None:
    self.send(dat)
    self.wait_for_ack(ack, nack)

  def wait_for_backup_restore_status(self, timeout: float = 1.0) -> int:
    dat = b''
    st = time.monotonic()
    while True:
      dat += self.receive()
      position = dat.find(UBLOX_BACKUP_RESTORE_MSG)
      if position >= 0 and len(dat) >= position + 11:
        return dat[position + 10]
      if time.monotonic() - st > timeout:
        raise TimeoutError('No backup restore response from ublox')
      time.sleep(0.001)

  def reset_device(self) -> bool:
    """Cold-start and clear flash backup (used during factory setup)."""
    for _ in range(5):
      self.send(b"\xb5\x62\x06\x04\x04\x00\xff\xff\x00\x00\x0c\x5d")
      time.sleep(1)
      init_baudrate(self)
      self.send_with_ack(b"\xb5\x62\x06\x09\x0d\x00\x1f\x1f\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x17\x71\xd7")
      self.send_with_ack(b"\xB5\x62\x09\x14\x04\x00\x01\x00\x00\x00\x22\xf0")
      self.send(b"\xB5\x62\x09\x14\x00\x00\x1D\x60")
      status = self.wait_for_backup_restore_status()
      if status in (1, 3):
        return True
    return False


def init_baudrate(pigeon: TTYPigeon) -> None:
  """Negotiate 115200 baud from the u-blox 9600 default."""
  pigeon.set_baud(9600)
  # $PUBX,41,1,0007,0003,115200,0*1E\r\n
  pigeon.send(b"\x24\x50\x55\x42\x58\x2C\x34\x31\x2C\x31\x2C\x30\x30\x30\x37\x2C\x30\x30\x30\x33\x2C\x31\x31\x35\x32\x30\x30\x2C\x30\x2A\x31\x45\x0D\x0A")
  time.sleep(0.1)
  pigeon.set_baud(115200)


def initialize_pigeon(pigeon: TTYPigeon) -> bool:
  """Apply NEO-M8U production configuration and A-GNSS data."""
  for attempt in range(10):
    try:
      # Port configuration
      pigeon.send_with_ack(b"\xb5\x62\x06\x00\x14\x00\x03\xFF\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\x00\x01\x00\x00\x00\x00\x00\x1E\x7F")
      pigeon.send_with_ack(b"\xb5\x62\x06\x00\x14\x00\x00\xFF\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x19\x35")
      pigeon.send_with_ack(b"\xb5\x62\x06\x00\x14\x00\x01\x00\x00\x00\xC0\x08\x00\x00\x00\x08\x07\x00\x01\x00\x01\x00\x00\x00\x00\x00\xF4\x80")
      pigeon.send_with_ack(b"\xb5\x62\x06\x00\x14\x00\x04\xFF\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x1D\x85")
      pigeon.send_with_ack(b"\xb5\x62\x06\x00\x00\x00\x06\x18")
      pigeon.send_with_ack(b"\xb5\x62\x06\x00\x01\x00\x01\x08\x22")
      pigeon.send_with_ack(b"\xb5\x62\x06\x00\x01\x00\x03\x0A\x24")

      # 10 Hz navigation rate
      pigeon.send_with_ack(b"\xB5\x62\x06\x08\x06\x00\x64\x00\x01\x00\x00\x00\x79\x10")

      # Automotive dynamic model
      pigeon.send_with_ack(b"\xB5\x62\x06\x24\x24\x00\x05\x00\x04\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x5A\x63")

      # Odometer / UDR
      pigeon.send_with_ack(b"\xB5\x62\x06\x1E\x14\x00\x00\x00\x00\x00\x01\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x3C\x37")
      pigeon.send_with_ack(b"\xb5\x62\x06\x39\x08\x00\xFF\xAD\x62\xAD\x1E\x63\x00\x00\x83\x0C")
      pigeon.send_with_ack(b"\xb5\x62\x06\x23\x28\x00\x00\x00\x00\x04\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x56\x24")

      # Poll current configuration
      pigeon.send_with_ack(b"\xB5\x62\x06\x24\x00\x00\x2A\x84")
      pigeon.send_with_ack(b"\xB5\x62\x06\x23\x00\x00\x29\x81")
      pigeon.send_with_ack(b"\xB5\x62\x06\x1E\x00\x00\x24\x72")
      pigeon.send_with_ack(b"\xB5\x62\x06\x39\x00\x00\x3F\xC3")

      # Message rates: NAV-PVT, RXM-RAWX/MEASX, MON-HW, MON-IO, NAV-STATUS
      pigeon.send_with_ack(b"\xB5\x62\x06\x01\x03\x00\x01\x07\x01\x13\x51")
      pigeon.send_with_ack(b"\xB5\x62\x06\x01\x03\x00\x02\x15\x01\x22\x70")
      pigeon.send_with_ack(b"\xB5\x62\x06\x01\x03\x00\x02\x13\x01\x20\x6C")
      pigeon.send_with_ack(b"\xB5\x62\x06\x01\x03\x00\x0A\x09\x01\x1E\x70")
      pigeon.send_with_ack(b"\xB5\x62\x06\x01\x03\x00\x0A\x0B\x01\x20\x74")
      pigeon.send_with_ack(b"\xB5\x62\x06\x01\x03\x00\x01\x35\x01\x41\xAD")

      cloudlog.debug("PigeonD: UBX configuration applied")

      # Almanac backup restore
      pigeon.send(b"\xB5\x62\x09\x14\x00\x00\x1D\x60")
      restore_status = pigeon.wait_for_backup_restore_status()
      if restore_status == 2:
        cloudlog.info("PigeonD: almanac backup restored")
      elif restore_status == 3:
        cloudlog.info("PigeonD: no almanac backup found")
      else:
        cloudlog.warning(f"PigeonD: failed to restore almanac backup, status={restore_status}")

      # Inject current UTC time if system clock is plausible
      if system_time_valid():
        t_now = datetime.now(UTC)
        if t_now.year >= 2021:
          msg = add_ubx_checksum(b"\xB5\x62\x13\x40\x18\x00" + struct.pack("<BBBBHBBBBBxIHxxI",
            0x10, 0x00, 0x00, 0x80,
            t_now.year, t_now.month, t_now.day,
            t_now.hour, t_now.minute, t_now.second,
            0, 30, 0))
          pigeon.send_with_ack(msg, ack=UBLOX_ASSIST_ACK)
          cloudlog.info("PigeonD: sent UTC time assist")

      # AssistNow Online if token configured. Retry with exponential backoff
      # so transient network failures during boot do not leave the receiver
      # without ephemeris for the whole drive.
      token = Params().get('AssistNowToken')
      if token is not None:
        assist_backoff = 1.0
        for assist_attempt in range(3):
          try:
            lat = int(float(Params().get('LastGPSLatitude') or 0.0) * 1e7)
            lon = int(float(Params().get('LastGPSLongitude') or 0.0) * 1e7)
            alt = int(float(Params().get('LastGPSAltitude') or 0.0) * 100)
            for msg in get_assistnow_messages(token, lat=lat, lon=lon, alt=alt):
              pigeon.send_with_ack(msg, ack=UBLOX_ASSIST_ACK)
            cloudlog.info("PigeonD: AssistNow data injected")
            break
          except Exception:
            cloudlog.warning(f"PigeonD: AssistNow attempt {assist_attempt + 1} failed", exc_info=True)
            if assist_attempt < 2:
              time.sleep(assist_backoff)
              assist_backoff *= 2

      cloudlog.warning("PigeonD: GPS on!")
      return True
    except TimeoutError:
      cloudlog.warning(f"PigeonD: initialization attempt {attempt + 1} failed, retrying")

  cloudlog.error("PigeonD: failed to initialize pigeon after 10 attempts")
  return False


def deinitialize_and_exit(pigeon: TTYPigeon | None) -> None:
  """Save almanac to flash and power off cleanly."""
  cloudlog.warning("PigeonD: storing almanac and shutting down")

  if pigeon is not None:
    try:
      # Controlled GNSS stop
      pigeon.send(b"\xB5\x62\x06\x04\x04\x00\x00\x00\x08\x00\x16\x74")
      # Save almanac to flash
      pigeon.send(b"\xB5\x62\x09\x14\x04\x00\x00\x00\x00\x00\x21\xEC")
      if pigeon.wait_for_ack(ack=UBLOX_SOS_ACK, nack=UBLOX_SOS_NACK):
        cloudlog.info("PigeonD: almanac stored")
      else:
        cloudlog.warning("PigeonD: error storing almanac")
    except TimeoutError:
      cloudlog.warning("PigeonD: timeout while storing almanac")
    except Exception:
      cloudlog.exception("PigeonD: error during shutdown")

  set_power(False)
  sys.exit(0)


class PigeonD:
  """Pigeon GNSS Daemon - Multi-platform GPS driver."""

  def __init__(self):
    self.params = Params()
    self.pm = messaging.PubMaster(['ubloxRaw'])

    self.pigeon: TTYPigeon | None = None
    self.running = False

  def run(self) -> None:
    if not RK3588:
      cloudlog.warning("PigeonD: non-RK3588 platform, exiting")
      return

    device = _get_gps_device()
    if not Path(device).exists():
      cloudlog.warning(f"PigeonD: GPS device {device} not present, exiting")
      return

    set_power(False)
    time.sleep(0.1)
    set_power(True)
    time.sleep(0.5)

    self.pigeon = TTYPigeon(baudrate=9600)
    init_baudrate(self.pigeon)
    if not initialize_pigeon(self.pigeon):
      deinitialize_and_exit(self.pigeon)
      return

    self.params.put_bool('UbloxAvailable', True)
    self.running = True
    last_almanac = time.monotonic()

    while self.running:
      try:
        dat = self.pigeon.receive()
        if len(dat) > 0:
          if dat[0] == 0x00:
            cloudlog.warning("PigeonD: invalid data from ublox, re-initializing")
            init_baudrate(self.pigeon)
            if not initialize_pigeon(self.pigeon):
              break
            continue

          msg = messaging.new_message('ubloxRaw', len(dat), valid=True)
          msg.ubloxRaw = dat[:]
          self.pm.send('ubloxRaw', msg)
        else:
          time.sleep(0.001)

        # Save almanac every 5 minutes
        if time.monotonic() - last_almanac > 300:
          try:
            self.pigeon.send(b"\xB5\x62\x09\x14\x04\x00\x00\x00\x00\x00\x21\xEC")
            last_almanac = time.monotonic()
          except Exception:
            cloudlog.warning("PigeonD: periodic almanac save failed")
      except Exception:
        cloudlog.exception("PigeonD: error in receive loop")
        time.sleep(0.1)

    deinitialize_and_exit(self.pigeon)

  def stop(self) -> None:
    self.running = False


def main():
  daemon = PigeonD()

  def handler(sig, frame):
    daemon.stop()

  signal.signal(signal.SIGINT, handler)
  signal.signal(signal.SIGTERM, handler)
  daemon.run()


if __name__ == "__main__":
  main()
