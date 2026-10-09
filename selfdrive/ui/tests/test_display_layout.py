"""02M preserves its wide floating panels on the shared 01M chrome baseline."""

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from openpilot.selfdrive.ui.qt import QApplication
from openpilot.selfdrive.ui.state import Snapshot
from openpilot.selfdrive.ui.views.onroad import OnroadView


def test_wide_camera_floating_panels_and_shared_chrome():
  _app = QApplication.instance() or QApplication([])
  view = OnroadView(live_camera=False)
  view.resize(1600, 600)
  view.show()
  view.set_snapshot(Snapshot())
  _app.processEvents()
  assert view.camera_size() == (1600, 600)
  assert view.model.geometry() == view.camera.geometry()
  assert view.top.height() == view.bottom.height() == 50
  assert view.panels.height() == 500
  assert view.panels.right_key == 'map'
  view.grab().save('/tmp/exo-02m-wide-tone.png')
  view.close()


def test_wide_panels_fold_away_on_narrow_viewport():
  _app = QApplication.instance() or QApplication([])
  view = OnroadView(live_camera=False)
  view.resize(1600, 600)
  view.show()
  _app.processEvents()
  assert view.panels.isVisible() and not view.hud.isVisible()
  view.resize(1024, 600)
  _app.processEvents()
  assert not view.panels.isVisible() and view.hud.isVisible()
  assert view.camera_size() == (1024, 600)
  view.resize(1600, 600)
  _app.processEvents()
  assert view.panels.isVisible() and not view.hud.isVisible()
  view.close()


def test_narrow_display_does_not_create_floating_panels():
  _app = QApplication.instance() or QApplication([])
  view = OnroadView(live_camera=False)
  view.resize(1024, 600)
  view.show()
  view.set_snapshot(Snapshot())
  _app.processEvents()
  assert view.panels is None and view.hud.isVisible()
  assert view.camera_size() == (1024, 600)
  view.close()
