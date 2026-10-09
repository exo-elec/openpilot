"""Explicit hold-to-speak input for online voice; no local wake word/model."""

from openpilot.selfdrive.ui.components.panels import SidePanel, panel
from openpilot.selfdrive.ui.qt import Qt, QColor, QRectF, QtWidgets


@panel('voice')
class VoicePanel(SidePanel):
  title = 'online voice'
  compact = True

  def __init__(self, panel, parent=None, params=None):
    super().__init__(panel, parent)
    self.params = params
    self.recording = False
    self.button = QtWidgets.QPushButton('Hold to speak', self)
    self.button.setStyleSheet('font-size: 20px; padding: 12px; color: white; background: #245e83;')
    self.button.pressed.connect(lambda: self.set_recording(True))
    self.button.released.connect(lambda: self.set_recording(False))

  def set_recording(self, active):
    if self.params is None:
      from openpilot.common.params import Params

      self.params = Params()
    allowed = self.params.get_bool('EOPCloudVoiceEnabled') and self.params.get_bool('EOPVoiceEnabled')
    self.recording = bool(active and allowed)
    self.params.put_bool('EOPVoiceRecording', self.recording)
    self.button.setText('Listening… release to send' if self.recording else 'Hold to speak')
    self.update()

  def hideEvent(self, event):
    if self.recording:
      self.set_recording(False)
    super().hideEvent(event)

  def resizeEvent(self, event):
    super().resizeEvent(event)
    self.button.setGeometry(12, self.height() - 70, max(1, self.width() - 24), 54)

  def paint_body(self, painter):
    painter.setPen(QColor('#f0f4f4'))
    font = painter.font()
    font.setPointSize(13)
    painter.setFont(font)
    text = (
      self.data.values.get('voice_text', '')
      or 'Enable Online Voice in settings.\nHold to speak, then release to send.\nMaximum recording: 15 seconds.\nRequires internet.'
    )
    painter.drawText(QRectF(16, 54, max(1, self.width() - 32), max(1, self.height() - 134)), Qt.TextWordWrap | Qt.AlignTop, text)
