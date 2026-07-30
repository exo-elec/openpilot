"""
Camera Source Switcher — full-screen rear / side camera replacement.

ExoPilot 01M/02:
  - Reverse gear  → replace main view with rear camera (full screen)
  - Left blinker  → replace main view with side_left camera (full screen)
  - Right blinker → replace main view with side_right camera (full screen)

The UVC streams come from the "uvcd" VisionIPC server instead of "v4l2d".
"""

import pyray as rl
from msgq.visionipc import VisionStreamType
from openpilot.selfdrive.ui.onroad.cameraview import CameraView
from openpilot.selfdrive.ui.ui_state import ui_state

REAR_STREAM   = VisionStreamType.VISION_STREAM_REAR
SIDE_L_STREAM = VisionStreamType.VISION_STREAM_SIDE_LEFT
SIDE_R_STREAM = VisionStreamType.VISION_STREAM_SIDE_RIGHT


class CameraSourceSwitcher:
  """Decides when to replace the road camera with a UVC camera feed."""

  def __init__(self):
    self._uvc_camera = CameraView("uvcd", REAR_STREAM)
    self._uvc_camera._set_placeholder_color(rl.Color(0, 0, 0, 180))
    self._active_stream: VisionStreamType | None = None

  def close(self) -> None:
    self._uvc_camera.close()

  def should_show_uvc(self) -> bool:
    """Return True if reverse or a SINGLE turn signal is active.

    Hazard lights (both blinkers) keep the normal road camera.
    """
    sm = ui_state.sm
    if sm.recv_frame["carState"] < ui_state.started_frame:
      return False

    car_state = sm["carState"]

    # Reverse gear detection (CarState lives in car.capnp, not log)
    from cereal import car
    if car_state.gearShifter == car.CarState.GearShifter.reverse:
      return True

    # Single turn signal only — hazards (both on) keep road camera
    return (car_state.leftBlinker != car_state.rightBlinker)

  def get_uvc_camera(self) -> CameraView:
    """Return the UVC CameraView, switching stream if needed.

    Priority:
      1. Reverse gear      → rear camera (highest)
      2. Left blinker only → side_left camera
      3. Right blinker only→ side_right camera
      4. Both blinkers     → rear camera (hazard fallback)
    """
    sm = ui_state.sm
    target = REAR_STREAM

    if sm.recv_frame["carState"] >= ui_state.started_frame:
      car_state = sm["carState"]

      from cereal import car
      is_reverse = car_state.gearShifter == car.CarState.GearShifter.reverse

      if is_reverse:
        target = REAR_STREAM
      elif car_state.leftBlinker and not car_state.rightBlinker:
        target = SIDE_L_STREAM
      elif car_state.rightBlinker and not car_state.leftBlinker:
        target = SIDE_R_STREAM
      else:
        # Hazards or no signal — keep road camera (should not reach here
        # because should_show_uvc() guards this path, but fallback to rear)
        target = REAR_STREAM

    if self._active_stream != target:
      self._uvc_camera.switch_stream(target)
      self._active_stream = target

    return self._uvc_camera
