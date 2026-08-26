"""Shared USB/UVC camera config types, used by every Rockchip platform's
own `camera_config.py` (currently `rk3588/camera_config.py`,
`rk3576/camera_config.py`) so the dataclass/enum shapes don't drift between
platforms — only each platform's `USB_CAMERAS` list (sourced from that
platform's `hal.platform.<soc>_camera_geometry`) differs.
"""

from dataclasses import dataclass
from enum import Enum


class CameraSensor(Enum):
  UVC = "uvc"  # USB Video Class interface (used for AHD cameras converted to USB)


class HDRMode(Enum):
  SDR = "sdr"


@dataclass(frozen=True)
class CameraConfig:
  name: str
  sensor: CameraSensor
  width: int
  height: int
  fps: int
  hdr: HDRMode
  fov_deg: float
  y_offset_mm: float         # lateral offset from vehicle centerline (+ = left)
  v4l2_subdev: str           # empty for UVC
  v4l2_mainpath: str         # e.g. "/dev/video-rear"
  sensor_i2c_addr: int       # 0 for UVC
  orientation: str           # "left", "right", "rear"
  lens_type: str             # e.g. "ahd_120deg"


def find_camera(cameras: list[CameraConfig], name: str) -> CameraConfig | None:
  """Return the camera config matching `name`, or None."""
  for cam in cameras:
    if cam.name == name:
      return cam
  return None
