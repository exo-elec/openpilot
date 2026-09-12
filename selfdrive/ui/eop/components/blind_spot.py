"""Blind-spot severity and its on-screen presentation.

Behaviour is specified in docs/eop10/EOP10_PORT_PLAN.md section 5.7 and was
settled on dev/01M, where it exists in C++. This is the PySide6 side of the
same design, not a new one.

Severity is deliberately a plain dataclass computed by a free function taking
already-extracted values, with no cereal import anywhere in this module. That
keeps every widget here testable without capnp, and keeps the messaging
surface confined to state.py -- the same rule section 5.2 sets for bridges.
"""

from __future__ import annotations

from dataclasses import dataclass

from openpilot.selfdrive.ui.eop.qt import QWidget

from openpilot.selfdrive.ui.eop.components.border_overlay import (
  BorderOverlay,
  Mode,
  Side,
)

CLEAR = 0
CAUTION = 1
WARNING = 2

# Fraction of view width each band covers. 90px on 01M's 1024 was tuned by
# eye; expressed as a fraction it holds at 1600 without becoming a stripe.
_BAND_FRACTION = 0.09


@dataclass(frozen=True)
class BlindSpotSeverity:
  """0 = clear, 1 = caution, 2 = warning, per side."""
  left: int = CLEAR
  right: int = CLEAR

  @property
  def any_active(self) -> bool:
    return self.left > CLEAR or self.right > CLEAR


def fuse_blind_spot(controls_left: int, controls_right: int,
                    car_left: bool, car_right: bool) -> BlindSpotSeverity:
  """Fuse controlsState's Int8 severity with carState's bools.

  carState carries no severity of its own, so folding it in can only raise a
  side to at least CAUTION -- it can never suppress a real WARNING.

  Callers pass 0/False for a source that is not currently valid rather than
  skipping it. That is not incidental: max() only ever raises, so a caller
  that instead left the previous value in place would strand a stale WARNING
  on screen with no way to clear it if controlsState alone went quiet. The
  same reasoning is written out at the C++ site on dev/01M.
  """
  return BlindSpotSeverity(
    left=max(int(controls_left), CAUTION if car_left else CLEAR),
    right=max(int(controls_right), CAUTION if car_right else CLEAR),
  )


def severity_color(severity: int) -> str:
  return "warning" if severity >= WARNING else "caution"


class BlindSpotBands(QWidget):
  """Left and right edge bands over the road view.

  Caution is solid; warning breathes on a raised cosine. The breathe is the
  behaviour 01M's C++ indicator has always had, on the reasoning that a hard
  on/off this deep into peripheral vision reads as a distraction while a
  smooth ramp still draws the eye -- see BorderOverlay.Mode.BREATHE.
  """

  def __init__(self, parent: QWidget | None = None):
    super().__init__(parent)
    self._left = BorderOverlay(Side.LEFT, "caution", Mode.SOLID, parent=self)
    self._right = BorderOverlay(Side.RIGHT, "caution", Mode.SOLID, parent=self)
    self._severity = BlindSpotSeverity()

  def severity(self) -> BlindSpotSeverity:
    return self._severity

  def set_severity(self, sev: BlindSpotSeverity) -> None:
    self._severity = sev
    for band, level in ((self._left, sev.left), (self._right, sev.right)):
      band.set_enabled(level > CLEAR)
      if level > CLEAR:
        band.set_color(severity_color(level))
        band.set_mode(Mode.BREATHE if level >= WARNING else Mode.SOLID)

  def _layout_bands(self) -> None:
    w = max(1, int(self.width() * _BAND_FRACTION))
    h = self.height()
    self._left.setGeometry(0, 0, w, h)
    self._right.setGeometry(self.width() - w, 0, w, h)

  def resizeEvent(self, event) -> None:
    super().resizeEvent(event)
    self._layout_bands()

  def showEvent(self, event) -> None:
    # Not redundant with resizeEvent. Qt queues resize events for a widget
    # that has never been shown, so a parent that sets geometry once and then
    # shows would leave the bands at their default 100x30 -- the same trap the
    # C++ side hit by calling move() from a constructor, before layout. Laying
    # out again on show costs one call and closes it.
    super().showEvent(event)
    self._layout_bands()
