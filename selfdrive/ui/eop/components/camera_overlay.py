"""Full-screen camera overlays and the rule for which one is up.

Behaviour is settled (plan section 5.7, implemented in C++ on dev/01M):

  single blinker  -> that side's camera, full screen
  both blinkers   -> nothing
  hazards are not an intent to move sideways
  reverse         -> rear camera, outranking any blinker

A full-screen image carries no chrome saying which camera it is, so a bar
blinks on the edge that camera looks out of. And because a full-screen overlay
covers the blind-spot bands completely, the warning moves onto the overlay's
own border while a side is occupied.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from openpilot.selfdrive.ui.eop.components.blind_spot import (
  CLEAR,
  WARNING,
  BlindSpotSeverity,
)
from openpilot.selfdrive.ui.eop.components.border_overlay import (
  BorderOverlay,
  Mode,
  Side,
)
from openpilot.selfdrive.ui.eop.qt import Qt, QColor, QPainter, QPen, QWidget
from openpilot.selfdrive.ui.eop.state import Snapshot


class Camera(Enum):
  NONE = "none"
  LEFT = "side_left"
  RIGHT = "side_right"
  REAR = "rear"


# The side and rear cameras are ordinary UVC devices published by uvcd, not
# the ISP pipeline camerad drives -- so both the publisher and the stream
# differ from the road camera. Same pairing the C++ overlay used.
OVERLAY_PUBLISHER = "uvcd"
STREAMS = {
  Camera.LEFT: "VISION_STREAM_SIDE_LEFT",
  Camera.RIGHT: "VISION_STREAM_SIDE_RIGHT",
  Camera.REAR: "VISION_STREAM_REAR",
}

CORNER_RADIUS = 12


# Resting border for a side overlay; replaced by the blind-spot colour while
# that side is occupied.
IDLE_BORDER = QColor(0, 200, 255, 220)
IDLE_BORDER_PX = 3
WARN_BORDER_PX = 12


def active_camera(snap: Snapshot) -> Camera:
  """Which camera the driver should be looking at, if any.

  Reverse outranks a blinker rather than fighting it for the same screen: if
  the car is moving backwards, the rear view is the one that matters.
  """
  if snap.in_reverse:
    return Camera.REAR
  if snap.hazards:
    return Camera.NONE
  if snap.left_blinker:
    return Camera.LEFT
  if snap.right_blinker:
    return Camera.RIGHT
  return Camera.NONE


SOURCE_EDGE = {
  Camera.LEFT: Side.LEFT,
  Camera.RIGHT: Side.RIGHT,
  Camera.REAR: Side.BOTTOM,
}


@dataclass(frozen=True)
class OverlayStyle:
  """How the active overlay should be drawn, derived from severity."""
  border: QColor
  border_px: int


def overlay_style(camera: Camera, bs: BlindSpotSeverity) -> OverlayStyle:
  level = CLEAR
  if camera is Camera.LEFT:
    level = bs.left
  elif camera is Camera.RIGHT:
    level = bs.right
  if level <= CLEAR:
    return OverlayStyle(IDLE_BORDER, IDLE_BORDER_PX)
  # Full screen turns a 3px frame into a hairline around a large image, so it
  # widens when carrying a warning rather than just identifying the camera.
  return OverlayStyle(QColor(_hex(level)), WARN_BORDER_PX)


def _hex(level: int) -> str:
  return "#ff3c3c" if level >= WARNING else "#ffc200"


def _default_view(stream_name: str, publisher: str, parent: QWidget):
  """Deferred so importing this module does not pull in VisionIPC."""
  from openpilot.selfdrive.ui.eop.components.camera_view import create_camera_view
  return create_camera_view(stream_name, publisher, parent)


class CameraOverlay(QWidget):
  """One full-screen camera view with a border and a blinking source edge.

  The frame source is a CPU VisionIPC view, deliberately -- see
  create_camera_view(). These overlays appear and disappear mid-drive, and
  EGLFS permits one fullscreen GL surface, so a second QOpenGLWidget showing
  up when the driver signals is exactly the case that terminates the
  application.

  The stream is only polled while the overlay is visible. Holding a VisionIPC
  connection open for three cameras that are off screen most of the time
  costs a conversion per frame per camera for nothing.
  """

  def __init__(self, camera: Camera, parent: QWidget | None = None,
               view_factory=None):
    super().__init__(parent)
    self.camera = camera
    self._border = IDLE_BORDER
    self._border_px = IDLE_BORDER_PX
    self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    factory = view_factory if view_factory is not None else _default_view
    self.view = factory(STREAMS[camera], OVERLAY_PUBLISHER, self)
    self.view.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    side = SOURCE_EDGE.get(camera)
    self._edge = BorderOverlay(side, IDLE_BORDER, Mode.BLINK, parent=self) if side else None
    if self._edge:
      self._edge.raise_()
      self._edge.set_enabled(True)
    self.hide()

  def poll(self) -> None:
    """Pull the newest frame. Called only while this overlay is up."""
    poll = getattr(self.view, "poll", None)
    if poll is not None:
      poll()

  def apply_style(self, style: OverlayStyle) -> None:
    self._border = style.border
    self._border_px = style.border_px
    if self._edge:
      # The bar takes the border colour, so it says two things at once: which
      # camera this is, and whether there is something in that blind spot.
      self._edge.set_color(style.border)
    self.update()

  def paintEvent(self, event) -> None:
    """Only the border. The camera view underneath paints the image itself."""
    if self._border_px <= 0:
      return
    p = QPainter(self)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(self._border)
    pen.setWidth(self._border_px)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    # Inset by half the pen width so the stroke lands inside the widget
    # rather than being clipped down the middle at the edges.
    half = self._border_px // 2
    p.drawRoundedRect(self.rect().adjusted(half, half, -half, -half),
                      CORNER_RADIUS, CORNER_RADIUS)

  def _layout(self) -> None:
    self.view.setGeometry(self.rect())
    if self._edge:
      side = SOURCE_EDGE[self.camera]
      bar = 14
      if side is Side.BOTTOM:
        self._edge.setGeometry(0, self.height() - bar, self.width(), bar)
      elif side is Side.LEFT:
        self._edge.setGeometry(0, 0, bar, self.height())
      else:
        self._edge.setGeometry(self.width() - bar, 0, bar, self.height())
      self._edge.raise_()

  def resizeEvent(self, event):
    super().resizeEvent(event)
    self._layout()

  def showEvent(self, event):
    super().showEvent(event)
    self._layout()


class CameraOverlayStack(QWidget):
  """Holds the three overlays and shows at most one."""

  def __init__(self, parent: QWidget | None = None, view_factory=None):
    super().__init__(parent)
    self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    self._overlays = {c: CameraOverlay(c, self, view_factory) for c in
                      (Camera.LEFT, Camera.RIGHT, Camera.REAR)}
    self._active = Camera.NONE

  @property
  def active(self) -> Camera:
    return self._active

  def set_snapshot(self, snap: Snapshot) -> None:
    want = active_camera(snap)
    if want is not self._active:
      for cam, ov in self._overlays.items():
        ov.setVisible(cam is want)
      self._active = want
    if want is not Camera.NONE:
      ov = self._overlays[want]
      ov.apply_style(overlay_style(want, snap.blind_spot))
      ov.raise_()
      # Polled here rather than on a timer of its own: this runs at the UI
      # tick, and only the one overlay that is actually on screen is pulled.
      ov.poll()

  def _layout(self) -> None:
    for ov in self._overlays.values():
      ov.setGeometry(self.rect())

  def resizeEvent(self, event):
    super().resizeEvent(event)
    self._layout()

  def showEvent(self, event):
    super().showEvent(event)
    self._layout()
