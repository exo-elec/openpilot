"""
RK3588 (ExoPilot 01M) AHD camera pipeline configuration.

ExoPilot 01M hardware revision adds a USB 3.0 hub providing 4× USB 2.0 ports:
  - TC275 bootloader (USB-UART / USB-CAN adapter)
  - side_left  : 120° FOV AHD camera (blind spot / lane change assist)
  - side_right : 120° FOV AHD camera (blind spot / lane change assist)
  - rear_camera: 170° FOV AHD camera (backup / reverse view)

Cameras accessed via UVC/USB interface.

The 4× MIPI CSI cameras (road, wide_road, stereo_left, stereo_right) are
configured in system.v4l2d and are unchanged by this addition.
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


# USB cameras via hub (ExoPilot 01M hardware revision). Physical mounting data
# (y_offset_mm, fov_deg, lens_type, v4l2/i2c wiring) ships from the closed
# exopilot hal package (hal.platform.rk3588_camera_geometry) rather than living
# in this public repo. Without hal, this list is empty and side/rear camera
# support degrades gracefully (see HardwareBase.has_side_cameras/has_rear_camera).
USB_CAMERAS: list[CameraConfig]
try:
  from hal.platform.rk3588_camera_geometry import USB_CAMERAS as _HAL_USB_CAMERAS
  USB_CAMERAS = [
    CameraConfig(
      name=c["name"],
      sensor=CameraSensor.UVC,
      width=c["width"],
      height=c["height"],
      fps=c["fps"],
      hdr=HDRMode.SDR,
      fov_deg=c["fov_deg"],
      y_offset_mm=c["y_offset_mm"],
      v4l2_subdev="",
      v4l2_mainpath=c["v4l2_mainpath"],
      sensor_i2c_addr=c["sensor_i2c_addr"],
      orientation=c["orientation"],
      lens_type=c["lens_type"],
    )
    for c in _HAL_USB_CAMERAS
  ]
except ImportError:
  USB_CAMERAS = []


def get_camera(name: str) -> CameraConfig | None:
  """Return USB camera configuration by name."""
  for cam in USB_CAMERAS:
    if cam.name == name:
      return cam
  return None
