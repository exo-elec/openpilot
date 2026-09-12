"""Confirmation, alert and selection dialogs.

Port of widgets/input.cc. Full-screen and dark rather than native message
boxes: this is a touch panel in a car, where a 300px dialog with 30px buttons
is not something a driver can reliably hit.
"""

from __future__ import annotations

from openpilot.selfdrive.ui.eop.qt import Qt, QtWidgets, QWidget

DIALOG_STYLE = """
QDialog { background-color: black; }
QLabel#dialogText {
  color: white;
  font-size: 42px;
  font-weight: 500;
}
QPushButton {
  color: white;
  background-color: #444444;
  border-radius: 10px;
  font-size: 34px;
  font-weight: 500;
  padding: 25px 40px;
}
QPushButton:pressed { background-color: #333333; }
QPushButton#confirm { background-color: #465BEA; }
QPushButton#confirm:pressed { background-color: #3049F4; }
QListWidget {
  background-color: #303030;
  border-radius: 10px;
  color: white;
  font-size: 34px;
}
QListWidget::item { padding: 20px; }
QListWidget::item:selected { background-color: #465BEA; }
"""


class _Dialog(QtWidgets.QDialog):
  def __init__(self, parent: QWidget | None = None):
    super().__init__(parent)
    self.setStyleSheet(DIALOG_STYLE)
    self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
    self.setModal(True)

  def showEvent(self, event):
    super().showEvent(event)
    # Cover the whole window rather than floating: on a 1024x600 panel a
    # centred dialog leaves live UI visible and tappable around its edges.
    parent = self.parentWidget()
    if parent is not None:
      self.setGeometry(parent.window().rect())


def _exec(dialog: QtWidgets.QDialog) -> int:
  """Qt5 bindings spell it exec_(); PySide6 exec()."""
  return dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()


def confirm_dialog(text: str, confirm_text: str = "Ok",
                   parent: QWidget | None = None) -> bool:
  """Ask, and return whether the user said yes. Cancel is the default."""
  d = _Dialog(parent)
  lay = QtWidgets.QVBoxLayout(d)
  lay.setContentsMargins(50, 50, 50, 50)
  lay.setSpacing(30)

  label = QtWidgets.QLabel(text)
  label.setObjectName("dialogText")
  label.setWordWrap(True)
  label.setAlignment(Qt.AlignCenter)
  lay.addWidget(label, 1)

  buttons = QtWidgets.QHBoxLayout()
  buttons.setSpacing(30)
  cancel = QtWidgets.QPushButton("Cancel")
  cancel.clicked.connect(d.reject)
  ok = QtWidgets.QPushButton(confirm_text)
  ok.setObjectName("confirm")
  ok.clicked.connect(d.accept)
  buttons.addWidget(cancel)
  buttons.addWidget(ok)
  lay.addLayout(buttons)

  return _exec(d) == QtWidgets.QDialog.Accepted


def alert_dialog(text: str, parent: QWidget | None = None) -> None:
  """Say something that needs acknowledging but offers no choice."""
  d = _Dialog(parent)
  lay = QtWidgets.QVBoxLayout(d)
  lay.setContentsMargins(50, 50, 50, 50)
  lay.setSpacing(30)

  label = QtWidgets.QLabel(text)
  label.setObjectName("dialogText")
  label.setWordWrap(True)
  label.setAlignment(Qt.AlignCenter)
  lay.addWidget(label, 1)

  ok = QtWidgets.QPushButton("Ok")
  ok.setObjectName("confirm")
  ok.clicked.connect(d.accept)
  lay.addWidget(ok)
  _exec(d)


def selection_dialog(title: str, options: list[str], current: str = "",
                     parent: QWidget | None = None) -> str:
  """Pick one of `options`, or "" if cancelled."""
  d = _Dialog(parent)
  lay = QtWidgets.QVBoxLayout(d)
  lay.setContentsMargins(50, 50, 50, 50)
  lay.setSpacing(20)

  label = QtWidgets.QLabel(title)
  label.setObjectName("dialogText")
  lay.addWidget(label)

  listing = QtWidgets.QListWidget()
  listing.addItems(options)
  if current in options:
    listing.setCurrentRow(options.index(current))
  # Double-tap to choose, so a selection is one gesture rather than
  # select-then-reach-for-a-button on a moving screen.
  listing.itemDoubleClicked.connect(lambda _item: d.accept())
  lay.addWidget(listing, 1)

  buttons = QtWidgets.QHBoxLayout()
  buttons.setSpacing(30)
  cancel = QtWidgets.QPushButton("Cancel")
  cancel.clicked.connect(d.reject)
  ok = QtWidgets.QPushButton("Select")
  ok.setObjectName("confirm")
  ok.clicked.connect(d.accept)
  buttons.addWidget(cancel)
  buttons.addWidget(ok)
  lay.addLayout(buttons)

  if _exec(d) != QtWidgets.QDialog.Accepted:
    return ""
  item = listing.currentItem()
  return item.text() if item is not None else ""


def text_input_dialog(prompt: str, initial: str = "", secret: bool = False,
                      parent: QWidget | None = None) -> str | None:
  """Ask for a line of text, returning None if cancelled.

  Wraps components/keyboard.py's OnScreenKeyboard, which already owns the
  prompt, the masked preview and the text itself -- there is no hardware
  keyboard on this device and no compositor-provided virtual one under EGLFS,
  so this is the only way text gets entered.
  """
  from openpilot.selfdrive.ui.eop.components.keyboard import OnScreenKeyboard

  d = _Dialog(parent)
  lay = QtWidgets.QVBoxLayout(d)
  lay.setContentsMargins(0, 0, 0, 0)

  keyboard = OnScreenKeyboard(prompt=prompt, initial=initial, password=secret)
  keyboard.submitted.connect(lambda _text: d.accept())
  keyboard.cancelled.connect(d.reject)
  lay.addWidget(keyboard)

  if _exec(d) != QtWidgets.QDialog.Accepted:
    return None
  return keyboard.text()
