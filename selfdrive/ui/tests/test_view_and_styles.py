"""Onroad view layout and QSS composition."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from openpilot.selfdrive.ui.components.blind_spot import (
  CLEAR,
  WARNING,
  BlindSpotSeverity,
)
from openpilot.selfdrive.ui.qt import QApplication
from openpilot.selfdrive.ui.state import Snapshot
from openpilot.selfdrive.ui.styles.style_manager import (
  Component,
  StyleManager,
  Theme,
)
from openpilot.selfdrive.ui.views.onroad import PANEL_H, PANEL_W, OnroadView


@pytest.fixture(scope="module")
def app():
  return QApplication.instance() or QApplication([])


class TestStyleManager:
  def test_composes_base_and_theme(self):
    css = StyleManager(Theme.DARK).stylesheet()
    assert "QPushButton" in css        # base layer
    assert "#0d1113" in css            # dark theme layer

  def test_component_layer_is_opt_in(self):
    sm = StyleManager(Theme.DARK)
    assert "#speed" not in sm.stylesheet()
    assert "#speed" in sm.stylesheet(Component.ONROAD)

  def test_import_directives_are_stripped(self):
    # Qt's QSS parser ignores @import entirely, so leaving one in would be a
    # silently dead layer -- the bug this port inherited from Nagasware.
    assert "@import" not in StyleManager(Theme.DARK).stylesheet(Component.ONROAD)

  def test_missing_sheet_is_not_fatal(self):
    sm = StyleManager(Theme.LIGHT)   # no light_automotive.qss shipped yet
    assert "QPushButton" in sm.stylesheet()   # base still applies

  def test_theme_change_signals_once(self):
    sm = StyleManager(Theme.DARK)
    seen = []
    sm.theme_changed.connect(seen.append)
    sm.set_theme(Theme.LIGHT)
    sm.set_theme(Theme.LIGHT)          # no-op, must not re-emit
    assert seen == ["light_automotive"]

  def test_apply_sets_the_stylesheet(self, app):
    from openpilot.selfdrive.ui.qt import QWidget
    w = QWidget()
    StyleManager(Theme.DARK).apply(w, Component.ONROAD)
    assert "#speed" in w.styleSheet()


class TestOnroadView:
  def _view(self, app, w=PANEL_W, h=PANEL_H):
    v = OnroadView()
    v.resize(w, h)
    v.show()
    QApplication.processEvents()
    self._keep = v
    return v

  def test_camera_and_bands_fill_the_panel(self, app):
    v = self._view(app)
    assert v.camera.size() == v.size()
    assert v.bands.size() == v.size()

  def test_sized_for_1600x600(self, app):
    v = self._view(app)
    assert (v.width(), v.height()) == (1600, 600)

  def test_bands_follow_the_snapshot(self, app):
    v = self._view(app)
    v.set_snapshot(Snapshot(blind_spot=BlindSpotSeverity(left=WARNING)))
    assert v.bands.severity().left == WARNING
    assert v.bands._left.is_enabled()
    v.set_snapshot(Snapshot(blind_spot=BlindSpotSeverity(CLEAR, CLEAR)))
    assert not v.bands._left.is_enabled()

  def test_bands_sit_above_the_camera(self, app):
    v = self._view(app)
    kids = v.children()
    assert kids.index(v.bands) > kids.index(v.camera)

  def test_relayout_on_resize(self, app):
    v = self._view(app, 1024, 600)
    v.resize(PANEL_W, PANEL_H)
    QApplication.processEvents()
    assert v.camera.size() == v.size() == v.bands.size()
