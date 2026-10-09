"""EOP10 bird's-eye geometry, drawn by the shared PyQt5 UI."""

import math
from openpilot.selfdrive.ui.components.base import BaseOverlay
from openpilot.selfdrive.ui.qt import QColor, QPainter, QPainterPath, QPen, QRectF, Qt


class BevOverlay(BaseOverlay):
  def __init__(self, parent=None):
    super().__init__('bevOverlay', parent)
    self.frame = None
    self.snap = None
    self.hide()

  def set_frame(self, frame, snap):
    self.frame, self.snap = frame, snap
    self.update()

  def paintEvent(self, event):
    p = QPainter(self)
    p.setRenderHint(QPainter.Antialiasing)
    p.fillRect(self.rect(), QColor(13, 17, 19))
    scale = min(self.width() / 24, max(1, self.height() - 50) / 60)
    cx, cy = self.width() / 2, self.height() - 35
    p.setPen(QPen(QColor(124, 139, 141, 70), 1))
    for distance in range(10, 60, 10):
      y = cy - distance * scale
      p.drawLine(0, int(y), self.width(), int(y))
      p.drawText(12, int(y) - 4, f'{distance} m')
    frame = self.frame
    if frame is not None and frame.valid:
      for lines, confidences, color, width in (
        (frame.road_edges, frame.road_edge_stds, QColor(255, 100, 90), 2),
        (frame.lane_lines, frame.lane_line_probs, QColor(230, 235, 234), 2),
        ((frame.position,) if frame.position is not None else (), (1.0,), QColor(98, 223, 255), 4),
      ):
        for i, points in enumerate(lines):
          if points is None:
            continue
          confidence = confidences[i] if i < len(confidences) else 0.0
          if lines is frame.road_edges:
            confidence = 1 - confidence
          confidence = max(0.0, min(1.0, confidence))
          if confidence <= 0.05:
            continue
          line_color = QColor(color)
          line_color.setAlpha(int(255 * confidence))
          p.setPen(QPen(line_color, width))
          path = QPainterPath()
          connected = False
          for forward, lateral in zip(points[0][:128], points[1][:128], strict=True):
            if not (math.isfinite(forward) and math.isfinite(lateral) and 0 <= forward <= 60):
              connected = False
              continue
            x, y = cx + lateral * scale, cy - forward * scale
            if connected:
              path.lineTo(x, y)
            else:
              path.moveTo(x, y)
            connected = True
          p.drawPath(path)
      p.setPen(Qt.NoPen)
      p.setBrush(QColor(255, 194, 0))
      for lead in frame.leads[:2]:
        if not (math.isfinite(lead.d_rel) and math.isfinite(lead.y_rel) and 0 < lead.d_rel <= 60):
          continue
        p.drawRoundedRect(QRectF(cx - lead.y_rel * scale - 8, cy - lead.d_rel * scale - 12, 16, 24), 4, 4)
    else:
      p.setPen(QColor(179, 192, 192))
      p.drawText(self.rect(), Qt.AlignCenter, 'Waiting for live model and radar')
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(98, 223, 255))
    p.drawRoundedRect(QRectF(cx - 10, cy - 18, 20, 36), 5, 5)
    if self.snap is not None:
      for side, severity in ((-1, self.snap.blind_spot.left), (1, self.snap.blind_spot.right)):
        if severity:
          p.setBrush(QColor(255, 90, 80) if severity >= 2 else QColor(255, 194, 0))
          p.drawEllipse(QRectF(cx + side * 38 - 6, cy - 6, 12, 12))
