"""Floating, swipeable side panels (plan section 5.6).

Gesture vocabulary is Nagasware's, which is the implementation that actually
runs. The signal shape is widget_changed / swap_panels / reset.

Moved off the main window's event filter and onto the panel base class, and
the hardcoded 500px split and leftover debug print are gone.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from openpilot.selfdrive.ui.components.base import BaseTimerWidget
from openpilot.selfdrive.ui.qt import (
  Qt,
  QColor,
  QPainter,
  QTimer,
  QWidget,
  Signal,
)

SWIPE_PX = 10          # Nagasware's threshold, deliberately easy to trigger
HOLD_MS = 1000         # press-and-hold -> swap panels
MULTI_TAP_MS = 400
RESET_TAPS = 3


@dataclass
class PanelData:
  """Whatever the active panel needs. Panels read what they know about."""
  values: dict


class SidePanel(BaseTimerWidget):
  """Base for a swappable information panel.

  Owns its gestures. Subclasses implement `paint_body`.
  """

  widget_changed = Signal(str, str)     # panel ('left'/'right'), direction
  swap_panels = Signal()
  reset_requested = Signal(str)

  title = "panel"

  def __init__(self, panel: str, parent=None, interval_ms: int = 500):
    super().__init__(f"panel_{panel}_{self.title}", interval_ms, parent)
    self.panel = panel
    self.data = PanelData({})
    self._press_pos = None
    self._press_ms = 0.0
    self._taps = 0
    self._last_tap_ms = 0.0
    self._tap_timer = QTimer(self)
    self._tap_timer.setSingleShot(True)
    self._tap_timer.setInterval(MULTI_TAP_MS)
    self._tap_timer.timeout.connect(self._resolve_taps)

  # ---- data -------------------------------------------------------------

  def set_data(self, data: PanelData) -> None:
    self.data = data
    self.update()

  # ---- gestures ---------------------------------------------------------

  @staticmethod
  def _now_ms() -> float:
    return time.monotonic() * 1000.0

  def mousePressEvent(self, event):
    if event.button() == Qt.LeftButton:
      self._press_pos = event.pos()
      self._press_ms = self._now_ms()

  def mouseReleaseEvent(self, event):
    if self._press_pos is None:
      return
    dx = event.pos().x() - self._press_pos.x()
    held = self._now_ms() - self._press_ms
    self._press_pos = None

    if abs(dx) > SWIPE_PX:
      self.widget_changed.emit(self.panel, "left" if dx < 0 else "right")
      return
    if held >= HOLD_MS:
      self.swap_panels.emit()
      return

    now = self._now_ms()
    self._taps = self._taps + 1 if (now - self._last_tap_ms) < MULTI_TAP_MS else 1
    self._last_tap_ms = now
    self._tap_timer.start()

  def _resolve_taps(self):
    if self._taps >= RESET_TAPS:
      self.reset_requested.emit(self.panel)
    self._taps = 0

  # ---- painting ---------------------------------------------------------

  def paintEvent(self, event):
    p = QPainter(self)
    p.setRenderHint(QPainter.Antialiasing)
    p.fillRect(self.rect(), QColor(0, 0, 0, 140))
    p.setPen(QColor(124, 139, 141))
    f = p.font()
    f.setPointSize(11)
    p.setFont(f)
    p.drawText(self.rect().adjusted(14, 10, -14, 0), Qt.AlignTop | Qt.AlignLeft,
               self.title.upper())
    self.paint_body(p)
    p.setPen(QColor(255, 255, 255, 40))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)

  def paint_body(self, p: QPainter) -> None:
    """Subclass hook."""

  def _big(self, p: QPainter, text: str, sub: str = "") -> None:
    r = self.rect()
    p.setPen(QColor(255, 255, 255))
    f = p.font()
    f.setPointSize(38)
    f.setBold(True)
    p.setFont(f)
    p.drawText(r, Qt.AlignCenter, text)
    if sub:
      p.setPen(QColor(179, 192, 192))
      f.setPointSize(12)
      f.setBold(False)
      p.setFont(f)
      p.drawText(r.adjusted(0, 0, 0, -18), Qt.AlignBottom | Qt.AlignHCenter, sub)


class PanelRegistry:
  """Which panel types exist, and which two are currently shown."""

  def __init__(self):
    self._types: dict[str, type[SidePanel]] = {}
    self.order: list[str] = []

  def register(self, key: str, cls: type[SidePanel]) -> None:
    self._types[key] = cls
    if key not in self.order:
      self.order.append(key)

  def make(self, key: str, panel: str, parent=None) -> SidePanel:
    return self._types[key](panel, parent)

  def next_key(self, current: str, direction: str) -> str:
    if not self.order:
      return current
    i = self.order.index(current) if current in self.order else 0
    step = 1 if direction == "right" else -1
    return self.order[(i + step) % len(self.order)]


REGISTRY = PanelRegistry()


def register_builtin_panels() -> PanelRegistry:
  """Populate REGISTRY with the shipped panels.

  Importing `panel_widgets` is what runs its @panel decorators, so leaving
  that to chance means an empty registry and panel slots that silently
  resolve to None -- which reads as "the panels just don't show" rather than
  as an error. Idempotent, and callable from anywhere that needs the registry
  populated without constructing a PanelHost.

  The import is deferred rather than at module scope because panel_widgets
  imports SidePanel from here, and at import time that class does not exist
  yet.
  """
  from openpilot.selfdrive.ui.components import panel_widgets  # noqa: F401
  return REGISTRY


def panel(key: str):
  def deco(cls):
    REGISTRY.register(key, cls)
    return cls
  return deco


class PanelHost(QWidget):
  """Two panels over the camera, paged by swipe and swapped by hold."""

  LEFT_KEY_DEFAULT = "speed"
  RIGHT_KEY_DEFAULT = "telemetry"

  def __init__(self, parent=None, width_frac: float = 0.31):
    super().__init__(parent)
    register_builtin_panels()
    self._frac = width_frac
    self.left_key = self.LEFT_KEY_DEFAULT
    self.right_key = self.RIGHT_KEY_DEFAULT
    self.left = None
    self.right = None
    self._blocked = False
    self._rebuild()

  def set_blocked(self, blocked: bool) -> None:
    """While a safety warning is up, cycling is refused (section 5.6)."""
    self._blocked = blocked

  def _rebuild(self) -> None:
    for attr, key, side in (("left", self.left_key, "left"),
                            ("right", self.right_key, "right")):
      old = getattr(self, attr)
      if old is not None:
        old.setParent(None)
        old.deleteLater()
      w = REGISTRY.make(key, side, self) if key in REGISTRY._types else None
      setattr(self, attr, w)
      if w is not None:
        w.widget_changed.connect(self._on_cycle)
        w.swap_panels.connect(self._on_swap)
        w.reset_requested.connect(self._on_reset)
        w.show()
    self._layout()

  def _on_cycle(self, panel: str, direction: str) -> None:
    if self._blocked:
      return
    if panel == "left":
      self.left_key = REGISTRY.next_key(self.left_key, direction)
    else:
      self.right_key = REGISTRY.next_key(self.right_key, direction)
    self._rebuild()

  def _on_swap(self) -> None:
    if self._blocked:
      return
    self.left_key, self.right_key = self.right_key, self.left_key
    self._rebuild()

  def _on_reset(self, _panel: str) -> None:
    self.left_key = self.LEFT_KEY_DEFAULT
    self.right_key = self.RIGHT_KEY_DEFAULT
    self._rebuild()

  def set_data(self, data: PanelData) -> None:
    for w in (self.left, self.right):
      if w is not None:
        w.set_data(data)

  def _layout(self) -> None:
    w = int(self.width() * self._frac)
    h = self.height()
    if self.left is not None:
      self.left.setGeometry(0, 0, w, h)
    if self.right is not None:
      self.right.setGeometry(self.width() - w, 0, w, h)

  def resizeEvent(self, event):
    super().resizeEvent(event)
    self._layout()

  def showEvent(self, event):
    super().showEvent(event)
    self._layout()
