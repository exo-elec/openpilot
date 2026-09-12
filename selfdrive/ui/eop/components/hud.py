"""The classic onroad HUD.

Port of hud.cc. Same elements in the same places: the MAX/set-speed box top
left, the big current speed centred under a header gradient, a MUTCD speed
limit roundel beside the set-speed box, a driver-monitoring pill top right,
the turn-by-turn maneuver card top left, and red bars down whichever edge the
driver is signalling toward while that blind spot is occupied.

Drawn as one widget rather than a renderer called from the camera widget's
paintEvent, which is how the C++ did it. The reason is EGLFS: on this device
the camera surface is a GL widget, and painting QPainter primitives into a
GL widget's paintEvent is exactly the mix that made Qt terminate (see plan
section 4.3). A separate translucent widget stacked over the camera has no
such constraint.
"""

from __future__ import annotations

import math

from openpilot.selfdrive.ui.eop.components.theme import (
  UI_HEADER_HEIGHT,
  WHITE,
  inter,
)
from openpilot.selfdrive.ui.eop.qt import (
  QBrush,
  QColor,
  QFont,
  QFontMetrics,
  QLinearGradient,
  QPainter,
  QPainterPath,
  QPen,
  QPoint,
  QRect,
  Qt,
  QWidget,
  text_width,
)
from openpilot.selfdrive.ui.eop.state import MS_TO_KPH, MS_TO_MPH, Snapshot, UIStatus

M_TO_FT = 3.28084
FT_PER_MILE = 5280.0

# hud.cc geometry
SET_SPEED_POS = (35, 25)
SET_SPEED_SIZE_IMPERIAL = (110, 130)
SET_SPEED_SIZE_METRIC = (125, 130)
SPEED_LIMIT_CENTRE = (193, 90)
SPEED_LIMIT_RADIUS = 28
NAV_CARD = QRect(20, 20, 180, 130)
BSD_BAR_W = 24

# Frames per BSD pulse. Advanced once per snapshot, i.e. at UI_FREQ_HZ.
BSD_PULSE_FRAMES = 25

# Valhalla maneuver modifiers to arrow rotation, degrees clockwise from up.
TURN_ANGLES = {
  "slight right": 30.0, "right": 90.0, "sharp right": 135.0,
  "slight left": -30.0, "left": -90.0, "sharp left": -135.0,
}


class HudOverlay(QWidget):
  """Translucent HUD layer over the camera."""

  def __init__(self, parent=None):
    super().__init__(parent)
    self.setObjectName("hud")
    self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    self.setAttribute(Qt.WA_TranslucentBackground, True)
    self._snap = Snapshot()
    self._bsd_frame = 0

  def set_snapshot(self, snap: Snapshot) -> None:
    self._snap = snap
    self._bsd_frame = (self._bsd_frame + 1) % BSD_PULSE_FRAMES
    self.update()

  # ---- painting ---------------------------------------------------------

  def paintEvent(self, event):
    s = self._snap
    p = QPainter(self)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)

    self._draw_header_gradient(p)
    if s.cruise_available:
      self._draw_set_speed(p, s)
    self._draw_current_speed(p, s)
    if s.speed_limit_ms > 0:
      self._draw_speed_limit(p, s)
    if s.driver.valid:
      self._draw_driver(p, s)
    self._draw_blind_spot_bars(p, s)
    if s.nav.valid:
      self._draw_nav(p, s)

  def _draw_header_gradient(self, p: QPainter) -> None:
    g = QLinearGradient(0, UI_HEADER_HEIGHT - UI_HEADER_HEIGHT / 2.5, 0, UI_HEADER_HEIGHT)
    g.setColorAt(0, QColor(0, 0, 0, int(0.45 * 255)))
    g.setColorAt(1, QColor(0, 0, 0, 0))
    p.fillRect(0, 0, self.width(), UI_HEADER_HEIGHT, QBrush(g))

  def _draw_set_speed(self, p: QPainter, s: Snapshot) -> None:
    default_w = SET_SPEED_SIZE_IMPERIAL[0]
    w, h = SET_SPEED_SIZE_METRIC if s.is_metric else SET_SPEED_SIZE_IMPERIAL
    # Centre the wider metric box on the same axis as the narrower one, so
    # the element does not shift when the unit setting changes.
    rect = QRect(SET_SPEED_POS[0] + (default_w - w) // 2, SET_SPEED_POS[1], w, h)

    p.setPen(QPen(QColor(255, 255, 255, 75), 4))
    p.setBrush(QColor(0, 0, 0, 166))
    p.drawRoundedRect(rect, 20, 20)

    max_color = QColor(0xa6, 0xa6, 0xa6)
    value_color = QColor(0x72, 0x72, 0x72)
    if s.cruise_set:
      value_color = WHITE
      if s.status is UIStatus.DISENGAGED:
        max_color = WHITE
      elif s.status is UIStatus.OVERRIDE:
        max_color = QColor(0x91, 0x9b, 0x95)
      else:
        max_color = QColor(0x80, 0xd8, 0xa6)

    p.setFont(inter(24, QFont.DemiBold))
    p.setPen(max_color)
    p.drawText(rect.adjusted(0, 15, 0, 0), Qt.AlignTop | Qt.AlignHCenter, "MAX")

    p.setFont(inter(32, QFont.Bold))
    p.setPen(value_color)
    text = f"{round(s.set_speed):.0f}" if s.cruise_set else "–"
    p.drawText(rect.adjusted(0, 42, 0, 0), Qt.AlignTop | Qt.AlignHCenter, text)

  def _draw_current_speed(self, p: QPainter, s: Snapshot) -> None:
    cx = self.width() // 2
    p.setFont(inter(100, QFont.Bold))
    self._centred_text(p, cx, 120, f"{round(max(0.0, s.speed_display)):.0f}", 255)
    p.setFont(inter(38))
    self._centred_text(p, cx, 165, s.speed_unit, 200)

  @staticmethod
  def _centred_text(p: QPainter, x: int, y: int, text: str, alpha: int) -> None:
    """hud.cc drawText(): centre on x, sit the baseline at y.

    Uses the glyph bounding rect rather than the font's line box, so the
    digits are optically centred instead of centred on the line height.
    """
    r = p.fontMetrics().boundingRect(text)
    r.moveCenter(QPoint(x, y - r.height() // 2))
    p.setPen(QColor(255, 255, 255, alpha))
    p.drawText(r.x(), r.bottom(), text)

  def _draw_speed_limit(self, p: QPainter, s: Snapshot) -> None:
    """MUTCD roundel: white disc, red ring, black number.

    Sits to the right of the set-speed box. Coordinates are relative to this
    widget's left edge, not the screen's, so the element lands correctly
    whatever the panel width is.
    """
    limit = round(s.speed_limit_ms * (MS_TO_KPH if s.is_metric else MS_TO_MPH))
    cx, cy = SPEED_LIMIT_CENTRE
    centre = QPoint(cx, cy)

    p.setPen(Qt.NoPen)
    p.setBrush(WHITE)
    p.drawEllipse(centre, SPEED_LIMIT_RADIUS, SPEED_LIMIT_RADIUS)
    p.setPen(QPen(QColor(255, 0, 0), 4))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(centre, SPEED_LIMIT_RADIUS, SPEED_LIMIT_RADIUS)

    p.setPen(QColor(0, 0, 0))
    p.setFont(inter(52, QFont.Bold))
    box = QRect(cx - SPEED_LIMIT_RADIUS, cy - SPEED_LIMIT_RADIUS,
                SPEED_LIMIT_RADIUS * 2, SPEED_LIMIT_RADIUS * 2)
    p.drawText(box, Qt.AlignCenter, f"{limit:.0f}")

  def _draw_driver(self, p: QPainter, s: Snapshot) -> None:
    d = s.driver
    if d.face_detected:
      label = "DRIVER" if d.face_forward else "AWAY"
      bg = QColor(0x00, 0xd8, 0x4a, 0xcc) if d.face_forward else QColor(0xff, 0xa5, 0x00, 0xcc)
    else:
      label, bg = "NO DRIVER", QColor(0xff, 0x33, 0x33, 0xcc)

    font = inter(16, QFont.DemiBold)
    font.setStyleStrategy(QFont.PreferAntialias)
    p.setFont(font)
    fm = QFontMetrics(font)
    pad_x, pad_y = 8, 4
    pill_w = text_width(fm, label) + pad_x * 2
    pill_h = fm.height() + pad_y * 2
    pill = QRect(self.width() - pill_w - 20, 20, pill_w, pill_h)

    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(pill, pill_h // 2, pill_h // 2)
    p.setPen(QColor(255, 255, 255, 0xee))
    p.drawText(pill, Qt.AlignCenter, label)

    if d.face_detected:
      self._draw_gaze(p, d)

  def _draw_gaze(self, p: QPainter, d) -> None:
    """Face box and gaze vector, faded by how attentive the driver is: the
    more attention, the fainter the box. It is a warning, not a HUD element,
    so it should recede when everything is fine."""
    box = 70
    # The driver camera is mirrored relative to the screen, so face_x is
    # flipped to put the box on the side the driver's head actually is.
    fx = int((1.0 - d.face_x) * self.width())
    fy = int(d.face_y * self.height())
    alpha = int(180 * (1.0 - d.attention_prob) + 50)

    p.setPen(QPen(QColor(255, 255, 255, alpha), 2))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(fx - box // 2, fy - box // 2, box, box, 8, 8)

    arrow = 25.0
    ax = fx + int(arrow * math.sin(math.radians(d.face_yaw)))
    ay = fy - int(arrow * math.sin(math.radians(d.face_pitch)))
    p.setPen(QPen(QColor(0x00, 0xd8, 0x4a, alpha), 2))
    p.drawLine(fx, fy, ax, ay)
    p.drawEllipse(QPoint(ax, ay), 3, 3)

  def _draw_blind_spot_bars(self, p: QPainter, s: Snapshot) -> None:
    """A pulsing red bar plus a vertical BLIND SPOT label, shown only when the
    driver is signalling *into* an occupied blind spot.

    This is deliberately narrower than BlindSpotBands, which shows any
    occupied blind spot regardless of intent. Here the driver has said they
    intend to move into the lane a car is already in, which is the case worth
    shouting about.
    """
    h = self.height()
    w = self.width()
    phase = self._bsd_frame / BSD_PULSE_FRAMES
    alpha = 120 + int(80 * math.sin(phase * math.pi * 2.0))

    left = s.left_blinker and s.blind_spot.left > 0
    right = s.right_blinker and s.blind_spot.right > 0

    if left:
      self._bsd_bar(p, QRect(0, 0, BSD_BAR_W, h), 0, BSD_BAR_W, alpha)
      self._bsd_label(p, BSD_BAR_W + 4, h // 2, -90)
    if right:
      self._bsd_bar(p, QRect(w - BSD_BAR_W, 0, BSD_BAR_W, h), w, w - BSD_BAR_W, alpha)
      self._bsd_label(p, w - BSD_BAR_W - 4, h // 2, 90)

  @staticmethod
  def _bsd_bar(p: QPainter, band: QRect, x_from: int, x_to: int, alpha: int) -> None:
    g = QLinearGradient(x_from, 0, x_to, 0)
    g.setColorAt(0.0, QColor(255, 0, 0, alpha))
    g.setColorAt(1.0, QColor(255, 0, 0, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    p.drawRect(band)

  @staticmethod
  def _bsd_label(p: QPainter, x: int, y: int, rotation: float) -> None:
    p.save()
    p.translate(x, y)
    p.rotate(rotation)
    p.setFont(inter(18, QFont.Bold))
    p.setPen(QColor(255, 50, 50, 220))
    p.drawText(QRect(-100, 0, 200, 24), Qt.AlignCenter, "BLIND SPOT")
    p.restore()

  def _draw_nav(self, p: QPainter, s: Snapshot) -> None:
    """Next maneuver only -- arrow, distance, street name. There is no map on
    this device by design; the route lives in the NavPilot phone app."""
    card = NAV_CARD
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 166))
    p.drawRoundedRect(card, 20, 20)

    cx = card.x() + card.width() // 2
    cy = card.y() + 45
    if s.nav.maneuver_type == "arrive":
      self._draw_destination_pin(p, cx, cy)
    else:
      angle = 180.0 if s.nav.maneuver_type == "uturn" else TURN_ANGLES.get(s.nav.modifier, 0.0)
      self._draw_turn_arrow(p, cx, cy, angle)

    p.setFont(inter(28, QFont.Bold))
    p.setPen(WHITE)
    p.drawText(QRect(card.x(), card.y() + 75, card.width(), 30),
               Qt.AlignCenter, format_distance(s.nav.distance_m, s.is_metric))

    if s.nav.primary_text:
      font = inter(16)
      p.setFont(font)
      elided = QFontMetrics(font).elidedText(s.nav.primary_text, Qt.ElideRight,
                                             card.width() - 16)
      p.setPen(QColor(255, 255, 255, 200))
      p.drawText(QRect(card.x(), card.y() + 105, card.width(), 22),
                 Qt.AlignCenter, elided)

  @staticmethod
  def _draw_destination_pin(p: QPainter, cx: int, cy: int) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0x00, 0xd8, 0x4a))
    p.drawEllipse(QPoint(cx, cy), 22, 22)
    p.setPen(QPen(WHITE, 4))
    p.drawLine(cx, cy - 10, cx, cy + 10)
    p.drawLine(cx - 10, cy, cx + 10, cy)

  @staticmethod
  def _draw_turn_arrow(p: QPainter, cx: int, cy: int, angle: float) -> None:
    p.save()
    p.translate(cx, cy)
    p.rotate(angle)
    path = QPainterPath()
    path.moveTo(0, -30)
    for x, y in ((20, 0), (8, 0), (8, 26), (-8, 26), (-8, 0), (-20, 0)):
      path.lineTo(x, y)
    path.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(WHITE)
    p.drawPath(path)
    p.restore()


def format_distance(metres: float, is_metric: bool) -> str:
  """Distance to the next maneuver, rounded the way a driver reads it.

  Rounded to 10 below the unit break and to one decimal above, so the number
  changes at a readable rate rather than counting down every metre.
  """
  if is_metric:
    if metres >= 1000.0:
      return f"{metres / 1000.0:.1f} km"
    return f"{int(metres / 10.0) * 10} m"
  feet = metres * M_TO_FT
  if feet >= 1000.0:
    return f"{feet / FT_PER_MILE:.1f} mi"
  return f"{int(feet / 10.0) * 10} ft"
