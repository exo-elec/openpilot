"""Onroad view.

Layered, bottom to top:

  camera            VisionIPC surface (section 4.3)
  model path        lane lines, path and lead markers
  blind-spot bands  edge gradients (section 5.7)
  panels            two swipeable side panels (section 5.6)
  chrome            50px top and bottom bars (section 5.4)
  camera overlay    full-screen side/rear on blinker or reverse (section 5.7)
  alert             always last -- a full-screen camera must never bury one

Laid out by explicit geometry rather than a QLayout: most of these cover
rather than occupy, which is not something a layout expresses.
"""

from __future__ import annotations

from openpilot.selfdrive.ui.components.blind_spot import BlindSpotBands
from openpilot.selfdrive.ui.components.camera_overlay import CameraOverlayStack
from openpilot.selfdrive.ui.components.camera_view import create_camera_view
from openpilot.selfdrive.ui.components.model_renderer import ModelRenderer
from openpilot.selfdrive.ui.components.chrome import (
  BAR_H,
  AlertOverlay,
  BottomBar,
  TopBar,
)
from openpilot.selfdrive.ui.components.panels import PanelData, PanelHost
from openpilot.selfdrive.ui.components.warnings import (
  AdasWarning,
  WarningOverlay,
  blocks_engagement,
)
from openpilot.selfdrive.ui.qt import Qt, QColor, QPainter, QWidget
from openpilot.selfdrive.ui.state import ModelFrame, Snapshot

# 1600x600, split 50 / 500 / 50 (plan section 5.4).
PANEL_W, PANEL_H = 1600, 600

MS_TO_KPH = 3.6


class PlaceholderCamera(QWidget):
  """Used when VisionIPC is unavailable, e.g. `--demo`. Labelled rather than
  blank: an empty widget reads as a rendering bug."""

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
  def __init__(self, live_camera: bool = True, parent=None):
    super().__init__(parent)
    self.setObjectName("onroadRoot")

    self.camera = create_camera_view(parent=self) if live_camera else PlaceholderCamera(self)
    self.model = ModelRenderer(self)
    self.bands = BlindSpotBands(self)
    self.panels = PanelHost(self)
    self.top = TopBar(self)
    self.bottom = BottomBar(self)
    self.overlays = CameraOverlayStack(self)
    self.warnings = WarningOverlay(self)
    self.alert = AlertOverlay(self)
    self.alert.hide()

    # Alert last: a full-screen camera must never bury one, and a warning
    # card must never bury an alert.
    for w in (self.model, self.bands, self.panels, self.top, self.bottom,
              self.overlays, self.warnings, self.alert):
      w.raise_()

  # ---- state ------------------------------------------------------------

  def set_snapshot(self, snap: Snapshot) -> None:
    self.bands.set_severity(snap.blind_spot)
    self.overlays.set_snapshot(snap)

    self.top.status = snap.status.value
    self.top.temp_c = snap.cpu_temp
    self.top.update()

    self.bottom.left_text = f"{snap.v_ego * MS_TO_KPH:.0f} km/h"
    self.bottom.right_text = (f"cruise {snap.cruise_kph:.0f}"
                              if snap.cruise_kph > 0 else "cruise --")
    self.bottom.update()

    active = [w for w in (AdasWarning.from_key(k) for k in snap.warnings) if w]
    self.warnings.set_active(active)

    critical = snap.alert_severity == "critical"
    self.alert.set_alert(snap.alert_text1, snap.alert_text2, snap.alert_severity)
    # A safety warning freezes panel cycling: a swipe should not be able to
    # page away from what the car is trying to say (section 5.6).
    self.panels.set_blocked(critical or blocks_engagement(active))

    self.panels.set_data(PanelData({
      "v_ego": snap.v_ego,
      "steering_angle": snap.steering_angle,
      "cruise_kph": snap.cruise_kph,
      "gear": snap.gear,
      "engaged": snap.status.value == "engaged",
      "blinker": ("left" if snap.left_blinker else
                  "right" if snap.right_blinker else "none"),
      "lead_valid": snap.lead_valid,
      "lead_d": snap.lead_d,
      "status": snap.status.value,
      "bearing": snap.bearing,
      "cpu_temp": snap.cpu_temp,
      "mem_pct": snap.mem_pct,
      "free_gb": snap.free_gb,
      "objects": [],
    }))

  def set_model_frame(self, frame: ModelFrame | None, snap: Snapshot) -> None:
    self.model.set_frame(frame, snap)

  def camera_size(self) -> tuple[int, int]:
    """The surface the model path must be projected for."""
    return self.camera.width(), self.camera.height()

  def poll_camera(self) -> None:
    poll = getattr(self.camera, "poll", None)
    if poll is not None:
      poll()

  # ---- layout -----------------------------------------------------------

  def _layout(self) -> None:
    w, h = self.width(), self.height()
    body = self.rect().adjusted(0, BAR_H, 0, -BAR_H)

    self.camera.setGeometry(self.rect())
    self.model.setGeometry(self.rect())
    self.overlays.setGeometry(self.rect())
    self.bands.setGeometry(self.rect())

    self.top.setGeometry(0, 0, w, BAR_H)
    self.bottom.setGeometry(0, h - BAR_H, w, BAR_H)
    self.panels.setGeometry(body)
    self.alert.setGeometry(0, (h - 160) // 2, w, 160)
    self.warnings.setGeometry((w - 700) // 2, (h - 110) // 2, 700, 110)

  def resizeEvent(self, event):
    super().resizeEvent(event)
    self._layout()

  def showEvent(self, event):
    super().showEvent(event)
    self._layout()
