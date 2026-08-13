"""I2C sensor drivers for EOP10 imud.

ICM42670 (accel/gyro/temp) and LIS2MDL (magnetometer) are used on RK3588-based
ExoPilot 01M hardware. LSM6DS3 remains supported for legacy/development boards.

Register maps and scale factors are imported from the closed HAL package
(hal.platform.rk3588_sensors).  When the HAL is not installed the drivers fall
back to empty register maps and will not initialise real hardware, but the
module still imports cleanly for PC testing.
"""

from __future__ import annotations

import math
import struct
import time

from cereal import log
from openpilot.common.swaglog import cloudlog


try:
  from hal.platform.rk3588_sensors import ICM42670 as _ICM42670, LIS2MDL as _LIS2MDL
except ImportError:
  _ICM42670 = {}  # type: ignore[assignment]
  _LIS2MDL = {}   # type: ignore[assignment]


def _open_smbus(bus: int):
  """Open smbus2 SMBus handle."""
  import smbus2
  return smbus2.SMBus(bus)


class I2CSensor:
  """Minimal I2C sensor base."""

  def __init__(self, bus: int):
    self.bus = bus
    self._smbus = None
    self._initialized = False

  def _open(self) -> bool:
    try:
      self._smbus = _open_smbus(self.bus)
      return True
    except Exception as e:
      cloudlog.error(f"{self.__class__.__name__}: failed to open I2C bus {self.bus}: {e}")
      return False

  def _read_reg(self, reg: int) -> int:
    return self._smbus.read_byte_data(self.I2C_ADDRESS, reg)

  def _read_regs(self, reg: int, length: int) -> bytes:
    return bytes(self._smbus.read_i2c_block_data(self.I2C_ADDRESS, reg, length))

  def _write_reg(self, reg: int, value: int) -> None:
    self._smbus.write_byte_data(self.I2C_ADDRESS, reg, value)

  def close(self) -> None:
    if self._smbus is not None:
      try:
        self._smbus.close()
      except Exception:
        pass
      self._smbus = None
    self._initialized = False


class ICM42670(I2CSensor):
  """TDK ICM-42670 accel/gyro/temp on I2C.

  Output data rate: 200 Hz, ±2g accel, ±250 dps gyro.
  Mounting rotation of ROT_ANGLE_RAD is applied to align with vehicle frame.
  """

  I2C_ADDRESS = _ICM42670.get("I2C_ADDRESS", 0x00)

  WHO_AM_I = _ICM42670.get("WHO_AM_I", 0x00)
  CHIP_ID = _ICM42670.get("CHIP_ID", 0x00)

  REG_ACCEL_X0 = _ICM42670.get("REG_ACCEL_X0", 0x00)
  REG_ACCEL_X1 = _ICM42670.get("REG_ACCEL_X1", 0x00)
  REG_GYRO_X0 = _ICM42670.get("REG_GYRO_X0", 0x00)
  REG_GYRO_X1 = _ICM42670.get("REG_GYRO_X1", 0x00)
  REG_TEMP_X0 = _ICM42670.get("REG_TEMP_X0", 0x00)
  REG_TEMP_X1 = _ICM42670.get("REG_TEMP_X1", 0x00)

  REG_PWR_MGMT0 = _ICM42670.get("REG_PWR_MGMT0", 0x00)
  PWR_MGMT0_NORMAL = _ICM42670.get("PWR_MGMT0_NORMAL", 0x00)
  PWR_MGMT0_SLEEP = _ICM42670.get("PWR_MGMT0_SLEEP", 0x00)

  REG_ACCEL_CONFIG0 = _ICM42670.get("REG_ACCEL_CONFIG0", 0x00)
  REG_ACCEL_CONFIG1 = _ICM42670.get("REG_ACCEL_CONFIG1", 0x00)
  CONFIG_ACCEL_2_G = _ICM42670.get("CONFIG_ACCEL_2_G", 0x00)
  CONFIG_RATE_200_Hz = _ICM42670.get("CONFIG_RATE_200_Hz", 0x00)
  ACCEL_UI_FILT_BW_16HZ = _ICM42670.get("ACCEL_UI_FILT_BW_16HZ", 0x00)

  REG_GYRO_CONFIG0 = _ICM42670.get("REG_GYRO_CONFIG0", 0x00)
  REG_GYRO_CONFIG1 = _ICM42670.get("REG_GYRO_CONFIG1", 0x00)
  CONFIG_GYRO_250_DPS = _ICM42670.get("CONFIG_GYRO_250_DPS", 0x00)
  GYRO_UI_FILT_BW_16HZ = _ICM42670.get("GYRO_UI_FILT_BW_16HZ", 0x00)

  ROT_ANGLE_RAD = _ICM42670.get("ROT_ANGLE_RAD", 0.0)
  ACCEL_SCALE = _ICM42670.get("ACCEL_SCALE", 0.0)
  GYRO_SCALE_LSB_DPS = _ICM42670.get("GYRO_SCALE_LSB_DPS", 0.0)

  def __init__(self, bus: int = 0):
    super().__init__(bus)
    self.source = log.SensorEventData.SensorSource.icm42670

  def init(self) -> bool:
    try:
      if not self._open():
        return False

      chip_id = self._read_reg(self.WHO_AM_I)
      if chip_id != self.CHIP_ID:
        cloudlog.warning(f"ICM42670: unexpected WHO_AM_I 0x{chip_id:02X}, expected 0x{self.CHIP_ID:02X}")
        self.close()
        return False

      self._write_reg(self.REG_PWR_MGMT0, self.PWR_MGMT0_NORMAL)
      time.sleep(0.01)
      self._write_reg(self.REG_ACCEL_CONFIG0, self.CONFIG_ACCEL_2_G | self.CONFIG_RATE_200_Hz)
      self._write_reg(self.REG_ACCEL_CONFIG1, self.ACCEL_UI_FILT_BW_16HZ)
      self._write_reg(self.REG_GYRO_CONFIG0, self.CONFIG_GYRO_250_DPS | self.CONFIG_RATE_200_Hz)
      self._write_reg(self.REG_GYRO_CONFIG1, self.GYRO_UI_FILT_BW_16HZ)
      time.sleep(0.02)

      self._initialized = True
      cloudlog.info(f"ICM42670: initialized on I2C bus {self.bus}")
      return True
    except Exception as e:
      cloudlog.error(f"ICM42670: init failed: {e}")
      self.close()
      return False

  def read(self) -> dict | None:
    if not self._initialized:
      return None
    try:
      # ICM42670 accel/gyro registers are big-endian, high byte first.
      ab = self._read_regs(self.REG_ACCEL_X1, 6)
      x_raw = self._be16(ab[5], ab[4]) * self.ACCEL_SCALE
      y_raw = -self._be16(ab[1], ab[0]) * self.ACCEL_SCALE
      z_raw = -self._be16(ab[3], ab[2]) * self.ACCEL_SCALE
      c, s = math.cos(-self.ROT_ANGLE_RAD), math.sin(-self.ROT_ANGLE_RAD)
      xr = c * x_raw - s * z_raw
      zr = s * x_raw + c * z_raw

      gb = self._read_regs(self.REG_GYRO_X1, 6)
      scale_rad = (math.pi / 180.0) / self.GYRO_SCALE_LSB_DPS
      gx_raw = self._be16(gb[5], gb[4]) * scale_rad
      gy_raw = -self._be16(gb[1], gb[0]) * scale_rad
      gz_raw = -self._be16(gb[3], gb[2]) * scale_rad
      gx = c * gx_raw - s * gz_raw
      gz = s * gx_raw + c * gz_raw

      tb = self._read_regs(self.REG_TEMP_X1, 2)
      temperature = self._be16(tb[1], tb[0]) / 128.0 + 25.0

      return {
        'acceleration': [xr, y_raw, zr],
        'gyro': [gx, gy_raw, gz],
        'temperature': temperature,
      }
    except Exception as e:
      cloudlog.error(f"ICM42670: read error: {e}")
      return None

  @staticmethod
  def _be16(hi: int, lo: int) -> int:
    return struct.unpack('>h', bytes([hi, lo]))[0]


class LIS2MDL(I2CSensor):
  """ST LIS2MDL magnetometer on I2C.

  Continuous mode, 100 Hz ODR, temperature compensation enabled.
  Output is in µT (microtesla).
  """

  I2C_ADDRESS = _LIS2MDL.get("I2C_ADDRESS", 0x00)

  WHO_AM_I = _LIS2MDL.get("WHO_AM_I", 0x00)
  CHIP_ID = _LIS2MDL.get("CHIP_ID", 0x00)

  REG_MAGN_DATA = _LIS2MDL.get("REG_MAGN_DATA", 0x00)
  REG_CFG_REG_A = _LIS2MDL.get("REG_CFG_REG_A", 0x00)
  REG_CFG_REG_B = _LIS2MDL.get("REG_CFG_REG_B", 0x00)

  TEMP_COMP_MODE = _LIS2MDL.get("TEMP_COMP_MODE", 0x00)
  LOW_POWER_MODE = _LIS2MDL.get("LOW_POWER_MODE", 0x00)
  ODR_100HZ = _LIS2MDL.get("ODR_100HZ", 0x00)
  MODE_CONT = _LIS2MDL.get("MODE_CONT", 0x00)
  LOW_PASS_ON = _LIS2MDL.get("LOW_PASS_ON", 0x00)

  SCALE_UT_PER_LSB = _LIS2MDL.get("SCALE_UT_PER_LSB", 0.0)

  def __init__(self, bus: int = 0):
    super().__init__(bus)
    self.source = log.SensorEventData.SensorSource.lis2mdl

  def init(self) -> bool:
    try:
      if not self._open():
        return False

      chip_id = self._read_reg(self.WHO_AM_I)
      if chip_id != self.CHIP_ID:
        cloudlog.warning(f"LIS2MDL: unexpected WHO_AM_I 0x{chip_id:02X}, expected 0x{self.CHIP_ID:02X}")
        self.close()
        return False

      self._write_reg(self.REG_CFG_REG_A, self.TEMP_COMP_MODE | self.ODR_100HZ | self.MODE_CONT)
      self._write_reg(self.REG_CFG_REG_B, self.LOW_PASS_ON)
      time.sleep(0.01)

      self._initialized = True
      cloudlog.info(f"LIS2MDL: initialized on I2C bus {self.bus}")
      return True
    except Exception as e:
      cloudlog.error(f"LIS2MDL: init failed: {e}")
      self.close()
      return False

  def read(self) -> dict | None:
    if not self._initialized:
      return None
    try:
      b = self._read_regs(self.REG_MAGN_DATA, 6)
      # NED convention: x=-word2, y=word0, z=-word1
      x = -self._le16(b[3], b[2]) * self.SCALE_UT_PER_LSB
      y = self._le16(b[1], b[0]) * self.SCALE_UT_PER_LSB
      z = -self._le16(b[5], b[4]) * self.SCALE_UT_PER_LSB
      return {'magnetic': [x, y, z]}
    except Exception as e:
      cloudlog.error(f"LIS2MDL: read error: {e}")
      return None

  @staticmethod
  def _le16(lo: int, hi: int) -> int:
    return struct.unpack('<h', bytes([lo, hi]))[0]
