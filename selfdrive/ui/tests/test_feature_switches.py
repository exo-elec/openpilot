import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from openpilot.selfdrive.ui.qt import QApplication
from openpilot.selfdrive.ui.components.controls import ParamStore
from openpilot.selfdrive.ui.main import _DemoParams
from openpilot.selfdrive.ui.views.settings import SettingsWindow
from openpilot.selfdrive.ui.settings.descriptor import all_controls, Kind


def test_feature_switches_write_both_states_and_lock_when_driving():
  _app = QApplication.instance() or QApplication([])
  params = _DemoParams()
  view = SettingsWindow(ParamStore(params))
  page = view.pages['features']
  for row in page.rows:
    assert row.control.kind is Kind.TOGGLE
    row.widget.setChecked(True)
    assert params.get_bool(row.control.key)
    row.widget.setChecked(False)
    assert not params.get_bool(row.control.key)
  view.set_driving_state(False, False)
  assert all(not row.isEnabled() for row in page.rows)
  view.set_driving_state(False, True)
  assert all(row.isEnabled() for row in page.rows)
  for row in page.rows:
    assert row.control.feature_id
  view.deleteLater()


def test_core_eop10_custom_feature_switches_are_exposed():
  switches = {c.key for c in all_controls() if c.kind is Kind.TOGGLE}
  assert {
    'EOPLatALCC',
    'EOPTLSCEnabled',
    'EOPVTSCEnabled',
    'EOPMTSCEnabled',
    'EOPMSLCEnabled',
    'EOPNSLCEnabled',
    'EOPSQSCEnabled',
    'EOPCATEnabled',
    'EOPTJAEnabled',
    'EOPNeuralNetworkLateralControl',
  } <= switches
