"""The classic 01M driving screen.

Layered, bottom to top:

  border          status-coloured frame, painted by this widget itself
  camera          VisionIPC road camera, inset by the border
  model path      lane lines, path and lead markers
  blind-spot      edge bands for any occupied blind spot
  HUD             speeds, speed limit, driver pill, maneuver card
  camera overlay  full-screen side/rear on a single blinker or reverse
  pairing PIN     shown only while Bluetooth pairing is open
  alert           always last -- a full-screen camera must never bury one

Same composition as onroad_home.cc, and the same stacking rule: the alert
outranks everything, because the one thing that must never be hidden is the
car telling the driver to take over.

Geometry is set explicitly rather than by a QLayout. Most of these layers
cover rather than occupy, which is not something a layout expresses.
"""

from __future__ import annotations

from openpilot.selfdrive.ui.eop.components.alerts import AlertBanner
from openpilot.selfdrive.ui.eop.components.blind_spot import BlindSpotBands
from openpilot.selfdrive.ui.eop.components.camera_overlay import CameraOverlayStack
from openpilot.selfdrive.ui.eop.components.camera_view import create_camera_view
from openpilot.selfdrive.ui.eop.components.hud import HudOverlay
from openpilot.selfdrive.ui.eop.components.model_renderer import ModelRenderer
from openpilot.selfdrive.ui.eop.components.theme import (
  STATUS_COLORS,
  UI_BORDER_SIZE,
)
from openpilot.selfdrive.ui.eop.qt import (
  QColor,
  QPainter,
  QRect,
  Qt,
  QtWidgets,
  QWidget,
)
from openpilot.selfdrive.ui.eop.state import ModelFrame, Snapshot, UIStatus

PAIRING_KEYS = ("BluetoothPairingPin", "BluetoothPairingActive")
PAIRING_POLL_MS = 2000

PAIRING_STYLE = """
QLabel {
  color: #ffcc00;
  background-color: rgba(0, 0, 0, 180);
  border-radius: 12px;
  padding: 12px 24px;
  font-size: 32px;
  font-weight: bold;
}
"""


class PlaceholderCamera(QWidget):
  """Stands in for VisionIPC when there is none, e.g. --demo. Labelled rather
  than blank: an empty widget reads as a rendering bug."""

  def __init__(self, parent=None):
    super().__init__(parent)
    self.setAttribute(Qt.WA_OpaquePaintEvent, True)

  def paintEvent(self, event):
    p = QPainter(self)
    p.fillRect(self.rect(), QColor(0, 0, 0))
    p.setPen(QColor(60, 72, 74))
    p.drawText(self.rect(), Qt.AlignCenter, "camera: demo mode")

  def poll(self):
    pass


class OnroadView(QWidget):
  """Driving screen: camera, path, HUD and alerts inside a status border."""

  def __init__(self, live_camera: bool = True, store=None, parent=None):
    super().__init__(parent)
    self.setObjectName("onroadRoot")
    self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    self._store = store
    self._border = STATUS_COLORS[UIStatus.DISENGAGED]

    self.camera = create_camera_view(parent=self) if live_camera else PlaceholderCamera(self)
    self.model = ModelRenderer(self)
    self.bands = BlindSpotBands(self)
    self.hud = HudOverlay(self)
    self.overlays = CameraOverlayStack(self)

    self.pairing = QtWidgets.QLabel(self)
    self.pairing.setAlignment(Qt.AlignCenter)
    self.pairing.setStyleSheet(PAIRING_STYLE)
    # A status label, not a control: it must not swallow taps meant for what
    # is underneath it.
    self.pairing.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    self.pairing.hide()

    self.alert = AlertBanner(self)

    for w in (self.model, self.bands, self.hud, self.overlays,
              self.pairing, self.alert):
      w.raise_()

    self._pairing_state = dict.fromkeys(PAIRING_KEYS, "")
    from openpilot.selfdrive.ui.eop.qt import QTimer
    self._pairing_timer = QTimer(self)
    self._pairing_timer.setInterval(PAIRING_POLL_MS)
    self._pairing_timer.timeout.connect(self._refresh_pairing)

  # ---- state ------------------------------------------------------------

  def set_snapshot(self, snap: Snapshot) -> None:
    self.bands.set_severity(snap.blind_spot)
    # A full-screen side camera covers the bands entirely, which is exactly
    # when the driver most needs the warning -- signalling toward a car that
    # is already alongside. CameraOverlayStack carries it on the overlay's own
    # border from the same fused severity, so the two can never disagree.
    self.overlays.set_snapshot(snap)
    self.hud.set_snapshot(snap)
    self.alert.set_alert(snap.alert_text1, snap.alert_text2,
                         snap.alert_severity, snap.alert_size)

    border = STATUS_COLORS.get(snap.status, STATUS_COLORS[UIStatus.DISENGAGED])
    if border != self._border:
      self._border = border
      self.update()


  def set_model_frame(self, frame: ModelFrame | None, snap: Snapshot) -> None:
    self.model.set_frame(frame, snap)

  def camera_size(self) -> tuple[int, int]:
    """The surface the model path must be projected for."""
    return self.camera.width(), self.camera.height()

  def poll_camera(self) -> None:
    poll = getattr(self.camera, "poll", None)
    if poll is not None:
      poll()

  def _refresh_pairing(self) -> None:
    if self._store is None:
      return
    for key in PAIRING_KEYS:
      self._pairing_state[key] = self._store.get_text(key)

    pin = self._pairing_state["BluetoothPairingPin"]
    if self._pairing_state["BluetoothPairingActive"] == "1" and pin:
      self.pairing.setText(f"PIN: {pin}")
      self.pairing.adjustSize()
      self.pairing.move(self.width() - self.pairing.width() - 30, 30)
      self.pairing.show()
      self.pairing.raise_()
      self.alert.raise_()
    else:
      self.pairing.hide()

  # ---- layout -----------------------------------------------------------

  def _layout(self) -> None:
    w, h = self.width(), self.height()
    inner = QRect(UI_BORDER_SIZE, UI_BORDER_SIZE,
                  max(0, w - UI_BORDER_SIZE * 2), max(0, h - UI_BORDER_SIZE * 2))

    # Everything that belongs to the camera image is inset by the border, so
    # the coloured frame stays visible around all of it.
    for widget in (self.camera, self.model, self.bands, self.hud, self.overlays):
      widget.setGeometry(inner)
    self.alert.setGeometry(inner)
    self._refresh_pairing()

  def resizeEvent(self, event):
    super().resizeEvent(event)
    self._layout()

  def showEvent(self, event):
    # Qt queues resize events for a widget that has never been shown, so a
    # parent that sets geometry once and then shows would leave every layer
    # at its default size.
    super().showEvent(event)
    self._layout()
    self._refresh_pairing()
    self._pairing_timer.start()

  def hideEvent(self, event):
    super().hideEvent(event)
    self._pairing_timer.stop()

  # ---- painting ---------------------------------------------------------

  def paintEvent(self, event):
    # Only the border is painted here; the camera covers the rest opaquely.
    p = QPainter(self)
    p.fillRect(self.rect(), QColor(self._border.red(), self._border.green(),
                                   self._border.blue(), 255))
