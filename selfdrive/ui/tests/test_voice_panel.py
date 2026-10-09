import os
import time
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from openpilot.selfdrive.ui.components.voice_panel import VoicePanel
from openpilot.selfdrive.ui.qt import QApplication
from openpilot.selfdrive.ui.state import UIState


class Params:
  values = {'EOPCloudVoiceEnabled': False, 'EOPVoiceEnabled': True}
  def __init__(self): self.values = dict(self.values)
  def get_bool(self, key): return self.values.get(key, False)
  def put_bool(self, key, value): self.values[key] = value


def test_hold_is_gated_release_and_hide_stop_recording():
  app = QApplication.instance() or QApplication([])
  params = Params()
  panel = VoicePanel('right', params=params)
  panel.set_recording(True)
  assert not params.get_bool('EOPVoiceRecording')
  params.put_bool('EOPCloudVoiceEnabled', True)
  panel.show()
  app.processEvents()
  panel.set_recording(True)
  assert params.get_bool('EOPVoiceRecording')
  panel.set_recording(False)
  assert not params.get_bool('EOPVoiceRecording')
  panel.set_recording(True)
  panel.hide()
  assert not params.get_bool('EOPVoiceRecording')
  panel.deleteLater()


def test_cloud_reply_error_and_freshness():
  class SM:
    valid = {'voiceCommand': True}
    recv_time = {'voiceCommand': time.monotonic()}
    value = SimpleNamespace(error='', intent='Hello driver', transcript='hello')
    def __getitem__(self, name): return self.value
  sm = SM()
  assert UIState._read_voice(sm) == 'Hello driver'
  sm.value.error = 'Online voice unavailable'
  assert UIState._read_voice(sm) == 'Online voice unavailable'
  sm.recv_time['voiceCommand'] -= 31
  assert UIState._read_voice(sm) == ''
