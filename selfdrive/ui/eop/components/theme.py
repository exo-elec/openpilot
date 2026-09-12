"""Geometry and palette for the classic 01M look.

These are not new values. Every constant here is lifted from the C++ UI this
replaces -- ui.h's border and header sizes, its status colours, sidebar.cc's
card metrics, alerts.cc's heights -- because the point of the 01M port is that
the screen a driver already knows does not change. Where a number came from a
specific file it says so, so the two can be compared line by line while the
C++ is still in git history.

Kept separate from styles/qss because these are painter values: QSS styles
widgets, and most of the classic UI is drawn with QPainter, which QSS does not
reach.
"""

from __future__ import annotations

from openpilot.selfdrive.ui.eop.qt import QColor, QFont
from openpilot.selfdrive.ui.eop.state import UIStatus

# ---- geometry (ui.h) -------------------------------------------------------

# 01M is a 1024x600 panel and only that. deviceScreenSize() became a constant
# when 02M support was removed from this branch.
SCREEN_W, SCREEN_H = 1024, 600

UI_BORDER_SIZE = 30       # status-coloured frame around the camera
UI_HEADER_HEIGHT = 250    # gradient the HUD text sits in

SIDEBAR_W = 260           # sidebar.cc setFixedWidth
SETTINGS_NAV_W = 210      # settings.cc sidebar_widget->setFixedWidth

# ---- status colours (ui.h bg_colors) ---------------------------------------

STATUS_COLORS = {
  UIStatus.DISENGAGED: QColor(0x17, 0x33, 0x49, 0xc8),
  UIStatus.OVERRIDE: QColor(0x91, 0x9b, 0x95, 0xf1),
  UIStatus.ENGAGED: QColor(0x17, 0x86, 0x44, 0xf1),
  UIStatus.ALCC: QColor(0x22, 0xa0, 0xdc, 0xf1),
}

# ---- alert colours (util.h alert_colors) -----------------------------------

ALERT_COLORS = {
  "normal": QColor(0x15, 0x15, 0x15, 0xf1),
  "warning": QColor(0xDA, 0x6F, 0x25, 0xf1),
  "critical": QColor(0xC9, 0x22, 0x31, 0xf1),
}

# alerts.cc alert_heights, by AlertSize. FULL means the whole view.
ALERT_HEIGHTS = {"small": 150, "mid": 235}

# ---- shared palette --------------------------------------------------------

GOOD = QColor(0x1b, 0x9f, 0xfa)
WARNING = QColor(0xff, 0xc9, 0x00)
DANGER = QColor(0xc9, 0x22, 0x31)
INACTIVE = QColor(0x54, 0x54, 0x54)
WHITE = QColor(0xff, 0xff, 0xff)
BLACK = QColor(0x00, 0x00, 0x00)
SIDEBAR_BG = QColor(57, 57, 57)

# Resting border for a side/rear camera overlay -- it identifies which camera
# is on screen, and is replaced by the blind-spot colour when that side is
# occupied (onroad_home.cc SIDE_OVERLAY_BORDER).
OVERLAY_BORDER = QColor(0, 200, 255, 220)
OVERLAY_BORDER_PX = 3
OVERLAY_BLIND_SPOT_PX = 12


def inter(size: int, weight: int = QFont.Normal) -> QFont:
  """util.cc's InterFont. The family is loaded from assets at startup; Qt
  falls back to the default sans if it is missing, which is what happens on a
  dev PC without the bundled fonts."""
  f = QFont("Inter")
  f.setPixelSize(size)
  f.setWeight(weight)
  return f
