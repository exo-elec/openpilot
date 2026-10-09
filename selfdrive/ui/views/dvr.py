"""DVR playback.

From Nagasware's `dvr_video_player.py`, which is the only source with one.
Retargeted: nagasware drives its own recorder thread, while here `recordd`
already owns recording and segment rotation, so this is a browser and player
over what recordd wrote -- not a second recorder.

Segments are read off disk rather than through a message: a UI that asks a
daemon to list files is a round trip for information the filesystem already
has, and it must keep working when recordd is not running, which is exactly
when someone wants to review footage.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openpilot.selfdrive.ui.qt import Qt, QtWidgets, QWidget, Signal

# Where recordd writes. Overridable for a dev machine.
SEGMENT_ROOT = Path(os.environ.get("EOP_DVR_ROOT", "/data/media/0/realdata"))
VIDEO_SUFFIXES = (".hevc", ".mp4", ".ts")


@dataclass(frozen=True)
class Segment:
  path: Path
  started: datetime
  size_mb: float
  camera: str

  @property
  def label(self) -> str:
    return f"{self.started:%Y-%m-%d  %H:%M:%S}   {self.camera}   {self.size_mb:.0f} MB"


def scan_segments(root: Path = SEGMENT_ROOT, limit: int = 200) -> list[Segment]:
  """Newest first, bounded.

  recordd lays segments out as dated directories, so this walks the newest
  directories first and stops once it has enough -- rather than rglob'ing the
  whole tree and sorting at the end. On a device with months of footage that
  difference is the whole stall: the old form stat'ed every file on disk
  before returning 200 of them, on the UI thread.

  A missing or unreadable root is an empty list, not an error. A device that
  has not recorded anything yet is not a failure.
  """
  if not root.exists():
    return []

  try:
    dirs = sorted(
      (d for d in root.iterdir() if d.is_dir()),
      key=lambda d: d.stat().st_mtime,
      reverse=True,
    )
  except OSError:
    return []

  # Include the root itself, for flat layouts and dev machines.
  found: list[Segment] = []
  for directory in [root, *dirs]:
    if len(found) >= limit:
      break
    try:
      entries = sorted(directory.iterdir(), key=lambda p: p.name)
    except OSError:
      continue
    for path in entries:
      if len(found) >= limit:
        break
      if path.suffix.lower() not in VIDEO_SUFFIXES or not path.is_file():
        continue
      try:
        stat = path.stat()
      except OSError:
        continue
      found.append(Segment(
        path=path,
        started=datetime.fromtimestamp(stat.st_mtime),
        size_mb=stat.st_size / (1024 * 1024),
        camera=path.stem.replace("_", " "),
      ))

  found.sort(key=lambda s: s.started, reverse=True)
  return found[:limit]


class PlaybackSurface(QWidget):
  """Shows one decoded frame, seeked by position.

  Holds the numpy array alive alongside the QImage: QImage wraps the buffer
  without copying, so letting the array fall out of scope is a use-after-free
  that shows up as torn frames rather than a crash.
  """

  def __init__(self, parent=None):
    super().__init__(parent)
    self.setObjectName("dvrSurface")
    self.setAttribute(Qt.WA_OpaquePaintEvent, True)
    self._segment: Segment | None = None
    self._decoder = None
    self._rgb = None
    self._image = None
    self._error = ""

  def set_segment(self, segment: Segment | None) -> None:
    self._close()
    self._segment = segment
    self._error = ""
    if segment is None:
      self.update()
      return
    try:
      from openpilot.selfdrive.ui.components.decoder import open_decoder
      self._decoder = open_decoder(segment.path)
      self.seek(0.0)
    except Exception as e:
      self._error = str(e)
      self.update()

  def seek(self, seconds: float) -> None:
    if self._decoder is None:
      return
    from openpilot.selfdrive.ui.components.decoder import to_qimage
    rgb = self._decoder.frame_at(seconds)
    if rgb is None:
      self._error = "no frame at this position"
      self._image = None
    else:
      import numpy as np
      self._rgb = np.ascontiguousarray(rgb)
      self._image = to_qimage(self._rgb)
      self._error = ""
    self.update()

  def _close(self) -> None:
    if self._decoder is not None:
      try:
        self._decoder.close()
      except Exception:
        pass
    self._decoder = None
    self._image = None
    self._rgb = None

  def hideEvent(self, event):
    super().hideEvent(event)
    self._close()

  def paintEvent(self, event):
    from openpilot.selfdrive.ui.qt import QColor, QPainter
    p = QPainter(self)
    p.fillRect(self.rect(), QColor(0, 0, 0))
    if self._image is not None:
      scaled = self._image.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
      p.drawImage((self.width() - scaled.width()) // 2,
                  (self.height() - scaled.height()) // 2, scaled)
      return
    p.setPen(QColor(60, 72, 74))
    if self._segment is None:
      text = "no segment selected"
    elif self._error:
      text = f"{self._segment.path.name}\n{self._error}"
    else:
      text = self._segment.path.name
    p.drawText(self.rect(), Qt.AlignCenter, text)


class DvrView(QWidget):
  """Segment list beside a playback surface."""

  segment_selected = Signal(object)

  def __init__(self, root: Path = SEGMENT_ROOT, parent=None):
    super().__init__(parent)
    self.setObjectName("dvrRoot")
    self._root = root
    self.segments: list[Segment] = []

    lay = QtWidgets.QHBoxLayout(self)
    lay.setContentsMargins(16, 16, 16, 16)
    lay.setSpacing(16)

    left = QtWidgets.QVBoxLayout()
    self.list = QtWidgets.QListWidget()
    self.list.setObjectName("dvrList")
    self.list.currentRowChanged.connect(self._on_row)
    left.addWidget(self.list, 1)

    refresh = QtWidgets.QPushButton("Refresh")
    refresh.clicked.connect(self.refresh)
    self.status = QtWidgets.QLabel("")
    self.status.setObjectName("dvrStatus")
    left.addWidget(self.status)
    left.addWidget(refresh)
    lay.addLayout(left, 0)

    self.surface = PlaybackSurface(self)
    lay.addWidget(self.surface, 1)

    self.refresh()

  def refresh(self) -> None:
    self.segments = scan_segments(self._root)
    self.list.clear()
    for seg in self.segments:
      self.list.addItem(seg.label)
    total = sum(s.size_mb for s in self.segments)
    self.status.setText(
      f"{len(self.segments)} segments · {total / 1024:.1f} GB"
      if self.segments else f"no recordings under {self._root}")

  def _on_row(self, row: int) -> None:
    seg = self.segments[row] if 0 <= row < len(self.segments) else None
    self.surface.set_segment(seg)
    self.segment_selected.emit(seg)
