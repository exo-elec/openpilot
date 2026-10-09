import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from openpilot.selfdrive.ui.qt import QApplication
from openpilot.selfdrive.ui.components.controls import ParamStore
from openpilot.selfdrive.ui.main import _DemoParams
from openpilot.selfdrive.ui.state import Snapshot
from openpilot.selfdrive.ui.views.window import MainWindow, PAGE_SETTINGS, PAGE_HOME


def test_shared_offroad_navigation_voice_and_drive_transition():
  _app = QApplication.instance() or QApplication([])
  from openpilot.selfdrive.ui.styles.style_manager import StyleManager, Theme, Component

  StyleManager(Theme.DARK).apply(_app, Component.ONROAD, Component.OFFROAD)
  for width in (1024, 1600):
    window = MainWindow(ParamStore(_DemoParams()), live_camera=False)
    window.resize(width, 600)
    window.stack.setCurrentIndex(PAGE_HOME)
    window.show()
    window.open_settings('Voice')
    _app.processEvents()
    assert window.stack.currentIndex() == PAGE_SETTINGS
    assert window.settings.tabs.tabText(window.settings.tabs.currentIndex()) == 'Voice'
    assert window.settings.tabs.usesScrollButtons()
    assert not window.home.sidebar.isVisible()
    snap = Snapshot(voice_recording=True, voice_level_db=-30)
    window.set_snapshot(snap)
    assert window.voice_popup.isVisible()
    assert window.voice_popup.x() == (width - 440) // 2
    window.grab().save(f'/tmp/exo-shared-offroad-{width}.png')
    window.set_started(True)
    assert window.stack.currentIndex() == PAGE_HOME
    assert window.home.stack.currentWidget() is window.home.onroad
    window.close()
