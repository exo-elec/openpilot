"""Wide Nagasware composition over the shared adaptive driving layer."""

from openpilot.selfdrive.ui.views.driving import OnroadView as DrivingView, PlaceholderCamera
from openpilot.selfdrive.ui.components.theme import SCREEN_W as PANEL_W, SCREEN_H as PANEL_H
from openpilot.selfdrive.ui.components.chrome import BAR_H
from openpilot.selfdrive.ui.components.panels import PanelHost, PanelData
from openpilot.selfdrive.ui.components.warnings import WarningOverlay, AdasWarning, blocks_engagement


__all__ = ["OnroadView", "PlaceholderCamera", "PANEL_W", "PANEL_H"]


class OnroadView(DrivingView):
  def __init__(self, live_camera=True, store=None, parent=None):
    super().__init__(live_camera=live_camera, store=store, parent=parent)
    self.panels = None
    self._panel_snapshot = None
    self.warnings = WarningOverlay(self)
    for widget in (self.top, self.bottom, self.overlays, self.warnings, self.pairing, self.voice_popup, self.alert):
      widget.raise_()

  def set_snapshot(self, snap):
    super().set_snapshot(snap)
    self.hud.setVisible(not snap.bev_enabled and self.width() < 1280)
    active = [w for w in (AdasWarning.from_key(k) for k in snap.warnings) if w]
    self.warnings.set_active(active)
    if self.panels is None:
      self._panel_snapshot = snap
      return
    self.panels.set_blocked(snap.alert_severity == 'critical' or blocks_engagement(active))
    self.panels.set_data(
      PanelData(
        {
          "v_ego": snap.v_ego,
          "steering_angle": snap.steering_angle,
          "cruise_kph": snap.cruise_kph,
          "gear": snap.gear,
          "engaged": snap.status.value == "engaged",
          "blinker": ("left" if snap.left_blinker else "right" if snap.right_blinker else "none"),
          "lead_valid": snap.lead_valid,
          "lead_d": snap.lead_d,
          "status": snap.status.value,
          "bearing": snap.bearing,
          "cpu_temp": snap.cpu_temp,
          "mem_pct": snap.mem_pct,
          "free_gb": snap.free_gb,
          "objects": [],
          "voice_text": snap.voice_text,
          "nav": snap.nav,
          "map": snap.nav_map,
          "is_metric": snap.is_metric,
        }
      )
    )

  def _layout(self):
    super()._layout()
    if not hasattr(self, 'panels'):
      return
    wide = self.width() >= 1280
    if wide and self.panels is None:
      self.panels = PanelHost(self)
      for widget in (self.panels, self.top, self.bottom, self.overlays, self.warnings, self.pairing, self.voice_popup, self.alert):
        widget.raise_()
      if self._panel_snapshot is not None:
        self.set_snapshot(self._panel_snapshot)
    if self.panels is not None:
      self.panels.setVisible(wide)
    self.hud.setVisible(not wide and not (self.bev.snap and self.bev.snap.bev_enabled))
    if self.panels is not None:
      self.panels.setGeometry(self.rect().adjusted(0, BAR_H, 0, -BAR_H))
    width = min(700, max(1, self.width() - 40))
    self.warnings.setGeometry((self.width() - width) // 2, (self.height() - 110) // 2, width, 110)
