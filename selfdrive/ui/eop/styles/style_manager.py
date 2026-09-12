"""Layered QSS loading.

Ported from Nagasware's `src/ui/styles/style_manager.py`, keeping its layering
-- a common base, then a theme, then per-view component sheets -- because that
is what lets a 1600x600 automotive skin be adjusted in one place rather than
widget by widget.

Two changes from the original. It reads sheets on demand and caches by path
rather than slurping every file at construction, since only one theme is ever
active. And `@import` directives are resolved here: Qt's QSS parser does not
support `@import` at all, so Nagasware's `dark_automotive.qss` opening with
`@import url("../common/base.qss")` silently did nothing -- the base layer was
never actually applied. Composing explicitly is what the original intended.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

from openpilot.selfdrive.ui.eop.qt import QObject, Signal

QSS_DIR = Path(__file__).parent / "qss"

_IMPORT_RE = re.compile(r'^\s*@import\s+url\(["\']?([^"\')]+)["\']?\);?\s*$', re.M)


class Theme(Enum):
  DARK = "dark_automotive"
  LIGHT = "light_automotive"


class Component(Enum):
  ONROAD = "onroad"
  OFFROAD = "offroad"


class StyleManager(QObject):
  """Composes base + theme + component sheets and applies them."""

  theme_changed = Signal(str)

  def __init__(self, theme: Theme = Theme.DARK, parent=None):
    super().__init__(parent)
    self._theme = theme
    self._cache: dict[Path, str] = {}

  @property
  def theme(self) -> Theme:
    return self._theme

  def set_theme(self, theme: Theme) -> None:
    if theme is self._theme:
      return
    self._theme = theme
    self.theme_changed.emit(theme.value)

  def _read(self, path: Path) -> str:
    if path not in self._cache:
      if not path.exists():
        self._cache[path] = ""
      else:
        text = path.read_text(encoding="utf-8")
        # Qt's QSS parser ignores @import outright, so strip the directives
        # and treat them as documentation of intent -- composition below is
        # what actually applies the layers.
        self._cache[path] = _IMPORT_RE.sub("", text)
    return self._cache[path]

  def stylesheet(self, *components: Component) -> str:
    parts = [
      self._read(QSS_DIR / "base.qss"),
      self._read(QSS_DIR / f"{self._theme.value}.qss"),
    ]
    parts += [self._read(QSS_DIR / f"{c.value}.qss") for c in components]
    return "\n".join(p for p in parts if p.strip())

  def apply(self, target, *components: Component) -> None:
    """Apply to a QApplication or any widget."""
    target.setStyleSheet(self.stylesheet(*components))
