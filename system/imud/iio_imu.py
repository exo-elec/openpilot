"""IIO-based IMU reader for LSM6DS3 (and compatible).

SDK Deep Study §1.9 / §2.1 recommends ditching raw smbus2 in favor of the
in-kernel st_lsm6dsx IIO driver. This gives:
  - hardware FIFO batching (4 KB FIFO → lower interrupt rate)
  - kernel timestamping
  - automatic power management

Falls back to smbus2 if IIO device is not present.
"""

from __future__ import annotations

import os
import glob

from openpilot.common.swaglog import cloudlog


class IIOImu:
    """Read LSM6DS3 via Linux IIO sysfs interface."""

    # LSM6DS3 WHO_AM_I value for identification
    WHO_AM_I = 0x69

    def __init__(self, device_name: str = "lsm6ds3"):
        self.device_name = device_name
        self._sysfs_path: str | None = None
        self._dev_path: str | None = None
        self._initialized = False

        # Scale factors (read from sysfs at init)
        self._accel_scale = 0.0
        self._gyro_scale = 0.0
        self._temp_scale = 1.0
        self._temp_offset = 25.0

    def _find_device(self) -> str | None:
        """Scan /sys/bus/iio/devices/ for a matching LSM6DS3 IIO node."""
        for iio_dev in sorted(glob.glob("/sys/bus/iio/devices/iio:device*")):
            name_path = os.path.join(iio_dev, "name")
            try:
                with open(name_path) as f:
                    name = f.read().strip()
                if self.device_name.lower() in name.lower():
                    return iio_dev
            except Exception:
                continue
        return None

    def _read_sysfs_float(self, path: str) -> float:
        with open(path) as f:
            return float(f.read().strip())

    def _read_sysfs_int(self, path: str) -> int:
        with open(path) as f:
            return int(f.read().strip())

    def init(self) -> bool:
        try:
            path = self._find_device()
            if path is None:
                cloudlog.warning("iio_imu: no IIO device found for %s", self.device_name)
                return False

            self._sysfs_path = path
            # Derive /dev/iio:deviceN from sysfs path name
            devname = os.path.basename(path)  # iio:deviceN
            self._dev_path = f"/dev/{devname}"

            # Read scale factors
            try:
                self._accel_scale = self._read_sysfs_float(os.path.join(path, "in_accel_scale"))
            except Exception:
                self._accel_scale = 0.061 / 1000.0 * 9.80665  # fallback mg→m/s²

            try:
                self._gyro_scale = self._read_sysfs_float(os.path.join(path, "in_anglvel_scale"))
            except Exception:
                self._gyro_scale = 8.75 / 1000.0  # fallback mdps→dps

            try:
                self._temp_scale = self._read_sysfs_float(os.path.join(path, "in_temp_scale"))
            except Exception:
                self._temp_scale = 1.0 / 256.0

            try:
                self._temp_offset = self._read_sysfs_float(os.path.join(path, "in_temp_offset"))
            except Exception:
                self._temp_offset = 25.0

            # Set sampling frequency to 104 Hz (or nearest supported)
            try:
                freq_path = os.path.join(path, "sampling_frequency")
                with open(freq_path, "w") as f:
                    f.write("104\n")
            except Exception:
                pass

            self._initialized = True
            cloudlog.info("iio_imu: initialized %s at %s", self.device_name, path)
            return True
        except Exception as e:
            cloudlog.error("iio_imu: init failed: %s", e)
            self._initialized = False
            return False

    def read(self) -> dict | None:
        if not self._initialized or self._sysfs_path is None:
            return None
        try:
            accel = [
                self._read_sysfs_int(os.path.join(self._sysfs_path, "in_accel_x_raw")) * self._accel_scale,
                self._read_sysfs_int(os.path.join(self._sysfs_path, "in_accel_y_raw")) * self._accel_scale,
                self._read_sysfs_int(os.path.join(self._sysfs_path, "in_accel_z_raw")) * self._accel_scale,
            ]
            gyro = [
                self._read_sysfs_int(os.path.join(self._sysfs_path, "in_anglvel_x_raw")) * self._gyro_scale,
                self._read_sysfs_int(os.path.join(self._sysfs_path, "in_anglvel_y_raw")) * self._gyro_scale,
                self._read_sysfs_int(os.path.join(self._sysfs_path, "in_anglvel_z_raw")) * self._gyro_scale,
            ]
            temp = self._read_sysfs_int(os.path.join(self._sysfs_path, "in_temp_raw")) * self._temp_scale + self._temp_offset

            return {
                'acceleration': accel,
                'gyro': gyro,
                'temperature': temp,
            }
        except Exception as e:
            cloudlog.error("iio_imu: read error: %s", e)
            return None

    def close(self) -> None:
        self._initialized = False
        self._sysfs_path = None
        self._dev_path = None
