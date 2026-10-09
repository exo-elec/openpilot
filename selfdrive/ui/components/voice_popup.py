"""Shared centered voice card. Animations run only while visible."""

import math
from openpilot.selfdrive.ui.qt import Qt, QtWidgets, QTimer, QtGui


class VoiceWave(QtWidgets.QWidget):
  def __init__(self, parent=None):
    super().__init__(parent)
    self.setFixedHeight(58)
    self.mode, self.level, self.phase = 'listening', 0.0, 0.0
    self.timer = QTimer(self)
    self.timer.setInterval(50)
    self.timer.timeout.connect(self.advance)

  def advance(self):
    self.phase += 0.16
    self.update()

  def showEvent(self, event):
    super().showEvent(event)
    self.timer.start()

  def hideEvent(self, event):
    self.timer.stop()
    super().hideEvent(event)

  def paintEvent(self, event):
    p = QtGui.QPainter(self)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    colors = ('#62dfff', '#8b99ff', '#d595ff')
    for layer, color in enumerate(colors):
      path = QtGui.QPainterPath()
      for x in range(0, self.width() + 1, 3):
        t = x / max(1, self.width())
        envelope = math.sin(math.pi * t) ** 2
        amplitude = 5 + 19 * self.level if self.mode == 'listening' else 13
        y = self.height() / 2 + envelope * amplitude * math.sin(t * math.pi * 6 + self.phase + layer * 1.1)
        if x == 0:
          path.moveTo(x, y)
        else:
          path.lineTo(x, y)
      p.setPen(QtGui.QPen(QtGui.QColor(color), 2.5))
      p.drawPath(path)


class VoicePopup(QtWidgets.QFrame):
  WIDTH, HEIGHT = 440, 220

  def __init__(self, parent=None):
    super().__init__(parent)
    self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    self.setStyleSheet("""
      QFrame {background: #101d30; border: 1px solid #53698a; border-radius: 24px;}
      QLabel {color: white; background: transparent; border: none; font-size: 20px;}
      QProgressBar {border: none; height: 3px; background: #253751;}
      QProgressBar::chunk {background: #62dfff;}
    """)
    layout = QtWidgets.QVBoxLayout(self)
    layout.setContentsMargins(24, 16, 24, 16)
    self.title = QtWidgets.QLabel()
    self.title.setAlignment(Qt.AlignCenter)
    self.wave = VoiceWave(self)
    self.detail = QtWidgets.QLabel()
    self.detail.setAlignment(Qt.AlignCenter)
    self.detail.setWordWrap(True)
    self.detail.setMaximumHeight(62)
    self.level = QtWidgets.QProgressBar()
    self.level.setRange(0, 100)
    self.level.setTextVisible(False)
    for w in (self.title, self.wave, self.detail, self.level):
      layout.addWidget(w)
    self.timeout = QTimer(self)
    self.timeout.setSingleShot(True)
    self.timeout.timeout.connect(self.hide)
    self.last_reply_id = 0
    self.mode = 'idle'
    self.hide()

  def center_in_parent(self):
    parent = self.parentWidget()
    if parent is None:
      return
    w, h = min(self.WIDTH, max(1, parent.width() - 40)), min(self.HEIGHT, max(1, parent.height() - 40))
    self.setGeometry((parent.width() - w) // 2, (parent.height() - h) // 2, w, h)

  def set_snapshot(self, snap):
    if getattr(snap, 'alert_text1', '') or getattr(snap, 'warnings', ()):
      self.timeout.stop()
      self.hide()
      return
    if snap.voice_reply_id and snap.voice_reply_id != self.last_reply_id:
      self.last_reply_id = snap.voice_reply_id
      self.detail.setText(snap.voice_reply[:220])
      self.timeout.start(4000)
    if snap.voice_recording:
      self.timeout.stop()
      self.mode = 'listening'
      self.title.setText('EXO · Listening')
      self.detail.setText('Keep talking. Pause when you’re done.')
      db = snap.voice_level_db
      value = int(max(0, min(100, (db + 60) * 100 / 60))) if math.isfinite(db) else 0
      self.level.setValue(value)
      self.wave.level = value / 100
      self.level.show()
    elif getattr(snap, 'voice_speaking', False):
      self.mode = 'speaking'
      self.title.setText('EXO · Speaking')
      self.level.hide()
      self.timeout.start(4000)
    elif snap.voice_processing:
      self.timeout.stop()
      self.mode = 'processing'
      self.title.setText('EXO · Thinking')
      self.detail.setText('Waiting for your online reply…')
      self.level.hide()
    elif self.timeout.isActive():
      self.mode = 'reply'
      self.title.setText('EXO')
      self.level.hide()
    else:
      self.mode = 'idle'
      self.hide()
      return
    self.wave.mode = self.mode
    self.center_in_parent()
    self.show()
