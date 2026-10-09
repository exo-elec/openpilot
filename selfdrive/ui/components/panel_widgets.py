"""The panel set the gesture system cycles through.

All QPainter, no messaging: each reads from PanelData.values, which the
onroad view fills from the snapshot, so the panels stay toolkit-pure.
"""

from __future__ import annotations

import math

from openpilot.selfdrive.ui.components.panels import SidePanel, panel
from openpilot.selfdrive.ui.qt import Qt, QColor, QPainter

MS_TO_KPH = 3.6


@panel("speed")
class SpeedPanel(SidePanel):
  title = "speed"

  def paint_body(self, p: QPainter) -> None:
    v = self.data.values.get("v_ego", 0.0) * MS_TO_KPH
    self._big(p, f"{v:.0f}", "km/h")


@panel("telemetry")
class TelemetryPanel(SidePanel):
  title = "telemetry"

  def paint_body(self, p: QPainter) -> None:
    v = self.data.values
    rows = [
      ("speed", f"{v.get('v_ego', 0.0) * MS_TO_KPH:.0f} km/h"),
      ("steer", f"{v.get('steering_angle', 0.0):+.1f}°"),
      ("lead", f"{v.get('lead_d', 0.0):.0f} m" if v.get("lead_valid") else "--"),
      ("state", str(v.get("status", "disengaged"))),
    ]
    self._rows(p, rows)

  def _rows(self, p: QPainter, rows) -> None:
    r = self.rect().adjusted(16, 44, -16, -16)
    f = p.font()
    f.setPointSize(13)
    p.setFont(f)
    y = r.top()
    for label, value in rows:
      p.setPen(QColor(124, 139, 141))
      p.drawText(r.left(), y + 18, label)
      p.setPen(QColor(230, 235, 234))
      p.drawText(r.left(), y + 42, value)
      y += 62


@panel("clock")
class ClockPanel(SidePanel):
  title = "clock"

  def paint_body(self, p: QPainter) -> None:
    from openpilot.selfdrive.ui.qt import QtCore
    now = QtCore.QDateTime.currentDateTime()
    self._big(p, now.toString("HH:mm"), now.toString("ddd d MMM"))


@panel("compass")
class CompassPanel(SidePanel):
  title = "heading"

  def paint_body(self, p: QPainter) -> None:
    bearing = float(self.data.values.get("bearing", 0.0))
    c = self.rect().center()
    radius = min(self.width(), self.height()) // 3
    p.setPen(QColor(124, 139, 141))
    p.drawEllipse(c, radius, radius)
    rad = math.radians(bearing - 90.0)
    p.setPen(QColor(255, 194, 0))
    p.drawLine(c.x(), c.y(),
               int(c.x() + radius * math.cos(rad)),
               int(c.y() + radius * math.sin(rad)))
    p.setPen(QColor(230, 235, 234))
    p.drawText(self.rect().adjusted(0, 0, 0, -18),
               Qt.AlignBottom | Qt.AlignHCenter, f"{bearing:.0f}°")


@panel("trip")
class TripPanel(SidePanel):
  title = "trip"

  def paint_body(self, p: QPainter) -> None:
    v = self.data.values
    self._big(p, f"{v.get('trip_km', 0.0):.1f}", "km this trip")


@panel("bev")
class BevPanel(SidePanel):
  """Top-down view. Retained as an opt-in panel rather than the permanent
  corner overlay it used to be on dev/01M -- at full panel size it is
  readable, which was the original problem with it."""
  title = "surroundings"

  SCALE_PX_PER_M = 4.0

  def paint_body(self, p: QPainter) -> None:
    r = self.rect().adjusted(10, 40, -10, -10)
    cx, cy = r.center().x(), r.bottom() - 40

    p.setPen(QColor(255, 255, 255, 30))
    for m in range(10, 60, 10):
      y = cy - m * self.SCALE_PX_PER_M
      if y > r.top():
        p.drawLine(r.left(), int(y), r.right(), int(y))

    p.setBrush(QColor(0, 200, 255, 200))
    p.setPen(Qt.NoPen)
    p.drawRect(cx - 7, cy - 14, 14, 28)

    p.setBrush(QColor(255, 194, 0, 220))
    for obj in self.data.values.get("objects", []):
      x = cx + float(obj.get("y", 0.0)) * -self.SCALE_PX_PER_M
      y = cy - float(obj.get("x", 0.0)) * self.SCALE_PX_PER_M
      if r.top() < y < r.bottom():
        p.drawRect(int(x) - 5, int(y) - 9, 10, 18)


@panel("hardware")
class HardwarePanel(SidePanel):
  title = "hardware"

  def paint_body(self, p: QPainter) -> None:
    v = self.data.values
    rows = [
      ("cpu", f"{v.get('cpu_temp', 0.0):.0f}°C"),
      ("npu", f"{v.get('npu_usage', 0.0):.0f}%"),
      ("mem", f"{v.get('mem_pct', 0.0):.0f}%"),
      ("free", f"{v.get('free_gb', 0.0):.1f} GB"),
    ]
    TelemetryPanel._rows(self, p, rows)


@panel("vehicle")
class VehiclePanel(SidePanel):
  title = "vehicle"

  def paint_body(self, p: QPainter) -> None:
    v = self.data.values
    rows = [
      ("gear", str(v.get("gear", "--"))),
      ("cruise", f"{v.get('cruise_kph', 0.0):.0f} km/h"),
      ("blinker", v.get("blinker", "none")),
      ("engaged", "yes" if v.get("engaged") else "no"),
    ]
    TelemetryPanel._rows(self, p, rows)
