#!/usr/bin/env python3
"""
imud - IMU Sensor Daemon

Polls IMU sensors (LSM6DS3, etc.) via I2C/SPI.
Optimized for A55 little cores.

Publishes:
  - accelerometer: Acceleration data
  - gyroscope: Gyroscope data
  - temperature: IMU temperature
"""

from __future__ import annotations

import time
import threading
import struct

import cereal.messaging as messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.swaglog import cloudlog

from openpilot.system.hardware import HARDWARE
from openpilot.system.imud.iio_imu import IIOImu

try:
    from hal.platform.rk3588_sensors import LSM6DS3 as _LSM6DS3
except ImportError:
    _LSM6DS3 = {}  # type: ignore[assignment]


class ImuSensor:
    """Base class for IMU sensors."""

    def __init__(self, name: str):
        self.name = name
        self.data_valid = False

    def read(self) -> dict | None:
        """Read sensor data. Returns dict with sensor values or None."""
        raise NotImplementedError

    def is_data_valid(self) -> bool:
        return self.data_valid


class LSM6DS3(ImuSensor):
    """LSM6DS3 IMU sensor via I2C.

    Datasheet: STM LSM6DS3
    - Accelerometer: ±2/±4/±8/±16 g
    - Gyroscope: ±125/±250/±500/±1000/±2000 dps
    - Temperature sensor
    - Output data rate: up to 6.66 kHz

    Register map is imported from the closed HAL package.  When the HAL is not
    installed the class uses empty defaults and will not initialise real
    hardware, but the module still imports cleanly for PC testing.
    """

    I2C_ADDRESS = _LSM6DS3.get("I2C_ADDRESS", 0x00)

    # Register addresses
    WHO_AM_I = _LSM6DS3.get("WHO_AM_I", 0x00)
    CTRL1_XL = _LSM6DS3.get("CTRL1_XL", 0x00)
    CTRL2_G = _LSM6DS3.get("CTRL2_G", 0x00)
    CTRL3_C = _LSM6DS3.get("CTRL3_C", 0x00)
    CTRL4_C = _LSM6DS3.get("CTRL4_C", 0x00)
    CTRL5_C = _LSM6DS3.get("CTRL5_C", 0x00)
    CTRL6_C = _LSM6DS3.get("CTRL6_C", 0x00)
    CTRL7_G = _LSM6DS3.get("CTRL7_G", 0x00)
    CTRL8_XL = _LSM6DS3.get("CTRL8_XL", 0x00)
    CTRL9_XL = _LSM6DS3.get("CTRL9_XL", 0x00)
    CTRL10_C = _LSM6DS3.get("CTRL10_C", 0x00)
    OUT_TEMP_L = _LSM6DS3.get("OUT_TEMP_L", 0x00)
    OUTX_L_G = _LSM6DS3.get("OUTX_L_G", 0x00)
    OUTX_L_XL = _LSM6DS3.get("OUTX_L_XL", 0x00)

    # Default config: 104 Hz ODR, ±2g accel, ±250 dps gyro
    DEFAULT_CTRL1_XL = _LSM6DS3.get("DEFAULT_CTRL1_XL", 0x00)
    DEFAULT_CTRL2_G = _LSM6DS3.get("DEFAULT_CTRL2_G", 0x00)
    DEFAULT_CTRL3_C = _LSM6DS3.get("DEFAULT_CTRL3_C", 0x00)

    # Scale factors
    ACCEL_SCALE_2G = _LSM6DS3.get("ACCEL_SCALE_2G", 0.0)
    GYRO_SCALE_250DPS = _LSM6DS3.get("GYRO_SCALE_250DPS", 0.0)
    TEMP_SCALE = _LSM6DS3.get("TEMP_SCALE", 0.0)
    TEMP_OFFSET = _LSM6DS3.get("TEMP_OFFSET", 0.0)

    def __init__(self, bus: int = 0):
        super().__init__("LSM6DS3")
        self.bus = bus
        self._initialized = False
        self._smbus = None

    def _open_bus(self) -> bool:
        """Open I2C bus via smbus2."""
        try:
            import smbus2
            self._smbus = smbus2.SMBus(self.bus)
            return True
        except ImportError:
            cloudlog.error("imud: smbus2 not available — install with: pip install smbus2")
            return False
        except Exception as e:
            cloudlog.error(f"imud: Failed to open I2C bus {self.bus}: {e}")
            return False

    def _read_reg(self, reg: int) -> int:
        """Read single byte from register."""
        if self._smbus is None:
            raise RuntimeError("I2C bus not open")
        return self._smbus.read_byte_data(self.I2C_ADDRESS, reg)

    def _write_reg(self, reg: int, value: int) -> None:
        """Write single byte to register."""
        if self._smbus is None:
            raise RuntimeError("I2C bus not open")
        self._smbus.write_byte_data(self.I2C_ADDRESS, reg, value)

    def _read_regs(self, reg: int, length: int) -> bytes:
        """Read multiple bytes starting at register (auto-increment)."""
        if self._smbus is None:
            raise RuntimeError("I2C bus not open")
        return bytes(self._smbus.read_i2c_block_data(self.I2C_ADDRESS, reg, length))

    def init(self) -> bool:
        """Initialize sensor."""
        try:
            if not self._open_bus():
                return False

            # Verify chip ID
            chip_id = self._read_reg(self.WHO_AM_I)
            expected_id = _LSM6DS3.get("CHIP_ID", 0x00)
            if chip_id != expected_id:
                cloudlog.warning(f"imud: Unexpected WHO_AM_I = 0x{chip_id:02X}, expected 0x{expected_id:02X}")
                # Continue anyway — some variants may differ

            # Soft reset
            self._write_reg(self.CTRL3_C, 0x01)
            time.sleep(0.01)

            # Configure accelerometer: 104 Hz, ±2g
            self._write_reg(self.CTRL1_XL, self.DEFAULT_CTRL1_XL)
            # Configure gyroscope: 104 Hz, ±250 dps
            self._write_reg(self.CTRL2_G, self.DEFAULT_CTRL2_G)
            # Enable auto-increment for multi-byte reads
            self._write_reg(self.CTRL3_C, self.DEFAULT_CTRL3_C)

            time.sleep(0.01)  # Wait for sensor to settle

            self._initialized = True
            cloudlog.info(f"imud: LSM6DS3 initialized on I2C bus {self.bus}")
            return True

        except Exception as e:
            cloudlog.error(f"imud: Failed to init LSM6DS3: {e}")
            self._smbus = None
            self._initialized = False
            return False

    def read(self) -> dict | None:
        """Read accelerometer and gyroscope data."""
        if not self._initialized or self._smbus is None:
            return None

        try:
            # Read temperature (2 bytes, signed)
            temp_raw = struct.unpack('<h', self._read_regs(self.OUT_TEMP_L, 2))[0]
            temperature = (temp_raw / self.TEMP_SCALE) + self.TEMP_OFFSET

            # Read gyroscope (6 bytes: X, Y, Z — each 2 bytes signed little-endian)
            gyro_data = self._read_regs(self.OUTX_L_G, 6)
            gyro_x, gyro_y, gyro_z = struct.unpack('<hhh', gyro_data)
            gyro = [
                gyro_x * self.GYRO_SCALE_250DPS / 1000.0,  # dps → rad/s
                gyro_y * self.GYRO_SCALE_250DPS / 1000.0,
                gyro_z * self.GYRO_SCALE_250DPS / 1000.0,
            ]

            # Read accelerometer (6 bytes: X, Y, Z — each 2 bytes signed little-endian)
            accel_data = self._read_regs(self.OUTX_L_XL, 6)
            accel_x, accel_y, accel_z = struct.unpack('<hhh', accel_data)
            accel = [
                accel_x * self.ACCEL_SCALE_2G / 1000.0 * 9.80665,  # mg → m/s²
                accel_y * self.ACCEL_SCALE_2G / 1000.0 * 9.80665,
                accel_z * self.ACCEL_SCALE_2G / 1000.0 * 9.80665,
            ]

            self.data_valid = True
            return {
                'acceleration': accel,
                'gyro': gyro,
                'temperature': temperature,
            }

        except Exception as e:
            self.data_valid = False
            cloudlog.error(f"imud: LSM6DS3 read error: {e}")
            return None

    def close(self) -> None:
        """Close I2C bus."""
        if self._smbus is not None:
            try:
                self._smbus.close()
            except Exception:
                pass
            self._smbus = None
        self._initialized = False


class ImuD:
    """IMU sensor daemon."""

    def __init__(self):
        set_daemon_affinity("imud")

        self.pm = messaging.PubMaster(['accelerometer', 'gyroscope', 'temperature'])

        self.sensors: list[ImuSensor] = []
        self._init_sensors()

        self.exit_event = threading.Event()
        self.threads: list[threading.Thread] = []

        cloudlog.info("imud: Initialized")

    def _init_sensors(self):
        """Initialize IMU sensors. Try IIO first, then smbus2 fallback."""
        # Try IIO kernel driver first (SDK Deep Study §1.9 recommendation)
        iio = IIOImu(device_name="lsm6ds3")
        if iio.init():
            self.sensors.append(iio)
            cloudlog.info("imud: LSM6DS3 initialized via IIO")
            return

        # Fallback to raw I2C/smbus2
        i2c_bus = 0
        try:
            hw = HARDWARE.get_hardware()
            if hasattr(hw, 'I2C') and 'SENSORS' in hw.I2C:
                i2c_bus = hw.I2C['SENSORS'].get('bus', 0)
        except Exception:
            pass  # Fallback to bus 0

        lsm = LSM6DS3(bus=i2c_bus)
        if lsm.init():
            self.sensors.append(lsm)
            cloudlog.info(f"imud: LSM6DS3 initialized on I2C bus {i2c_bus}")
        else:
            cloudlog.warning(f"imud: LSM6DS3 not found on I2C bus {i2c_bus}")

    def _polling_loop(self, sensor: ImuSensor, rate_hz: float):
        """Poll sensor at specified rate."""
        pm = messaging.PubMaster(['accelerometer', 'gyroscope', 'temperature'])
        rk = Ratekeeper(rate_hz, print_delay_threshold=None)

        while not self.exit_event.is_set():
            try:
                data = sensor.read()
                if data is None:
                    continue

                # Publish accelerometer (SensorEventData: acceleration is a SensorVec
                # union member — assigning it also selects the union, so the die
                # temperature goes out on the separate 'temperature' message below)
                msg = messaging.new_message('accelerometer', valid=True)
                msg.accelerometer.timestamp = msg.logMonoTime
                msg.accelerometer.acceleration.v = [float(v) for v in data['acceleration']]
                pm.send('accelerometer', msg)

                # Publish gyroscope
                msg = messaging.new_message('gyroscope', valid=True)
                msg.gyroscope.timestamp = msg.logMonoTime
                msg.gyroscope.gyro.v = [float(v) for v in data['gyro']]
                pm.send('gyroscope', msg)

                # Publish temperature
                msg = messaging.new_message('temperature', valid=True)
                msg.temperature.temperature = data['temperature']
                pm.send('temperature', msg)

            except Exception as e:
                cloudlog.error(f"imud: Sensor {sensor.name} error: {e}")

            rk.keep_time()

    def run(self):
        """Start polling threads."""
        # Start thread for each sensor
        for sensor in self.sensors:
            t = threading.Thread(
                target=self._polling_loop,
                args=(sensor, 100),  # 100Hz
                daemon=True
            )
            t.start()
            self.threads.append(t)
            cloudlog.info(f"imud: Started polling {sensor.name}")

        # Keep main thread alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """Stop daemon."""
        self.exit_event.set()
        for t in self.threads:
            t.join(timeout=1.0)
        # Close all sensor I2C connections
        for sensor in self.sensors:
            if isinstance(sensor, LSM6DS3):
                sensor.close()
        cloudlog.info("imud: Stopped")


def main() -> int:
    try:
        daemon = ImuD()
        daemon.run()
        return 0
    except KeyboardInterrupt:
        cloudlog.info("ImuD stopped")
        return 0
    except Exception as e:
        cloudlog.exception(f"ImuD fatal error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
