"""Shared parked settings: horizontal scrollable tabs on both display widths."""

from openpilot.selfdrive.ui.views.offroad import OffroadView
from openpilot.selfdrive.ui.qt import Qt, QtWidgets, Signal


class SettingsWindow(OffroadView):
  closed = Signal()

  def __init__(self, store=None, parent=None):
    super().__init__(store, parent)
    self.tabs.setUsesScrollButtons(True)
    self.tabs.setElideMode(Qt.ElideNone)
    back = QtWidgets.QPushButton('Back')
    back.setMinimumHeight(48)
    back.clicked.connect(self.closed)
    self.tabs.setCornerWidget(back)

  def open_panel(self, name):
    if name == 'ExoPilot':
      name = 'EOP Device'
    for i in range(self.tabs.count()):
      if self.tabs.tabText(i) == name:
        self.tabs.setCurrentIndex(i)
        return True
    return False
