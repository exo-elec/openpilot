"""
RK3576 (ExoPilot 02M) USB/UVC camera pipeline configuration.

ExoPilot 02M provides 3 USB-UVC cameras alongside its 5-camera MIPI CSI array
(mono_narrow, mono_wide, mono_tele, stereo_left, stereo_right — configured in
system.v4l2d, not here):
  - side_left  : 120° FOV UVC camera (blind spot / lane change assist)
  - side_right : 120° FOV UVC camera (blind spot / lane change assist)
  - rear_camera: 170° FOV UVC camera (backup / reverse view)

Same shape as system/hardware/rk3588/camera_config.py (see
system/hardware/camera_types.py for the shared dataclass/enum), sourced from
a different hal module (hal.platform.rk3576_camera_geometry) since 02M's
mounting geometry differs from 01M's.
"""

from openpilot.system.hardware.camera_types import CameraConfig, CameraSensor, HDRMode, find_camera

# USB cameras via hub (ExoPilot 02M). Physical mounting data (y_offset_mm,
# fov_deg, lens_type, v4l2/i2c wiring) ships from the closed exopilot hal
# package (hal.platform.rk3576_camera_geometry) rather than living in this
# public repo. Without hal, this list is empty and side/rear camera support
# degrades gracefully (see HardwareBase.has_side_cameras/has_rear_camera).
USB_CAMERAS: list[CameraConfig]
try:
  from hal.platform.rk3576_camera_geometry import USB_CAMERAS as _HAL_USB_CAMERAS
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
  return find_camera(USB_CAMERAS, name)
