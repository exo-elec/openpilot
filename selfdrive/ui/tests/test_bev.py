import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from openpilot.selfdrive.ui.qt import QApplication
from openpilot.selfdrive.ui.state import ModelFrame, Lead, Snapshot
from openpilot.selfdrive.ui.components.bev import BevOverlay
from openpilot.selfdrive.ui.views.onroad import OnroadView


def test_bev_draws_live_lead_and_clears_stale_geometry():
  _app = QApplication.instance() or QApplication([])
  view = BevOverlay()
  view.resize(1024, 500)
  view.show()
  points = np.array([[0.0, 30.0], [0.0, 0.0], [0.0, 0.0]])
  frame = ModelFrame(valid=True, position=points, lane_lines=(points,), lane_line_probs=(1.0,), leads=(Lead(d_rel=20.0),))
  view.set_frame(frame, Snapshot(bev_enabled=True))
  _app.processEvents()
  live = view.grab().toImage().pixelColor(512, 310)
  assert live.red() == 255 and live.green() == 194
  view.set_frame(ModelFrame(), Snapshot(bev_enabled=True))
  _app.processEvents()
  cleared = view.grab().toImage().pixelColor(512, 310)
  assert cleared != live
  view.close()


def test_bev_mode_does_not_create_narrow_side_widgets():
  _app = QApplication.instance() or QApplication([])
  view = OnroadView(live_camera=False)
  view.resize(1024, 600)
  view.show()
  view.set_snapshot(Snapshot(bev_enabled=True))
  assert view.bev.isVisible() and not view.model.isVisible()
  assert not view.hud.isVisible() and view.panels is None
  view.set_snapshot(Snapshot(bev_enabled=False))
  assert not view.bev.isVisible() and view.hud.isVisible()
  view.close()
