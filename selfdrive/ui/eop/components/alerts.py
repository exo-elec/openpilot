"""The onroad alert banner.

Port of alerts.cc's paintEvent. What the alert *says*, including the ones the
UI raises about selfdrived having gone quiet, is decided in state.py -- that
half needs the SubMaster's receipt timestamps, and state.py is the only module
here that holds one. This widget only draws what it is handed.

Sizes are alerts.cc's: a small band at the bottom, a taller one with two lines
of text, and a full-screen takeover for the critical case.
"""

from __future__ import annotations

from openpilot.selfdrive.ui.eop.components.theme import (
  ALERT_COLORS,
  ALERT_HEIGHTS,
  WHITE,
  inter,
)
from openpilot.selfdrive.ui.eop.qt import (
  QBrush,
  QColor,
  QFont,
  QLinearGradient,
  QPainter,
  QRect,
  Qt,
  QWidget,
)

MARGIN = 25
RADIUS = 18

# Past this many characters the headline is set smaller so a long one still
# fits the full-screen layout without clipping (alerts.cc).
LONG_TEXT_CHARS = 15


class AlertBanner(QWidget):
  """Bottom-anchored alert band. Hidden entirely when there is no alert."""

  def __init__(self, parent=None):
    super().__init__(parent)
    self.setObjectName("alertBanner")
    # Purely informational. It must not swallow taps meant for the buttons
    # underneath -- the same reason every camera overlay is click-through.
    self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    self.setAttribute(Qt.WA_TranslucentBackground, True)
    self.text1 = ""
    self.text2 = ""
    self.severity = "normal"
    self.size = "none"

  def set_alert(self, text1: str, text2: str, severity: str, size: str) -> None:
    if (text1, text2, severity, size) == (self.text1, self.text2, self.severity, self.size):
      return
    self.text1, self.text2 = text1, text2
    self.severity, self.size = severity, size
    self.update()

  def clear(self) -> None:
    self.set_alert("", "", "normal", "none")

  def has_alert(self) -> bool:
    return self.size != "none" and bool(self.text1)

  def _band(self) -> QRect:
    if self.size == "full":
      return self.rect()
    h = ALERT_HEIGHTS.get(self.size, ALERT_HEIGHTS["small"])
    return QRect(MARGIN, self.height() - h + MARGIN,
                 self.width() - MARGIN * 2, h - MARGIN * 2)

  def paintEvent(self, event):
    if not self.has_alert():
      return

    r = self._band()
    radius = 0 if self.size == "full" else RADIUS

    p = QPainter(self)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(ALERT_COLORS.get(self.severity, ALERT_COLORS["normal"])))
    p.drawRoundedRect(r, radius, radius)

    # A darkening gradient behind the fill, so the band reads as lit from
    # above rather than as a flat rectangle.
    g = QLinearGradient(0, r.y(), 0, r.bottom())
    g.setColorAt(0, QColor(0, 0, 0, int(0.05 * 255)))
    g.setColorAt(1, QColor(0, 0, 0, int(0.35 * 255)))
    p.setCompositionMode(QPainter.CompositionMode_DestinationOver)
    p.setBrush(QBrush(g))
    p.drawRoundedRect(r, radius, radius)
    p.setCompositionMode(QPainter.CompositionMode_SourceOver)

    p.setPen(WHITE)
    if self.size == "small":
      p.setFont(inter(42, QFont.DemiBold))
      p.drawText(r, Qt.AlignCenter, self.text1)
    elif self.size == "full":
      self._paint_full(p, r)
    else:
      self._paint_mid(p, r)

  def _paint_mid(self, p: QPainter, r: QRect) -> None:
    cy = r.center().y()
    p.setFont(inter(50, QFont.Bold))
    p.drawText(QRect(0, cy - 70, self.width(), 85),
               Qt.AlignHCenter | Qt.AlignTop, self.text1)
    p.setFont(inter(38))
    p.drawText(QRect(0, cy + 12, self.width(), 50), Qt.AlignHCenter, self.text2)

  def _paint_full(self, p: QPainter, r: QRect) -> None:
    long_headline = len(self.text1) > LONG_TEXT_CHARS
    p.setFont(inter(75 if long_headline else 100, QFont.Bold))
    p.drawText(QRect(0, r.y() + (135 if long_headline else 150), self.width(), 340),
               Qt.AlignHCenter | Qt.TextWordWrap, self.text1)
    p.setFont(inter(50))
    p.drawText(QRect(0, r.height() - (200 if long_headline else 235), self.width(), 170),
               Qt.AlignHCenter | Qt.TextWordWrap, self.text2)
