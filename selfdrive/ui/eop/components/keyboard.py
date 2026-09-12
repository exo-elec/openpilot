"""On-screen keyboard.

From Nagasware, which is the only source that has one -- VisionPilot's
settings assume a keyboard exists without shipping it. Replaces openpilot's
qt/widgets/keyboard.cc.

Sized for a 1600x600 panel used at arm's length: keys are 64px tall, which is
above the 44px minimum touch target and leaves room for the four rows plus a
preview line inside 500px of content height.
"""

from __future__ import annotations

from openpilot.selfdrive.ui.eop.qt import (
  Qt,
  QtWidgets,
  QWidget,
  Signal,
)

ROWS_LOWER = [
  list("qwertyuiop"),
  list("asdfghjkl"),
  ["⇧", *list("zxcvbnm"), "⌫"],
  ["123", "space", "done"],
]
ROWS_UPPER = [
  list("QWERTYUIOP"),
  list("ASDFGHJKL"),
  ["⇧", *list("ZXCVBNM"), "⌫"],
  ["123", "space", "done"],
]
ROWS_NUM = [
  list("1234567890"),
  list("-/:;()$&@\""),
  [".", ",", "?", "!", "'", "+", "=", "_", "⌫"],
  ["ABC", "space", "done"],
]

KEY_H = 64


class OnScreenKeyboard(QWidget):
  """Emits `submitted` on done, `cancelled` on escape."""

  submitted = Signal(str)
  cancelled = Signal()
  text_changed = Signal(str)

  def __init__(self, prompt: str = "", initial: str = "", password: bool = False,
               parent: QWidget | None = None):
    super().__init__(parent)
    self.setObjectName("keyboard")
    self._text = initial
    self._shift = False
    self._numeric = False
    self._password = password

    self._lay = QtWidgets.QVBoxLayout(self)
    self._lay.setContentsMargins(24, 16, 24, 16)
    self._lay.setSpacing(10)

    self.prompt = QtWidgets.QLabel(prompt)
    self.prompt.setObjectName("kbPrompt")
    self._lay.addWidget(self.prompt)

    self.preview = QtWidgets.QLabel()
    self.preview.setObjectName("kbPreview")
    self._lay.addWidget(self.preview)

    self._rows_host = QtWidgets.QVBoxLayout()
    self._rows_host.setSpacing(8)
    self._lay.addLayout(self._rows_host, 1)

    self._render_rows()
    self._refresh_preview()

  # ---- text -------------------------------------------------------------

  def text(self) -> str:
    return self._text

  def _refresh_preview(self) -> None:
    shown = "•" * len(self._text) if self._password else self._text
    self.preview.setText(shown or " ")
    self.text_changed.emit(self._text)

  # ---- layout -----------------------------------------------------------

  def _clear_rows(self) -> None:
    while self._rows_host.count():
      item = self._rows_host.takeAt(0)
      sub = item.layout()
      if sub is None:
        continue
      while sub.count():
        w = sub.takeAt(0).widget()
        if w is not None:
          w.setParent(None)
          w.deleteLater()

  def _render_rows(self) -> None:
    self._clear_rows()
    rows = ROWS_NUM if self._numeric else (ROWS_UPPER if self._shift else ROWS_LOWER)
    for row in rows:
      line = QtWidgets.QHBoxLayout()
      line.setSpacing(8)
      for key in row:
        b = QtWidgets.QPushButton("" if key == "space" else key)
        b.setMinimumHeight(KEY_H)
        b.setObjectName("kbKey" if len(key) == 1 else "kbKeyWide")
        if key == "space":
          b.setMinimumWidth(360)
        b.clicked.connect(lambda _c=False, k=key: self._on_key(k))
        line.addWidget(b)
      self._rows_host.addLayout(line)

  # ---- keys -------------------------------------------------------------

  def _on_key(self, key: str) -> None:
    if key == "⌫":
      self._text = self._text[:-1]
    elif key == "space":
      self._text += " "
    elif key == "⇧":
      self._shift = not self._shift
      self._render_rows()
      return
    elif key in ("123", "ABC"):
      self._numeric = not self._numeric
      self._render_rows()
      return
    elif key == "done":
      self.submitted.emit(self._text)
      return
    else:
      self._text += key
      if self._shift:
        # One-shot shift, like every phone keyboard. Sticky caps after a
        # single letter is the classic on-screen keyboard annoyance.
        self._shift = False
        self._render_rows()
    self._refresh_preview()

  def keyPressEvent(self, event):
    """Hardware keyboard works too -- dev machines have one, and refusing it
    makes the settings UI painful to work on."""
    if event.key() == Qt.Key_Escape:
      self.cancelled.emit()
    elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
      self.submitted.emit(self._text)
    elif event.key() == Qt.Key_Backspace:
      self._text = self._text[:-1]
      self._refresh_preview()
    elif event.text():
      self._text += event.text()
      self._refresh_preview()
