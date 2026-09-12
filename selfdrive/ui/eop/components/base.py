"""Widget class spine, ported from Nagasware's components/base/.

Nagasware shipped two different classes both named `BaseOverlay`
(base/base_overlay.py and base/onroad_base_overlay.py) with different signals.
Collapsed to one here.
"""

from __future__ import annotations

from openpilot.selfdrive.ui.eop.qt import QTimer, QWidget, Signal


class BaseWidget(QWidget):
  widget_ready = Signal()
  state_changed = Signal(str)

  def __init__(self, name: str, parent: QWidget | None = None):
    super().__init__(parent)
    self.widget_name = name
    self.setObjectName(name)
    self._ready = False

  def initialize(self) -> None:
    if self._ready:
      return
    self._setup()
    self._ready = True
    self.widget_ready.emit()

  def _setup(self) -> None:
    """Subclass hook."""

  def is_ready(self) -> bool:
    return self._ready


class BaseTimerWidget(BaseWidget):
  """A widget with its own refresh tick, stopped when hidden.

  Stopping on hide matters: these are panels that get swapped constantly by
  the gesture system, and a hidden panel still ticking is pure drain.
  """

  def __init__(self, name: str, interval_ms: int = 1000, parent=None):
    super().__init__(name, parent)
    self._timer = QTimer(self)
    self._timer.setInterval(interval_ms)
    self._timer.timeout.connect(self.refresh)

  def refresh(self) -> None:
    """Subclass hook."""

  def showEvent(self, event):
    super().showEvent(event)
    self._timer.start()
    self.refresh()

  def hideEvent(self, event):
    super().hideEvent(event)
    self._timer.stop()


class BaseOverlay(BaseWidget):
  """Translucent, click-through layer over the camera."""

  data_updated = Signal(dict)

  def __init__(self, name: str, parent=None):
    from openpilot.selfdrive.ui.eop.qt import Qt
    super().__init__(name, parent)
    self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    self.setAttribute(Qt.WA_TranslucentBackground, True)
    self.setAutoFillBackground(False)

  def update_data(self, data: dict) -> None:
    self.data_updated.emit(data)
    self.update()


class BasePage(BaseWidget):
  page_loaded = Signal()
  navigation_requested = Signal(str)

  def __init__(self, name: str, parent=None):
    super().__init__(name, parent)

  def showEvent(self, event):
    super().showEvent(event)
    self.initialize()
    self.page_loaded.emit()
