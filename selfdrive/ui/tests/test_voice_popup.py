import os
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from openpilot.selfdrive.ui.components.voice_popup import VoicePopup
from openpilot.selfdrive.ui.qt import QApplication


def test_recording_processing_reply_and_dismissal():
  _app = QApplication.instance() or QApplication([])
  popup = VoicePopup()
  snap = SimpleNamespace(voice_recording=True, voice_processing=False, voice_level_db=-30, voice_reply='', voice_reply_id=0)
  popup.set_snapshot(snap)
  assert popup.isVisible() and popup.level.value() == 50
  snap.voice_recording = False
  snap.voice_processing = True
  popup.set_snapshot(snap)
  assert popup.title.text() == 'EXO · Thinking'
  snap.voice_processing = False
  popup.set_snapshot(snap)
  assert not popup.isVisible()
  snap.voice_reply = 'Hello'
  snap.voice_reply_id = 1
  popup.set_snapshot(snap)
  assert popup.isVisible() and popup.timeout.isActive()
  popup.deleteLater()


def test_centered_shared_size_and_playback_alert_priority():
  _app = QApplication.instance() or QApplication([])
  for width in (1024, 1920):
    parent = __import__('openpilot.selfdrive.ui.qt', fromlist=['QtWidgets']).QtWidgets.QWidget()
    parent.resize(width, 600)
    parent.show()
    popup = VoicePopup(parent)
    snap = SimpleNamespace(voice_recording=False, voice_processing=False, voice_speaking=True, voice_level_db=-80, voice_reply='Hello', voice_reply_id=1)
    popup.set_snapshot(snap)
    assert popup.width() == 440 and popup.height() == 220
    assert popup.x() == (width - 440) // 2 and popup.y() == 190
    assert popup.mode == 'speaking' and popup.wave.timer.isActive()
    snap.alert_text1 = 'Brake!'
    popup.set_snapshot(snap)
    assert not popup.isVisible() and not popup.wave.timer.isActive()
    parent.close()
