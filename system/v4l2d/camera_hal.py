"""Camera HAL — maps sensor names to V4L2 driver instances."""

from openpilot.system.v4l2d.drivers import OX03C10Driver, GC4653Driver, BaseCameraDriver


class CameraHAL:
  """Hardware abstraction layer for camera driver selection."""

  @staticmethod
  def open_camera(device_path: str, stream_type, cam_id: str,
                  sensor_name: str = "", width: int = 0, height: int = 0,
                  fps: int = 0, fourcc: str | None = None, **kwargs):
    """Instantiate the correct driver for the given sensor.

    Args:
      device_path: V4L2 device node (e.g. /dev/video0)
      stream_type: VisionIPC stream type
      cam_id: camera identifier string
      sensor_name: "ox03c10", "gc4653", "uvc", or empty for fallback
      width: override capture width (0 = driver default)
      height: override capture height (0 = driver default)
      fps: override framerate (0 = driver default)
      fourcc: OpenCV FOURCC string, e.g. "MJPG" (default None)
      **kwargs: extra driver-specific args

    Returns:
      BaseCameraDriver subclass instance (already opened)
    """
    name = (sensor_name or "").lower()
    cam_id_lower = cam_id.lower()

    drv: BaseCameraDriver
    if name == "ox03c10":
      drv = OX03C10Driver(device_path, width=width, height=height, fps=fps)
    elif name == "gc4653":
      # stereo_left drives exposure (master); stereo_right follows (slave)
      is_master = kwargs.get("is_master", "left" in cam_id_lower)
      i2c_addr = kwargs.get(
        "i2c_addr",
        GC4653Driver.I2C_ADDR_MASTER if is_master else GC4653Driver.I2C_ADDR_SLAVE,
      )
      drv = GC4653Driver(
        device_path,
        width=width, height=height, fps=fps,
        i2c_bus=kwargs.get("i2c_bus", -1),
        i2c_addr=i2c_addr,
        is_master=is_master,
      )
    else:
      # UVC / generic V4L2
      drv = BaseCameraDriver(
        device_path, width=width, height=height, fps=fps, fourcc=fourcc
      )

    if not drv.open():
      raise RuntimeError(f"CameraHAL: failed to open {device_path} ({cam_id})")
    return drv
