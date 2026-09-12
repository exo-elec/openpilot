"""The offroad home screen.

Port of home.cc's OffroadHome: a header carrying the date, the version, and
notification chips for a pending update or an offroad alert, above a content
area that is normally the drive summary and switches to the update or alert
detail when one is tapped.
"""

from __future__ import annotations

import json
from datetime import datetime

from openpilot.selfdrive.ui.eop.components.controls import ParamStore
from openpilot.selfdrive.ui.eop.qt import Qt, QTimer, QtWidgets, QWidget, Signal

# Refreshed on a slow tick: everything here is a date, a version string or an
# alert count, none of which changes at anything like frame rate.
REFRESH_MS = 10_000

HOME_STYLE = """
QWidget#offroadHome { background-color: black; }
QWidget#offroadHome QLabel { color: white; font-size: 32px; }
QPushButton#updateChip, QPushButton#alertChip {
  padding: 10px 20px;
  border-radius: 5px;
  font-size: 24px;
  font-weight: 500;
  color: white;
}
QPushButton#updateChip { background-color: #364DEF; }
QPushButton#alertChip { background-color: #E22C2C; }
QWidget#homeCard { background-color: #292929; border-radius: 10px; }
QLabel#statValue { font-size: 48px; font-weight: 600; }
QLabel#statLabel { font-size: 22px; color: #A0A0A0; }
QPushButton#experimentalBtn {
  background-color: #292929;
  border-radius: 10px;
  font-size: 28px;
  font-weight: 500;
  color: white;
  padding: 30px;
  text-align: left;
}
QPushButton#experimentalBtn:pressed { background-color: #3B3B3B; }
"""

PAGE_HOME, PAGE_UPDATE, PAGE_ALERTS = 0, 1, 2


def format_stat(metres: float, is_metric: bool) -> str:
  if is_metric:
    return f"{metres / 1000.0:.0f}"
  return f"{metres / 1609.34:.0f}"


def read_drive_stats(store: ParamStore, is_metric: bool) -> list[tuple[str, str]]:
  """(value, label) pairs for the drive summary card.

  Reads the same CompletedTrainingVersion-adjacent stats blob the C++ used.
  A missing or malformed blob shows zeros rather than an empty card, because
  a brand-new device legitimately has no drives yet and that is not an error.
  """
  raw = store.get_text("ApiCache_DriveStats")
  unit = "km" if is_metric else "mi"
  try:
    stats = json.loads(raw) if raw else {}
  except ValueError:
    stats = {}

  all_time = stats.get("all", {}) if isinstance(stats, dict) else {}
  week = stats.get("week", {}) if isinstance(stats, dict) else {}
  return [
    (str(all_time.get("routes", 0)), "drives"),
    (format_stat(float(all_time.get("distance", 0.0)), is_metric), f"{unit} total"),
    (format_stat(float(week.get("distance", 0.0)), is_metric), f"{unit} this week"),
    (f"{float(all_time.get('minutes', 0.0)) / 60:.0f}", "hours"),
  ]


class StatCard(QWidget):
  """The drive summary: four numbers in a row."""

  def __init__(self, parent=None):
    super().__init__(parent)
    self.setObjectName("homeCard")
    lay = QtWidgets.QHBoxLayout(self)
    lay.setContentsMargins(30, 30, 30, 30)
    lay.setSpacing(20)
    self._values, self._labels = [], []
    for _ in range(4):
      column = QtWidgets.QVBoxLayout()
      value = QtWidgets.QLabel("0")
      value.setObjectName("statValue")
      label = QtWidgets.QLabel("")
      label.setObjectName("statLabel")
      for w in (value, label):
        w.setAlignment(Qt.AlignCenter)
        column.addWidget(w)
      lay.addLayout(column, 1)
      self._values.append(value)
      self._labels.append(label)

  def set_stats(self, stats: list[tuple[str, str]]) -> None:
    for (value, label), v_widget, l_widget in zip(stats, self._values,
                                                  self._labels, strict=False):
      v_widget.setText(value)
      l_widget.setText(label)


class OffroadHome(QWidget):
  """Header plus the content stack."""

  settings_requested = Signal(str)     # panel name, "" for the default

  def __init__(self, store: ParamStore | None = None, parent=None):
    super().__init__(parent)
    self.setObjectName("offroadHome")
    self.setStyleSheet(HOME_STYLE)
    self._store = store if store is not None else ParamStore()
    self._is_metric = True

    root = QtWidgets.QVBoxLayout(self)
    root.setContentsMargins(20, 20, 20, 20)

    header = QtWidgets.QHBoxLayout()
    header.setSpacing(10)
    self.update_chip = QtWidgets.QPushButton("UPDATE")
    self.update_chip.setObjectName("updateChip")
    self.update_chip.hide()
    self.update_chip.clicked.connect(lambda: self._show_page(PAGE_UPDATE))
    header.addWidget(self.update_chip, 0, Qt.AlignLeft)

    self.alert_chip = QtWidgets.QPushButton()
    self.alert_chip.setObjectName("alertChip")
    self.alert_chip.hide()
    self.alert_chip.clicked.connect(lambda: self._show_page(PAGE_ALERTS))
    header.addWidget(self.alert_chip, 0, Qt.AlignLeft)

    self.date = QtWidgets.QLabel()
    header.addWidget(self.date, 1, Qt.AlignLeft)
    self.version = QtWidgets.QLabel()
    header.addWidget(self.version, 0, Qt.AlignRight)
    root.addLayout(header)
    root.addSpacing(15)

    self.stack = QtWidgets.QStackedWidget()
    self.stack.addWidget(self._build_home_page())
    self.update_page = self._build_text_page("Update", PAGE_UPDATE)
    self.stack.addWidget(self.update_page)
    self.alerts_page = self._build_text_page("Alerts", PAGE_ALERTS)
    self.stack.addWidget(self.alerts_page)
    root.addWidget(self.stack, 1)

    self._timer = QTimer(self)
    self._timer.setInterval(REFRESH_MS)
    self._timer.timeout.connect(self.refresh)

  def _build_home_page(self) -> QWidget:
    page = QWidget()
    lay = QtWidgets.QHBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(18)

    self.stats = StatCard()
    lay.addWidget(self.stats, 1)

    right = QWidget()
    right.setFixedWidth(350)
    column = QtWidgets.QVBoxLayout(right)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(18)

    self.experimental_btn = QtWidgets.QPushButton()
    self.experimental_btn.setObjectName("experimentalBtn")
    # Goes straight to the toggle rather than to the top of settings: it is
    # the one setting this button exists to talk about.
    self.experimental_btn.clicked.connect(
      lambda: self.settings_requested.emit("Toggles"))
    column.addWidget(self.experimental_btn, 1)

    self.exopilot_btn = QtWidgets.QPushButton("ExoPilot Settings")
    self.exopilot_btn.setObjectName("experimentalBtn")
    self.exopilot_btn.clicked.connect(
      lambda: self.settings_requested.emit("ExoPilot"))
    column.addWidget(self.exopilot_btn, 1)

    lay.addWidget(right)
    return page

  def _build_text_page(self, title: str, page: int) -> QWidget:
    host = QWidget()
    lay = QtWidgets.QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)

    heading = QtWidgets.QLabel(title)
    lay.addWidget(heading)

    body = QtWidgets.QTextEdit()
    body.setReadOnly(True)
    body.setStyleSheet("background-color: #292929; border-radius: 10px; color: white; font-size: 24px; padding: 20px;")
    lay.addWidget(body, 1)

    dismiss = QtWidgets.QPushButton("Dismiss")
    dismiss.setObjectName("experimentalBtn")
    dismiss.clicked.connect(lambda: self._show_page(PAGE_HOME))
    lay.addWidget(dismiss)

    host.body = body
    return host

  # ---- state ------------------------------------------------------------

  def set_metric(self, is_metric: bool) -> None:
    if is_metric != self._is_metric:
      self._is_metric = is_metric
      self.refresh()

  def _show_page(self, page: int) -> None:
    self.stack.setCurrentIndex(page)

  def showEvent(self, event):
    super().showEvent(event)
    self.refresh()
    self._timer.start()

  def hideEvent(self, event):
    super().hideEvent(event)
    self._timer.stop()

  def refresh(self) -> None:
    self.date.setText(datetime.now().strftime("%A, %B %-d"))
    # The clock is wrong until NTP lands, and a confidently wrong date is
    # worse than none. 2020 is the same sanity floor ui.cc used.
    self.date.setVisible(datetime.now().year >= 2020)

    self.version.setText(
      f"ExoPilot {self._store.get_text('UpdaterCurrentDescription')}".strip())
    self.stats.set_stats(read_drive_stats(self._store, self._is_metric))
    self.experimental_btn.setText(
      "Experimental Mode\nON" if self._store.get_bool("ExperimentalMode")
      else "Experimental Mode\nOFF")

    update_available = self._store.get_bool("UpdateAvailable")
    self.update_page.body.setPlainText(
      self._store.get_text("UpdaterNewReleaseNotes"))
    self.update_chip.setVisible(update_available)

    alerts = self._alert_texts()
    self.alerts_page.body.setPlainText("\n\n".join(alerts))
    self.alert_chip.setVisible(bool(alerts))
    if alerts:
      n = len(alerts)
      self.alert_chip.setText(f"{n} ALERT" + ("S" if n > 1 else ""))

    # A page whose reason has gone away must not stay up: dismissing happens
    # by the alert clearing as much as by the button.
    current = self.stack.currentIndex()
    if current == PAGE_UPDATE and not update_available:
      self._show_page(PAGE_HOME)
    elif current == PAGE_ALERTS and not alerts:
      self._show_page(PAGE_HOME)

  def _alert_texts(self) -> list[str]:
    raw = self._store.get_text("OffroadAlerts")
    if not raw:
      # Each alert is also published under its own key by the daemon that
      # raised it; the aggregate key is the newer form.
      return []
    try:
      parsed = json.loads(raw)
    except ValueError:
      return []
    if isinstance(parsed, dict):
      return [str(v) for v in parsed.values() if v]
    if isinstance(parsed, list):
      return [str(v) for v in parsed if v]
    return []
