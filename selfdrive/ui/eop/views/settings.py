"""The classic settings window.

Port of settings.cc's SettingsWindow: a 210px nav column on the left with a
close button above a list of panel names, and the selected panel filling the
rest. Deliberately not 02M's top tab bar -- 01M keeps the navigation a driver
already knows.

Panels are constructed lazily. Building all seven up front means constructing
every control and opening a DBus connection to NetworkManager and BlueZ
before the user has asked for any of it, on a screen they reach by tapping
SETTINGS while parked.
"""

from __future__ import annotations

from collections.abc import Callable

from openpilot.selfdrive.ui.eop.components.controls import ParamStore
from openpilot.selfdrive.ui.eop.components.list_controls import ScrollPanel
from openpilot.selfdrive.ui.eop.components.theme import SETTINGS_NAV_W
from openpilot.selfdrive.ui.eop.qt import Qt, QtWidgets, QWidget, Signal

SETTINGS_STYLE = """
SettingsWindow { background-color: black; }
QWidget#settingsRoot { background-color: black; }
* { color: white; font-size: 24px; }
QScrollArea, QWidget#panelHost { background-color: #292929; border-radius: 18px; }
QPushButton#navButton {
  color: grey;
  border: none;
  background: none;
  font-size: 22px;
  font-weight: 500;
  text-align: right;
  padding-right: 0px;
}
QPushButton#navButton:checked { color: white; }
QPushButton#navButton:pressed { color: #ADADAD; }
QPushButton#closeButton {
  font-size: 32px;
  padding-bottom: 4px;
  border-radius: 35px;
  background-color: #292929;
  font-weight: 400;
}
QPushButton#closeButton:pressed { background-color: #3B3B3B; }
QPushButton#controlButton {
  color: white;
  background-color: #393939;
  border-radius: 10px;
  padding: 0px 24px;
  font-size: 22px;
  font-weight: 500;
}
QPushButton#controlButton:pressed { background-color: #4a4a4a; }
QPushButton#controlButton:checked { background-color: #465BEA; }
QPushButton#controlButton:disabled { color: #777777; }
"""

# Panels that are wider than the rest get no side padding, because they lay
# out their own lists edge to edge.
FULL_BLEED = ("WiFi", "Bluetooth")


class SettingsWindow(QWidget):
  """Nav column plus the panel stack."""

  closed = Signal()
  training_guide_requested = Signal()

  def __init__(self, store: ParamStore | None = None, parent=None):
    super().__init__(parent)
    self.setObjectName("settingsRoot")
    self.setStyleSheet(SETTINGS_STYLE)
    self._store = store if store is not None else ParamStore()

    root = QtWidgets.QHBoxLayout(self)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)

    nav_host = QWidget()
    nav_host.setFixedWidth(SETTINGS_NAV_W)
    nav = QtWidgets.QVBoxLayout(nav_host)
    nav.setContentsMargins(25, 25, 50, 25)

    close = QtWidgets.QPushButton("×")
    close.setObjectName("closeButton")
    close.setFixedSize(70, 70)
    close.clicked.connect(self.closed)
    nav.addSpacing(25)
    nav.addWidget(close, 0, Qt.AlignCenter)

    self.stack = QtWidgets.QStackedWidget()
    self.stack.setObjectName("panelHost")

    self._buttons: list[QtWidgets.QPushButton] = []
    self._names: list[str] = []
    self._builders: list[Callable[[], QWidget]] = []
    self._panels: dict[int, QWidget] = {}
    self._nav_layout = nav

    for name, builder in self._panel_builders():
      self._add_panel(name, builder)
    nav.addStretch(1)

    root.addWidget(nav_host)
    root.addWidget(self.stack, 1)

    self._engaged = False
    self._offroad = True
    if self._buttons:
      self.set_current_panel(0)

  # ---- construction -----------------------------------------------------

  def _panel_builders(self):
    """Panel order is openpilot's, with ExoPilot last -- the same order the
    C++ used, so muscle memory carries over."""
    return [
      ("Device", self._build_device),
      ("WiFi", self._build_wifi),
      ("Bluetooth", self._build_bluetooth),
      ("Toggles", self._build_toggles),
      ("Software", self._build_software),
      ("Developer", self._build_developer),
      ("ExoPilot", self._build_exopilot),
    ]

  def _add_panel(self, name: str, builder) -> None:
    index = len(self._buttons)
    button = QtWidgets.QPushButton(name)
    button.setObjectName("navButton")
    button.setCheckable(True)
    button.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                         QtWidgets.QSizePolicy.Expanding)
    button.clicked.connect(lambda _checked=False, i=index: self.set_current_panel(i))
    self._nav_layout.addWidget(button, 0, Qt.AlignRight)

    # A placeholder holds the slot so indices line up before the real panel
    # is built; it is replaced in place on first visit.
    self.stack.addWidget(QWidget())
    self._buttons.append(button)
    self._names.append(name)
    self._builders.append(builder)

  def _ensure_panel(self, index: int) -> QWidget:
    panel = self._panels.get(index)
    if panel is not None:
      return panel
    name = self._names[index]
    body = self._builders[index]()
    margin = 0 if name in FULL_BLEED else 50
    body.setContentsMargins(margin, 25, margin, 25)
    panel = ScrollPanel(body)
    placeholder = self.stack.widget(index)
    self.stack.insertWidget(index, panel)
    self.stack.removeWidget(placeholder)
    placeholder.deleteLater()
    self._panels[index] = panel
    self._apply_driving_state_to(body)
    return panel

  # ---- panels -----------------------------------------------------------

  def _build_device(self):
    from openpilot.selfdrive.ui.eop.views.panels.device import DevicePanel
    panel = DevicePanel(self._store)
    panel.training_guide_requested.connect(self.training_guide_requested)
    return panel

  def _build_toggles(self):
    from openpilot.selfdrive.ui.eop.views.panels.toggles import TogglesPanel
    return TogglesPanel(self._store)

  def _build_developer(self):
    from openpilot.selfdrive.ui.eop.views.panels.toggles import DeveloperPanel
    return DeveloperPanel(self._store)

  def _build_software(self):
    from openpilot.selfdrive.ui.eop.views.panels.software import SoftwarePanel
    return SoftwarePanel(self._store)

  def _build_exopilot(self):
    from openpilot.selfdrive.ui.eop.views.panels.exopilot import ExoPilotPanel
    return ExoPilotPanel(self._store)

  def _build_wifi(self):
    from openpilot.selfdrive.ui.eop.views.panels.network import WifiPanel
    return WifiPanel()

  def _build_bluetooth(self):
    from openpilot.selfdrive.ui.eop.views.panels.network import BluetoothPanel
    return BluetoothPanel(self._store)

  # ---- navigation -------------------------------------------------------

  def panel_names(self) -> list[str]:
    return list(self._names)

  def set_current_panel(self, index: int) -> None:
    if not 0 <= index < len(self._buttons):
      return
    self._ensure_panel(index)
    self.stack.setCurrentIndex(index)
    for i, button in enumerate(self._buttons):
      button.setChecked(i == index)

  def open_panel(self, name: str) -> bool:
    """Select a panel by name.

    By name rather than by index deliberately: the C++ had callers passing a
    hard-coded index alongside a param name, so inserting a panel silently
    misrouted them to the wrong one.
    """
    if name not in self._names:
      return False
    self.set_current_panel(self._names.index(name))
    return True

  def showEvent(self, event):
    super().showEvent(event)
    self.set_current_panel(0)

  # ---- driving state ----------------------------------------------------

  def set_driving_state(self, engaged: bool, offroad: bool) -> None:
    """Panels that can change how the car drives lock themselves while it is
    moving. Each decides for itself -- Reset Calibration stays available
    onroad because it guards on engagement, which is the tighter condition."""
    self._engaged = engaged
    self._offroad = offroad
    for panel in self._panels.values():
      self._apply_driving_state_to(panel.body)

  def _apply_driving_state_to(self, body: QWidget) -> None:
    for setter, args in (("set_driving_state", (self._engaged, self._offroad)),
                         ("set_offroad", (self._offroad,)),
                         ("set_engaged", (self._engaged,))):
      fn = getattr(body, setter, None)
      if fn is not None:
        fn(*args)
