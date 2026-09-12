"""Edge gradient overlay -- the primitive behind blind-spot bands and
camera source markers.

Ported from Nagasware's `src/ui/components/onroad/border_overlay.py`, which
already had the right shape: a translucent gradient running inward from one
screen edge, and a single class-level timer so every instance blinks in step
rather than each keeping its own clock and drifting apart.

Three things were changed on the way across, all of them defects rather than
preferences:

1. Blink-off used to paint the gradient and then overpaint the whole rect
   with `CompositionMode_Source` and a transparent brush to erase it. That is
   two full-rect paints per frame to draw nothing. It returns early instead.
2. `_instances` was a plain list appended in `__init__` and pruned only in
   `closeEvent`. Child widgets are normally destroyed without ever getting a
   close event, so entries accumulated and the shared timer eventually called
   `update()` on deleted C++ objects. It holds weak references now and drops
   dead ones as it sweeps.
3. The shared timer ran forever regardless of whether anything was enabled.
   It now stops when nothing needs it and restarts on demand -- a 300 ms
   wakeup that repaints nothing is pure drain on a device that is otherwise
   idle offroad.
"""

from __future__ import annotations

import weakref
from enum import Enum

from openpilot.selfdrive.ui.eop.qt import (
  QColor,
  QLinearGradient,
  QPainter,
  Qt,
  QTimer,
  QWidget,
)


class Side(Enum):
  LEFT = "left"
  RIGHT = "right"
  TOP = "top"
  BOTTOM = "bottom"


class Mode(Enum):
  SOLID = "solid"
  BLINK = "blink"


# Alpha ramp from the edge inward. Nagasware's stops, kept: the long tail
# matters because a hard-edged band reads as a UI element, while a falloff
# reads as light spilling in from the side, which is what it is standing in
# for.
_STOPS = ((0.0, 200), (0.3, 120), (0.6, 60), (1.0, 0))

_PRESETS = {
  "warning": QColor(255, 60, 60),
  "caution": QColor(255, 194, 0),
  "info": QColor(0, 200, 255),
  "success": QColor(0, 200, 0),
}


class BorderOverlay(QWidget):
  """A gradient band along one edge, optionally blinking in sync with every
  other instance."""

  _timer: QTimer | None = None
  _blink_on = True
  _instances: list[weakref.ref] = []

  def __init__(self, side: Side, color: str | QColor = "warning",
               mode: Mode = Mode.SOLID, blink_interval_ms: int = 400,
               parent: QWidget | None = None):
    super().__init__(parent)
    self.side = side
    self.mode = mode
    self._color = self._resolve(color)
    self._enabled = False
    self._blink_interval_ms = blink_interval_ms

    self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    self.setAttribute(Qt.WA_TranslucentBackground, True)
    self.setAutoFillBackground(False)

    BorderOverlay._instances.append(weakref.ref(self))

  # ---- appearance -------------------------------------------------------

  @staticmethod
  def _resolve(color: str | QColor) -> QColor:
    if isinstance(color, QColor):
      return color
    if isinstance(color, str) and color.startswith("#"):
      c = QColor(color)
      if c.isValid():
        return c
    return _PRESETS.get(color, _PRESETS["warning"])

  def set_color(self, color: str | QColor) -> None:
    c = self._resolve(color)
    if c == self._color:
      return
    self._color = c
    if self._enabled:
      self.update()

  def set_mode(self, mode: Mode) -> None:
    if mode == self.mode:
      return
    self.mode = mode
    self._sync_timer()
    if self._enabled:
      self.update()

  def set_enabled(self, enabled: bool) -> None:
    if enabled == self._enabled:
      return
    self._enabled = enabled
    self._sync_timer()
    self.update()

  def is_enabled(self) -> bool:
    return self._enabled

  # ---- shared blink clock ----------------------------------------------

  @classmethod
  def _live(cls) -> list[BorderOverlay]:
    """Live instances, pruning collected ones as we go."""
    live, kept = [], []
    for ref in cls._instances:
      inst = ref()
      if inst is not None:
        live.append(inst)
        kept.append(ref)
    cls._instances = kept
    return live

  @classmethod
  def _tick(cls) -> None:
    cls._blink_on = not cls._blink_on
    any_blinking = False
    for inst in cls._live():
      if inst._enabled and inst.mode is Mode.BLINK:
        any_blinking = True
        inst.update()
    if not any_blinking:
      cls._stop_timer()

  @classmethod
  def _stop_timer(cls) -> None:
    if cls._timer is not None:
      try:
        cls._timer.stop()
      except RuntimeError:
        # The C++ QTimer can already be gone -- on application teardown Qt
        # destroys it while this class attribute still holds the Python
        # wrapper. Calling stop() then raises rather than returning, so a
        # tidy shutdown would end in a traceback.
        pass
      cls._timer = None
      cls._blink_on = True

  def _sync_timer(self) -> None:
    if self._enabled and self.mode is Mode.BLINK:
      if BorderOverlay._timer is None:
        t = QTimer()
        t.setInterval(self._blink_interval_ms)
        t.timeout.connect(BorderOverlay._tick)
        t.start()
        BorderOverlay._timer = t
      return
    # Nothing left blinking? Let the next tick notice and stop itself, rather
    # than scanning every instance on every state change.

  # ---- painting ---------------------------------------------------------

  def paintEvent(self, event) -> None:
    if not self._enabled:
      return
    if self.mode is Mode.BLINK and not BorderOverlay._blink_on:
      return

    p = QPainter(self)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(self._gradient())
    p.drawRect(self.rect())

  def _gradient(self) -> QLinearGradient:
    w, h = self.width(), self.height()
    if self.side is Side.LEFT:
      g = QLinearGradient(0, 0, w, 0)
    elif self.side is Side.RIGHT:
      g = QLinearGradient(w, 0, 0, 0)
    elif self.side is Side.TOP:
      g = QLinearGradient(0, 0, 0, h)
    else:
      g = QLinearGradient(0, h, 0, 0)

    r, gr, b = self._color.red(), self._color.green(), self._color.blue()
    for pos, alpha in _STOPS:
      g.setColorAt(pos, QColor(r, gr, b, alpha))
    return g
