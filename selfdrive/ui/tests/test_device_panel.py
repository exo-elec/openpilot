"""DevicePanel's side/rear camera calibration section."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from openpilot.selfdrive.ui.components.controls import ParamStore
from openpilot.selfdrive.ui.qt import QApplication
from openpilot.selfdrive.ui.views.panels.device import (
  SIDE_REAR_CAMERAS,
  DevicePanel,
  side_rear_calibration_description,
)

HAL_SRC = os.path.join(os.path.dirname(__file__), '../../../../exopilot/hal')


@pytest.fixture
def hal(monkeypatch):
  if os.path.isdir(HAL_SRC):
    monkeypatch.syspath_prepend(os.path.abspath(HAL_SRC))
  pytest.importorskip("hal.calibration.store")


class FakeParams:
  def __init__(self, **d):
    self.d = dict(d)

  def get_bool(self, key):
    return bool(self.d.get(key, False))

  def put_bool(self, key, value):
    self.d[key] = value

  def get(self, key, encoding=None):
    return self.d.get(key)

  def get_text(self, key):
    v = self.d.get(key)
    return v if isinstance(v, str) else ""

  def put(self, key, value):
    self.d[key] = value

  def put_text(self, key, value):
    self.d[key] = value

  def remove(self, key):
    self.d.pop(key, None)


@pytest.fixture(scope="module")
def app():
  return QApplication.instance() or QApplication([])


def test_description_reports_not_available_without_hal(monkeypatch):
  import openpilot.selfdrive.ui.views.panels.device as device_mod
  monkeypatch.setattr(device_mod, "_hal_calibration_store", lambda: None)
  desc = side_rear_calibration_description()
  assert "not available" in desc


def test_description_reports_each_camera(hal, tmp_path, monkeypatch):
  from hal.calibration import store, tf_tree
  import openpilot.selfdrive.ui.views.panels.device as device_mod

  path = str(tmp_path / "sensors_tf.yaml")
  store.save_link(tf_tree.link_frame('rear_camera'), tf_tree.Transform(-2.6, 0.0, 1.4, 0.0, 0.1, 3.14), path)

  class _StoreAtPath:
    """store, but load_links() defaults to the test's tmp path instead of
    the real device path."""
    def load_links(self, p=None):
      return store.load_links(p or path)

  monkeypatch.setattr(device_mod, "_hal_calibration_store", lambda: (_StoreAtPath(), tf_tree))

  desc = side_rear_calibration_description()
  assert "Rear Camera: calibrated" in desc
  assert "Side Left: not yet calibrated" in desc
  assert "Side Right: not yet calibrated" in desc


def test_all_cameras_covered_by_default():
  assert set(SIDE_REAR_CAMERAS) == {'side_left', 'side_right', 'rear_camera'}


class TestDevicePanelSideRearRow:
  def test_row_exists_and_starts_unpressed(self, app):
    panel = DevicePanel(ParamStore(FakeParams()))
    assert panel.side_rear_calib.title_label.text() == "Side/Rear Camera Calibration"

  def test_reset_writes_the_trigger_param_when_confirmed(self, app, monkeypatch):
    import openpilot.selfdrive.ui.views.panels.device as device_mod
    monkeypatch.setattr(device_mod, "confirm_dialog", lambda *a, **k: True)
    params = FakeParams()
    panel = DevicePanel(ParamStore(params))
    panel._reset_side_rear_calibration()
    assert params.d.get("EOPSideRearCalibReset") is True

  def test_reset_refused_while_engaged(self, app, monkeypatch):
    import openpilot.selfdrive.ui.views.panels.device as device_mod
    monkeypatch.setattr(device_mod, "confirm_dialog", lambda *a, **k: True)
    monkeypatch.setattr(device_mod, "alert_dialog", lambda *a, **k: None)
    params = FakeParams()
    panel = DevicePanel(ParamStore(params))
    panel.set_driving_state(engaged=True, offroad=False)
    panel._reset_side_rear_calibration()
    assert "EOPSideRearCalibReset" not in params.d

  def test_side_rear_calib_stays_enabled_onroad(self, app):
    """Same policy as the road-camera reset: guarded by engagement, not
    disabled while driving."""
    panel = DevicePanel(ParamStore(FakeParams()))
    panel.set_driving_state(engaged=False, offroad=False)
    assert panel.side_rear_calib.button.isEnabled()
