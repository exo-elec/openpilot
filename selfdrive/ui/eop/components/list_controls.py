"""The row types the classic settings list is built from.

Port of widgets/controls.cc: a title, an optional wrapping description, and
something on the right -- a value, a button, or a toggle. Rows are separated
by a hairline and the description is collapsed until the title is tapped,
which is what keeps a panel of thirty settings scannable.

components/controls.py already owns the descriptor-driven ControlRow used by
the generated ExoPilot pages. These are the hand-written rows the openpilot
panels need, and they share ParamStore rather than each reaching for Params.
"""

from __future__ import annotations

from openpilot.selfdrive.ui.eop.components.controls import ParamStore
from openpilot.selfdrive.ui.eop.qt import (
  QColor,
  QFont,
  QPainter,
  Qt,
  QtWidgets,
  QWidget,
  Signal,
)

ROW_MARGINS = (50, 24, 50, 24)
TITLE_PX = 24
DESC_PX = 20
SEPARATOR = QColor(0x29, 0x29, 0x29)


class AbstractControl(QWidget):
  """Title, collapsible description, and a right-hand control."""

  description_shown = Signal()

  def __init__(self, title: str, description: str = "", parent=None):
    super().__init__(parent)
    self.setObjectName("listRow")

    outer = QtWidgets.QVBoxLayout(self)
    outer.setContentsMargins(*ROW_MARGINS)
    outer.setSpacing(0)

    line = QtWidgets.QHBoxLayout()
    line.setSpacing(20)
    self.title_label = QtWidgets.QLabel(title)
    self.title_label.setObjectName("controlTitle")
    self.title_label.setWordWrap(True)
    font = self.title_label.font()
    font.setPixelSize(TITLE_PX)
    font.setWeight(QFont.DemiBold)
    self.title_label.setFont(font)
    line.addWidget(self.title_label, 1, Qt.AlignLeft | Qt.AlignVCenter)
    self._line = line
    self._value_label: QtWidgets.QLabel | None = None
    outer.addLayout(line)

    self.desc_label = QtWidgets.QLabel(description)
    self.desc_label.setObjectName("controlDesc")
    self.desc_label.setWordWrap(True)
    desc_font = self.desc_label.font()
    desc_font.setPixelSize(DESC_PX)
    self.desc_label.setFont(desc_font)
    self.desc_label.setStyleSheet("color: #A0A0A0;")
    self.desc_label.hide()
    outer.addWidget(self.desc_label)

  def set_description(self, text: str) -> None:
    self.desc_label.setText(text)

  def description(self) -> str:
    return self.desc_label.text()

  def toggle_description(self) -> None:
    if not self.desc_label.text():
      return
    showing = not self.desc_label.isVisible()
    self.desc_label.setVisible(showing)
    if showing:
      # Emitted before the text is read, so a row whose description is
      # computed (calibration progress, say) can refresh it on the way open
      # rather than recomputing it on every tick.
      self.description_shown.emit()

  def mouseReleaseEvent(self, event):
    super().mouseReleaseEvent(event)
    if self.title_label.geometry().contains(event.pos()):
      self.toggle_description()

  def add_right(self, widget: QWidget) -> None:
    self._line.addWidget(widget, 0, Qt.AlignRight | Qt.AlignVCenter)

  def set_value(self, value: str) -> None:
    """A status string between the title and the right-hand control.

    Created on first use: most rows never have one, and an always-present
    empty QLabel per row is a widget per row for nothing.
    """
    if self._value_label is None:
      self._value_label = QtWidgets.QLabel()
      self._value_label.setObjectName("controlValue")
      self._value_label.setStyleSheet("color: #AAAAAA;")
      self._value_label.setWordWrap(True)
      self._line.insertWidget(1, self._value_label, 0, Qt.AlignRight | Qt.AlignVCenter)
    self._value_label.setText(value)

  def refresh(self) -> None:
    """Re-read whatever this row is bound to. Rows with nothing to re-read
    inherit the no-op."""


class LabelControl(AbstractControl):
  """A read-only value: dongle ID, serial, version."""

  def __init__(self, title: str, value: str = "", description: str = "", parent=None):
    super().__init__(title, description, parent)
    self.set_value(value)


class ButtonControl(AbstractControl):
  """A row that does something when its button is pressed."""

  clicked = Signal()

  def __init__(self, title: str, button_text: str, description: str = "", parent=None):
    super().__init__(title, description, parent)
    self.button = QtWidgets.QPushButton(button_text)
    self.button.setObjectName("controlButton")
    self.button.setFixedHeight(48)
    self.button.setMinimumWidth(140)
    self.button.clicked.connect(self.clicked)
    self.add_right(self.button)

  def set_button_text(self, text: str) -> None:
    self.button.setText(text)

  def set_enabled(self, enabled: bool) -> None:
    self.button.setEnabled(enabled)


class ToggleControl(AbstractControl):
  """A bool bound straight to a param."""

  toggled = Signal(bool)

  def __init__(self, param: str, title: str, description: str = "",
               store: ParamStore | None = None, confirm: bool = False, parent=None):
    super().__init__(title, description, parent)
    self.param = param
    self._store = store if store is not None else ParamStore()
    self._confirm = confirm

    self.toggle = QtWidgets.QCheckBox()
    self.toggle.setObjectName("controlToggle")
    self.toggle.setChecked(self._read())
    self.toggle.clicked.connect(self._on_clicked)
    self.add_right(self.toggle)

  def _read(self) -> bool:
    # Deliberately unguarded. Params raises UnknownKeyName for a key that is
    # not declared in params_keys.h, and a settings row bound to a key that
    # does not exist is dead UI that silently does nothing when toggled --
    # exactly the defect the params-coverage test exists to catch. Swallowing
    # it here would hide it again.
    return self._store.get_bool(self.param)

  def _on_clicked(self, checked: bool) -> None:
    if checked and self._confirm and not self._ask():
      # Put the widget back: the user said no, so the param must not move and
      # neither must the control showing it.
      self.toggle.setChecked(False)
      return
    self._store.put_bool(self.param, checked)
    self.toggled.emit(checked)

  def _ask(self) -> bool:
    from openpilot.selfdrive.ui.eop.components.dialogs import confirm_dialog
    return confirm_dialog(self.title_label.text(), "Enable", self)

  def refresh(self) -> None:
    self.toggle.blockSignals(True)
    self.toggle.setChecked(self._read())
    self.toggle.blockSignals(False)

  def set_enabled(self, enabled: bool) -> None:
    self.toggle.setEnabled(enabled)


class ButtonParamControl(AbstractControl):
  """An exclusive row of buttons; the chosen index is the param value."""

  changed = Signal(int)

  def __init__(self, param: str, title: str, description: str,
               options: list[str], store: ParamStore | None = None, parent=None):
    super().__init__(title, description, parent)
    self.param = param
    self._store = store if store is not None else ParamStore()

    box = QWidget()
    lay = QtWidgets.QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    self.buttons = []
    current = int(self._store.get_number(param, 0))
    for i, label in enumerate(options):
      b = QtWidgets.QPushButton(label)
      b.setObjectName("controlButton")
      b.setCheckable(True)
      b.setFixedHeight(48)
      b.setChecked(i == current)
      b.clicked.connect(lambda _checked=False, idx=i: self._select(idx))
      lay.addWidget(b)
      self.buttons.append(b)
    self.add_right(box)

  def _select(self, index: int) -> None:
    self.set_checked_button(index)
    self._store.put_number(self.param, index)
    self.changed.emit(index)

  def set_checked_button(self, index: int) -> None:
    for i, b in enumerate(self.buttons):
      b.setChecked(i == index)

  def refresh(self) -> None:
    self.set_checked_button(int(self._store.get_number(self.param, 0)))


class ListWidget(QWidget):
  """A column of rows with a hairline between them.

  The separator is painted rather than inserted as a widget, which is how the
  C++ did it: a QFrame per gap doubles the child count of a long panel for
  one line of pixels.
  """

  def __init__(self, spacing: int = 0, parent=None):
    super().__init__(parent)
    self.setObjectName("listWidget")
    self._layout = QtWidgets.QVBoxLayout(self)
    self._layout.setContentsMargins(0, 0, 0, 0)
    self._layout.setSpacing(spacing)
    self.rows: list[QWidget] = []

  def add_row(self, widget: QWidget) -> QWidget:
    self._layout.addWidget(widget)
    self.rows.append(widget)
    return widget

  def add_layout(self, layout) -> None:
    self._layout.addLayout(layout)

  def add_stretch(self) -> None:
    self._layout.addStretch(1)

  def refresh(self) -> None:
    for row in self.rows:
      fn = getattr(row, "refresh", None)
      if fn is not None:
        fn()

  def paintEvent(self, event):
    if len(self.rows) < 2:
      return
    p = QPainter(self)
    p.setPen(SEPARATOR)
    for row in self.rows[:-1]:
      if not row.isVisible():
        continue
      y = row.geometry().bottom()
      p.drawLine(ROW_MARGINS[0], y, self.width() - ROW_MARGINS[2], y)


class ScrollPanel(QtWidgets.QScrollArea):
  """A panel in the settings stack. Refreshes its rows on entry."""

  def __init__(self, body: QWidget, parent=None):
    super().__init__(parent)
    self.body = body
    self.setWidgetResizable(True)
    self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    self.setWidget(body)

  def showEvent(self, event):
    # Re-read on entry rather than trusting construction-time values: another
    # page, a daemon, or adb may have moved a param since.
    super().showEvent(event)
    fn = getattr(self.body, "refresh", None)
    if fn is not None:
      fn()
