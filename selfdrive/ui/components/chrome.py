"""Top and bottom bars, and the centre alert.

The 50px bands above and below the 500px content area (plan section 5.4).
"""

from __future__ import annotations

from openpilot.selfdrive.ui.components.base import BaseOverlay
from openpilot.selfdrive.ui.qt import Qt, QColor, QPainter, QtCore

BAR_H = 50


class TopBar(BaseOverlay):
  def __init__(self, parent=None):
    super().__init__("topBar", parent)
    self.status = "disengaged"
    self.temp_c = 0.0

  def paintEvent(self, event):
    p = QPainter(self)
    p.fillRect(self.rect(), QColor(19, 26, 28, 220))
    r = self.rect().adjusted(20, 0, -20, 0)
    f = p.font()
    f.setPointSize(12)
    p.setFont(f)
    p.setPen(QColor(179, 192, 192))
    p.drawText(r, Qt.AlignVCenter | Qt.AlignLeft,
               QtCore.QDateTime.currentDateTime().toString("HH:mm"))
    p.setPen(QColor(230, 235, 234))
    p.drawText(r, Qt.AlignVCenter | Qt.AlignHCenter, self.status.upper())
    p.setPen(QColor(179, 192, 192))
    p.drawText(r, Qt.AlignVCenter | Qt.AlignRight, f"{self.temp_c:.0f}°C")


class BottomBar(BaseOverlay):
  def __init__(self, parent=None):
    super().__init__("bottomBar", parent)
    self.left_text = ""
    self.right_text = ""

  def paintEvent(self, event):
    p = QPainter(self)
    p.fillRect(self.rect(), QColor(19, 26, 28, 220))
    r = self.rect().adjusted(20, 0, -20, 0)
    f = p.font()
    f.setPointSize(12)
    p.setFont(f)
    p.setPen(QColor(179, 192, 192))
    p.drawText(r, Qt.AlignVCenter | Qt.AlignLeft, self.left_text)
    p.drawText(r, Qt.AlignVCenter | Qt.AlignRight, self.right_text)


class AlertOverlay(BaseOverlay):
  """Centre alert band. Always above every camera overlay -- a full-screen
  image must never bury an alert."""

  SEVERITY_COLOR = {
    "none": QColor(0, 0, 0, 0),
    "normal": QColor(19, 26, 28, 220),
    "warning": QColor(201, 118, 47, 235),
    "critical": QColor(180, 60, 50, 245),
  }

  def __init__(self, parent=None):
    super().__init__("alertOverlay", parent)
    self.text1 = ""
    self.text2 = ""
    self.severity = "none"

  def set_alert(self, text1: str, text2: str = "", severity: str = "normal") -> None:
    self.text1, self.text2, self.severity = text1, text2, severity
    self.setVisible(bool(text1))
    self.update()

  def paintEvent(self, event):
    if not self.text1:
      return
    p = QPainter(self)
    p.setRenderHint(QPainter.Antialiasing)
    p.fillRect(self.rect(), self.SEVERITY_COLOR.get(self.severity, self.SEVERITY_COLOR["normal"]))
    r = self.rect()
    p.setPen(QColor(255, 255, 255))
    f = p.font()
    f.setPointSize(24)
    f.setBold(True)
    p.setFont(f)
    p.drawText(r.adjusted(0, 0, 0, -30 if self.text2 else 0), Qt.AlignCenter, self.text1)
    if self.text2:
      f.setPointSize(15)
      f.setBold(False)
      p.setFont(f)
      p.drawText(r.adjusted(0, 40, 0, 0), Qt.AlignCenter, self.text2)
