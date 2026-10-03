"""Offroad settings.

Nagasware's navigation model, not openpilot's (plan section 5.5): a horizontal
tab bar across the top with grouped pages below, rather than a left sidebar
over a long scrolling list. The geometry decides it -- 1600x600 leaves 500px
of content height between the bars, and a sidebar spends the scarce axis while
tabs spend the abundant one.

Pages are built from `settings/descriptor.py`, so adding a control is a data
change and the coverage gate can prove nothing was dropped.

The descriptor covers the EOP settings only. The openpilot panels -- Device,
WiFi, Bluetooth, Toggles, Software, Developer -- are hand-written and live in
views/panels/, shared byte-for-byte with dev/01M. They are tabs here rather
than a sidebar, which is the only difference between the two branches'
settings. They were missing entirely until now, and the WiFi one is not
optional: with no way to join a network there is no way to update and no way
to pair a phone, so the device has no recovery path but SSH.
"""

from __future__ import annotations

from collections.abc import Callable

from openpilot.selfdrive.ui.components.controls import ControlRow, ParamStore
from openpilot.selfdrive.ui.components.list_controls import ScrollPanel
from openpilot.selfdrive.ui.qt import Qt, QtWidgets, QWidget, Signal
from openpilot.selfdrive.ui.settings.descriptor import PAGES, Page

# Human titles for the descriptor's page keys. Kept here rather than in the
# descriptor because the descriptor is generated, and regenerating it should
# never silently revert a wording change.
PAGE_TITLES = {
  # "EOP Device", not "Device": the openpilot Device panel is a tab here too,
  # and two tabs with the same label is a UI that cannot be navigated.
  "device": "EOP Device",
  "lateral": "Steering",
  "cruise": "Cruise",
  "perception": "Perception",
  "map": "Map & GPS",
  "safety": "Safety",
  "vehicle": "Vehicle",
  "dvr": "Recording",
  "voice": "Voice",
}
PAGE_ORDER = ["device", "lateral", "cruise", "safety", "perception",
              "map", "vehicle", "dvr", "voice"]


class SettingsPage(QtWidgets.QScrollArea):
  """One tab: a scrolling column of ControlRows built from the descriptor."""

  def __init__(self, page: Page, store: ParamStore, parent=None):
    super().__init__(parent)
    self.page = page
    self.setWidgetResizable(True)
    self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    body = QWidget()
    lay = QtWidgets.QVBoxLayout(body)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)

    self.rows = []
    for control in page.controls:
      row = ControlRow(control, store)
      lay.addWidget(row)
      self.rows.append(row)
    lay.addStretch(1)
    self.setWidget(body)

  def showEvent(self, event):
    # Re-read on every entry rather than trusting construction-time values:
    # another page, a daemon, or adb may have moved the param since.
    super().showEvent(event)
    for row in self.rows:
      row.refresh()


class OffroadView(QWidget):
  """Top tab bar plus the page stack."""

  training_guide_requested = Signal()

  def __init__(self, store: ParamStore | None = None, parent=None):
    super().__init__(parent)
    self.setObjectName("offroadRoot")
    self._store = store if store is not None else ParamStore()
    self._engaged = False
    self._offroad = True
    # Declared before any tab is added: QTabWidget emits currentChanged while
    # the first tab is being inserted, so _refresh_current runs once before
    # the constructor has finished.
    self._lazy: dict[int, Callable[[], QWidget]] = {}
    self._built: dict[int, QWidget] = {}

    lay = QtWidgets.QVBoxLayout(self)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)

    self.tabs = QtWidgets.QTabWidget()
    self.tabs.setObjectName("settingsTabs")
    self.tabs.setTabPosition(QtWidgets.QTabWidget.North)
    self.tabs.currentChanged.connect(self._refresh_current)
    lay.addWidget(self.tabs)

    by_name = {p.name: p for p in PAGES}
    ordered = [by_name[n] for n in PAGE_ORDER if n in by_name]
    ordered += [p for p in PAGES if p.name not in PAGE_ORDER]

    self.pages = {}
    for page in ordered:
      widget = SettingsPage(page, self._store)
      self.pages[page.name] = widget
      self.tabs.addTab(widget, PAGE_TITLES.get(page.name, page.name.title()))

    # The openpilot panels follow the EOP pages, built on first visit: each
    # one constructs its whole control list, and WiFi and Bluetooth open DBus
    # connections to NetworkManager and BlueZ. None of that should happen
    # before the tab is actually selected.
    for title, builder in self._openpilot_panels():
      index = self.tabs.addTab(QWidget(), title)
      self._lazy[index] = builder

  def _openpilot_panels(self):
    return [
      ("Device", self._build_device),
      ("WiFi", self._build_wifi),
      ("Bluetooth", self._build_bluetooth),
      ("Toggles", self._build_toggles),
      ("Software", self._build_software),
      ("Developer", self._build_developer),
    ]

  def _build_device(self):
    from openpilot.selfdrive.ui.views.panels.device import DevicePanel
    panel = DevicePanel(self._store)
    panel.training_guide_requested.connect(self.training_guide_requested)
    return panel

  def _build_wifi(self):
    from openpilot.selfdrive.ui.views.panels.network import WifiPanel
    return WifiPanel()

  def _build_bluetooth(self):
    from openpilot.selfdrive.ui.views.panels.network import BluetoothPanel
    return BluetoothPanel(self._store)

  def _build_toggles(self):
    from openpilot.selfdrive.ui.views.panels.toggles import TogglesPanel
    return TogglesPanel(self._store)

  def _build_software(self):
    from openpilot.selfdrive.ui.views.panels.software import SoftwarePanel
    return SoftwarePanel(self._store)

  def _build_developer(self):
    from openpilot.selfdrive.ui.views.panels.toggles import DeveloperPanel
    return DeveloperPanel(self._store)

  def _ensure_built(self, index: int) -> QWidget | None:
    # Popped before the builder runs, not after. removeTab/insertTab below
    # each re-emit currentChanged, which comes straight back here -- leaving
    # the entry in place until the end made that an unbounded recursion that
    # blew the stack the first time an openpilot tab was opened.
    builder = self._lazy.pop(index, None)
    if builder is None:
      return self._built.get(index)

    body = builder()
    body.setContentsMargins(0, 16, 0, 16)
    panel = ScrollPanel(body)
    title = self.tabs.tabText(index)
    placeholder = self.tabs.widget(index)

    # Swapping the placeholder out shifts the current index and fires
    # currentChanged twice on the way through. Nothing useful can happen
    # mid-swap, so the signals are suppressed and the selection restored.
    blocked = self.tabs.blockSignals(True)
    try:
      self.tabs.removeTab(index)
      self.tabs.insertTab(index, panel, title)
      self.tabs.setCurrentIndex(index)
    finally:
      self.tabs.blockSignals(blocked)
    placeholder.deleteLater()

    self._built[index] = panel
    self._apply_driving_state_to(body)
    return panel

  def _refresh_current(self, index: int) -> None:
    panel = self._ensure_built(index)
    page = panel if panel is not None else self.tabs.widget(index)
    rows = getattr(page, "rows", None)
    if rows is not None:
      for row in rows:
        row.refresh()
      return
    body = getattr(page, "body", None)
    refresh = getattr(body, "refresh", None)
    if refresh is not None:
      refresh()

  def set_driving_state(self, engaged: bool, offroad: bool) -> None:
    self._engaged = engaged
    self._offroad = offroad
    for panel in self._built.values():
      self._apply_driving_state_to(panel.body)

  def _apply_driving_state_to(self, body: QWidget) -> None:
    for setter, args in (("set_driving_state", (self._engaged, self._offroad)),
                         ("set_offroad", (self._offroad,)),
                         ("set_engaged", (self._engaged,))):
      fn = getattr(body, setter, None)
      if fn is not None:
        fn(*args)

  def page_names(self) -> list[str]:
    return [self.tabs.tabText(i) for i in range(self.tabs.count())]
