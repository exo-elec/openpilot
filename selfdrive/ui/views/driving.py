"""01M full-screen driving view with shared 02M dark chrome, HUD and alerts.

No floating side panels are installed on the 1024 x 600 baseline.
"""

from __future__ import annotations

from openpilot.selfdrive.ui.components.alerts import AlertBanner
from openpilot.selfdrive.ui.components.bev import BevOverlay
from openpilot.selfdrive.ui.components.voice_popup import VoicePopup
from openpilot.selfdrive.ui.components.blind_spot import BlindSpotBands
from openpilot.selfdrive.ui.components.camera_overlay import CameraOverlayStack
from openpilot.selfdrive.ui.components.camera_view import create_camera_view
from openpilot.selfdrive.ui.components.hud import HudOverlay
from openpilot.selfdrive.ui.components.model_renderer import ModelRenderer
from openpilot.selfdrive.ui.components.chrome import BAR_H, TopBar, BottomBar
from openpilot.selfdrive.ui.qt import (
  QColor,
  QPainter,
  Qt,
  QtWidgets,
  QWidget,
)
from openpilot.selfdrive.ui.state import ModelFrame, Snapshot

PAIRING_KEYS = ("EOPBluetoothPairingPin", "EOPBluetoothPairingActive")
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
  """Full camera with shared dark bars, basic HUD and prioritized alerts."""

  def __init__(self, live_camera: bool = True, store=None, parent=None):
    super().__init__(parent)
    self.setObjectName("onroadRoot")
    self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    self._store = store

    self.camera = create_camera_view(parent=self) if live_camera else PlaceholderCamera(self)
    self.model = ModelRenderer(self)
    self.bev = BevOverlay(self)
    self.bands = BlindSpotBands(self)
    self.hud = HudOverlay(self)
    self.top = TopBar(self)
    self.bottom = BottomBar(self)
    self.overlays = CameraOverlayStack(self)

    self.pairing = QtWidgets.QLabel(self)
    self.pairing.setAlignment(Qt.AlignCenter)
    self.pairing.setStyleSheet(PAIRING_STYLE)
    # A status label, not a control: it must not swallow taps meant for what
    # is underneath it.
    self.pairing.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    self.pairing.hide()

    self.voice_popup = VoicePopup(self)
    self.alert = AlertBanner(self)

    for w in (self.model, self.bev, self.bands, self.hud, self.top, self.bottom, self.overlays,
              self.pairing, self.voice_popup, self.alert):
      w.raise_()

    self._pairing_state = dict.fromkeys(PAIRING_KEYS, "")
    from openpilot.selfdrive.ui.qt import QTimer
    self._pairing_timer = QTimer(self)
    self._pairing_timer.setInterval(PAIRING_POLL_MS)
    self._pairing_timer.timeout.connect(self._refresh_pairing)

  # ---- state ------------------------------------------------------------

  def set_snapshot(self, snap: Snapshot) -> None:
    self.voice_popup.set_snapshot(snap)
    self.voice_popup.raise_()
    self.alert.raise_()
    self.bands.set_severity(snap.blind_spot)
    # A full-screen side camera covers the bands entirely, which is exactly
    # when the driver most needs the warning -- signalling toward a car that
    # is already alongside. CameraOverlayStack carries it on the overlay's own
    # border from the same fused severity, so the two can never disagree.
    self.overlays.set_snapshot(snap)
    self.hud.set_snapshot(snap)
    self.bev.snap = snap
    self.bev.setVisible(snap.bev_enabled)
    self.model.setVisible(not snap.bev_enabled)
    self.alert.set_alert(snap.alert_text1, snap.alert_text2,
                         snap.alert_severity, snap.alert_size)

    self.top.status = snap.status.value
    self.top.temp_c = snap.cpu_temp
    self.top.update()
    self.bottom.left_text = f"{snap.speed_display:.0f} {snap.speed_unit}"
    self.bottom.right_text = f"cruise {snap.set_speed:.0f}" if snap.cruise_set else "cruise --"
    self.bottom.update()


  def set_model_frame(self, frame: ModelFrame | None, snap: Snapshot) -> None:
    self.model.set_frame(frame, snap)
    self.bev.set_frame(frame, snap)

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

    pin = self._pairing_state["EOPBluetoothPairingPin"]
    if self._pairing_state["EOPBluetoothPairingActive"] == "1" and pin:
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
    # Projection uses the entire camera surface on both branches.
    for widget in (self.camera, self.model, self.bands, self.overlays):
      widget.setGeometry(self.rect())
    self.bev.setGeometry(0, BAR_H, w, max(0, h - 2 * BAR_H))
    self.hud.setGeometry(0, BAR_H, w, max(0, h - 2 * BAR_H))
    self.top.setGeometry(0, 0, w, BAR_H)
    self.bottom.setGeometry(0, h - BAR_H, w, BAR_H)
    self.voice_popup.center_in_parent()
    self.alert.setGeometry(self.rect())
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
    p = QPainter(self)
    p.fillRect(self.rect(), QColor(0, 0, 0))
