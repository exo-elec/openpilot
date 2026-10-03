"""Component factory with instance caching and explicit teardown.

Ported from Nagasware's onroad/offroad component factories. The teardown half
is the point: the gesture system creates and destroys panels constantly, and
a factory that only ever builds is a leak with a nice API.
"""

from __future__ import annotations

from collections.abc import Callable

from openpilot.selfdrive.ui.qt import QWidget


class ComponentFactory:
  def __init__(self):
    self._types: dict[str, Callable[..., QWidget]] = {}
    self._cache: dict[tuple[str, str], QWidget] = {}

  def register(self, kind: str, ctor: Callable[..., QWidget]) -> None:
    self._types[kind] = ctor

  def available(self) -> list[str]:
    return sorted(self._types)

  def create(self, kind: str, name: str = "", parent: QWidget | None = None,
             cache: bool = True, **kw) -> QWidget:
    if kind not in self._types:
      raise KeyError(f"unknown component {kind!r}; have {self.available()}")
    key = (kind, name or kind)
    if cache and key in self._cache:
      return self._cache[key]
    widget = self._types[kind](parent=parent, **kw)
    if cache:
      self._cache[key] = widget
    return widget

  def release(self, kind: str, name: str = "") -> None:
    widget = self._cache.pop((kind, name or kind), None)
    if widget is not None:
      widget.setParent(None)
      widget.deleteLater()

  def release_all(self) -> None:
    for widget in self._cache.values():
      widget.setParent(None)
      widget.deleteLater()
    self._cache.clear()


_factory: ComponentFactory | None = None


def factory() -> ComponentFactory:
  global _factory
  if _factory is None:
    _factory = ComponentFactory()
  return _factory
