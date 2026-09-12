"""The top-level window.

Port of window.cc plus home.cc's HomeWindow. Three screens in a stack --
onboarding, home (sidebar beside either the offroad home or the driving view),
and settings -- with the sidebar collapsing onroad so the camera gets the
whole panel, and reappearing on a tap.

Closing settings on an offroad-to-onroad transition is deliberate and is
safety behaviour, not tidiness: the driving view must not be buried under a
settings screen the moment the car starts moving.
"""

from __future__ import annotations

from openpilot.selfdrive.ui.eop.components.controls import ParamStore
from openpilot.selfdrive.ui.eop.components.theme import SCREEN_H, SCREEN_W
from openpilot.selfdrive.ui.eop.qt import (
  QColor,
  QPainter,
  Qt,
  QtWidgets,
  QWidget,
)
from openpilot.selfdrive.ui.eop.state import Snapshot, UIStatus
from openpilot.selfdrive.ui.eop.views.home import OffroadHome
from openpilot.selfdrive.ui.eop.views.onroad import OnroadView
from openpilot.selfdrive.ui.eop.views.settings import SettingsWindow
from openpilot.selfdrive.ui.eop.views.sidebar import Sidebar

PAGE_HOME, PAGE_SETTINGS, PAGE_ONBOARDING = 0, 1, 2


class HomeWindow(QWidget):
  """Sidebar beside whichever of home/onroad is current."""

  def __init__(self, store: ParamStore, live_camera: bool = True, parent=None):
    super().__init__(parent)
    self.setObjectName("homeWindow")

    lay = QtWidgets.QHBoxLayout(self)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)

    self.sidebar = Sidebar(store=store, parent=self)
    lay.addWidget(self.sidebar)

    self.stack = QtWidgets.QStackedWidget()
    self.offroad = OffroadHome(store)
    self.onroad = OnroadView(live_camera=live_camera, store=store)
    self.stack.addWidget(self.offroad)
    self.stack.addWidget(self.onroad)
    lay.addWidget(self.stack, 1)

    self._started = False

  def set_started(self, started: bool) -> None:
    if started == self._started:
      return
    self._started = started
    self.stack.setCurrentWidget(self.onroad if started else self.offroad)
    # Onroad the camera gets the whole panel; the sidebar is one tap away.
    self.sidebar.setVisible(not started)

  def toggle_sidebar(self) -> None:
    if self._started:
      self.sidebar.setVisible(not self.sidebar.isVisible())

  def mousePressEvent(self, event):
    super().mousePressEvent(event)
    # Only a tap on the camera toggles the sidebar -- a tap inside the
    # sidebar is for its own buttons.
    if self._started and event.pos().x() > self.sidebar.width():
      self.toggle_sidebar()


class MainWindow(QWidget):
  """Onboarding, home and settings."""

  def __init__(self, store: ParamStore | None = None, live_camera: bool = True,
               parent=None):
    super().__init__(parent)
    self.setObjectName("mainWindow")
    self.setAttribute(Qt.WA_OpaquePaintEvent, True)
    self._store = store if store is not None else ParamStore()

    self.stack = QtWidgets.QStackedLayout(self)
    self.stack.setContentsMargins(0, 0, 0, 0)

    self.home = HomeWindow(self._store, live_camera=live_camera)
    self.stack.addWidget(self.home)

    self.settings = SettingsWindow(self._store)
    self.settings.closed.connect(self.close_settings)
    self.settings.training_guide_requested.connect(self._show_onboarding)
    self.stack.addWidget(self.settings)

    self.onboarding = self._build_onboarding()
    self.stack.addWidget(self.onboarding)

    self.home.sidebar.settings_clicked.connect(self.open_settings)
    self.home.offroad.settings_requested.connect(self.open_settings)

    # completed() compares stored version strings; CompletedTrainingVersion
    # holds a version, not a bool, so reading it as one would have shown
    # onboarding on every boot of an already-onboarded device.
    if not self.onboarding.completed():
      self.stack.setCurrentIndex(PAGE_ONBOARDING)

    self.resize(SCREEN_W, SCREEN_H)

  def _build_onboarding(self) -> QWidget:
    from openpilot.selfdrive.ui.eop.views.onboarding import OnboardingView
    view = OnboardingView(self._store)
    view.finished.connect(lambda: self.stack.setCurrentIndex(PAGE_HOME))
    return view

  def _show_onboarding(self) -> None:
    self.stack.setCurrentIndex(PAGE_ONBOARDING)

  # ---- navigation -------------------------------------------------------

  def open_settings(self, panel: str = "") -> None:
    self.stack.setCurrentIndex(PAGE_SETTINGS)
    if panel:
      self.settings.open_panel(panel)

  def close_settings(self) -> None:
    self.stack.setCurrentIndex(PAGE_HOME)

  # ---- state ------------------------------------------------------------

  def set_snapshot(self, snap: Snapshot) -> None:
    self.home.sidebar.set_snapshot(snap)
    self.home.offroad.set_metric(snap.is_metric)
    if snap.started:
      self.home.onroad.set_snapshot(snap)
    self.settings.set_driving_state(
      engaged=snap.status in (UIStatus.ENGAGED, UIStatus.OVERRIDE),
      offroad=not snap.started)

  def set_started(self, started: bool) -> None:
    self.home.set_started(started)
    if started and self.stack.currentIndex() == PAGE_SETTINGS:
      # The car started while settings were open. The driving view has to
      # come forward on its own -- waiting for the driver to close settings
      # means driving with the road view hidden.
      self.close_settings()

  def paintEvent(self, event):
    p = QPainter(self)
    p.fillRect(self.rect(), QColor(0, 0, 0))
