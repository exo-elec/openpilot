"""ADAS engagement warnings.

Taken from VisionPilot, which is the only one of the two source UIs that has
a typed warning taxonomy at all -- Nagasware has generic border alerts and
nothing that says *why* engagement is blocked.

VisionPilot spells it twice, though: `WarningType` (13 entries, carrying a
`dismissible` flag) and `SafetyType` (9 entries, carrying a `priority`), with
overlapping members and metadata that disagrees -- CALIBRATION_REQUIRED is
dismissible in one and priority-80 in the other, and CRASH_DETECTED uses key
"crash_detected" in one and "crash" in the other. Two enums for one concept
is how a door-open warning ends up outranking a crash on one screen and not
the other. Merged into one here, carrying both properties.

`blocking` and `dismissible` are kept as separate fields rather than
inverses. They answer different questions -- whether the car may engage, and
whether the driver may clear the message -- and collapsing them is what makes
a "degraded but driveable" state impossible to express.
"""

from __future__ import annotations

from enum import Enum

from openpilot.selfdrive.ui.eop.components.base import BaseOverlay
from openpilot.selfdrive.ui.eop.qt import Qt, QColor, QPainter


class AdasWarning(Enum):
  """key, title, icon, colour, blocks engagement, dismissible, priority."""

  # Vehicle state -- driver can fix, blocks engagement until they do.
  DOOR_OPEN = ("door_open", "Door Open", "🚪", "#FF6B6B", True, False, 100)
  SEATBELT = ("seatbelt", "Fasten Seatbelt", "🛡️", "#4ECDC4", True, False, 100)
  PARKING_BRAKE = ("parking_brake", "Release Parking Brake", "🅿️", "#FF9F43", True, False, 90)

  # System faults -- not driver-fixable, blocks engagement.
  STEERING_FAULT = ("steering_fault", "Steering System Fault", "⚠️", "#FF0000", True, False, 98)
  HARDWARE_FAULT = ("hardware_fault", "Hardware System Fault", "🔧", "#FF0000", True, False, 95)
  CAN_FAULT = ("can_fault", "CAN Communication Lost", "🔌", "#FF0000", True, False, 95)

  # Immediate disengage. Highest priority: nothing outranks a crash.
  CRASH_DETECTED = ("crash_detected", "Crash Detected — ADAS Disengaged", "💥", "#FF0000", True, False, 110)

  # Degraded -- driveable, user action wanted.
  CALIBRATION_REQUIRED = ("calibration_required", "Calibration Required", "🎯", "#A29BFE", True, True, 80)
  CALIBRATION_DEGRADED = ("calibration_degraded", "Calibration Degraded", "📐", "#FFD93D", False, True, 60)
  TIRE_MONITOR = ("tire_monitor", "Tire Alert", "🛞", "#FF9500", False, True, 70)
  GPS_FAULT = ("gps_fault", "GPS Signal Lost", "🛰️", "#FFD93D", False, True, 50)

  # Setup.
  TRAINING_INCOMPLETE = ("training_incomplete", "Training Required", "📚", "#00D2D3", True, True, 40)
  ADAS_DISABLED = ("adas_disabled", "ADAS Disabled", "🚫", "#74B9FF", False, True, 30)

  def __init__(self, key, title, icon, color, blocking, dismissible, priority):
    self.key = key
    self.title = title
    self.icon = icon
    self.color = color
    self.blocking = blocking
    self.dismissible = dismissible
    self.priority = priority

  @classmethod
  def from_key(cls, key: str) -> AdasWarning | None:
    for w in cls:
      if w.key == key:
        return w
    return None


def highest(active: list[AdasWarning]) -> AdasWarning | None:
  """Most important active warning. Ties break toward blocking."""
  if not active:
    return None
  return max(active, key=lambda w: (w.priority, w.blocking))


def blocks_engagement(active: list[AdasWarning]) -> bool:
  return any(w.blocking for w in active)


class WarningOverlay(BaseOverlay):
  """Centre card for the highest-priority active warning."""

  def __init__(self, parent=None):
    super().__init__("warningOverlay", parent)
    self._active: list[AdasWarning] = []
    self._dismissed: set[str] = set()
    self.hide()

  def set_active(self, active: list[AdasWarning]) -> None:
    # A warning that clears and returns should show again, so forget its
    # dismissal once it has genuinely gone away.
    keys = {w.key for w in active}
    self._dismissed &= keys
    self._active = [w for w in active if w.key not in self._dismissed]
    self.setVisible(bool(self._active))
    self.update()

  def dismiss_current(self) -> None:
    top = highest(self._active)
    if top is not None and top.dismissible:
      self._dismissed.add(top.key)
      self.set_active(self._active)

  def current(self) -> AdasWarning | None:
    return highest(self._active)

  def paintEvent(self, event):
    top = self.current()
    if top is None:
      return
    p = QPainter(self)
    p.setRenderHint(QPainter.Antialiasing)
    card = self.rect().adjusted(0, 0, 0, 0)

    p.setBrush(QColor(13, 17, 19, 240))
    p.setPen(QColor(top.color))
    p.drawRoundedRect(card.adjusted(1, 1, -1, -1), 14, 14)

    f = p.font()
    f.setPointSize(34)
    p.setFont(f)
    p.setPen(QColor(top.color))
    p.drawText(card.adjusted(28, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, top.icon)

    f.setPointSize(22)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor(230, 235, 234))
    p.drawText(card.adjusted(96, 0, -20, -14), Qt.AlignVCenter | Qt.AlignLeft, top.title)

    if len(self._active) > 1:
      f.setPointSize(12)
      f.setBold(False)
      p.setFont(f)
      p.setPen(QColor(124, 139, 141))
      p.drawText(card.adjusted(96, 22, -20, 0), Qt.AlignVCenter | Qt.AlignLeft,
                 f"+{len(self._active) - 1} more")

  def mousePressEvent(self, event):
    self.dismiss_current()
