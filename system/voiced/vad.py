"""Voice activity detection (ported from VisionPilot src/voice/vad).

VisionPilot required the `webrtcvad` package and raised without it. Here the
detector is a plain energy VAD against an adaptive noise floor, with
VisionPilot's hysteresis (3 speech frames to start, 5 silence frames to stop)
and hangover (10 frames), so it runs with numpy only. Local, no network.
"""
from __future__ import annotations

from collections import deque

import numpy as np

FRAME_MS = 30
SILENCE_DB = -96.0
INITIAL_FLOOR_DB = -60.0   # quiet cabin; a louder start is learned (see _frame)


def level_db(frame: np.ndarray) -> float:
  """RMS level of a float frame in dBFS."""
  if frame.size == 0:
    return SILENCE_DB
  rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
  return 20.0 * np.log10(rms) if rms > 0 else SILENCE_DB


class VAD:
  def __init__(self, sample_rate: int = 16000, margin_db: float = 10.0,
               min_speech_db: float = -50.0, start_frames: int = 3,
               stop_frames: int = 5, hangover_frames: int = 10):
    self.frame_len = sample_rate * FRAME_MS // 1000
    self.margin_db = margin_db            # speech must be this far above the floor
    self.min_speech_db = min_speech_db    # and above this absolute level
    self.start_frames = start_frames
    self.stop_frames = stop_frames
    self.hangover_frames = hangover_frames
    self.noise_db: float | None = None   # set on the first frame
    self.active = False
    self.level_db = SILENCE_DB
    self._speech = 0
    self._silence = 0
    self._hangover = 0
    self._recent: deque[bool] = deque(maxlen=5)
    self._pending = np.zeros(0, dtype=np.float32)

  @property
  def confidence(self) -> float:
    return sum(self._recent) / len(self._recent) if self._recent else 0.0

  def _frame(self, frame: np.ndarray) -> None:
    db = level_db(frame)
    self.level_db = db
    if self.noise_db is None:
      # Never start above a quiet floor: starting mid-sentence must not
      # learn the speech as noise.
      self.noise_db = min(db, INITIAL_FLOOR_DB)
    raw = db >= max(self.noise_db + self.margin_db, self.min_speech_db)
    # Noise floor: falls fast to any quieter frame, rises slowly (~6 s time
    # constant at 30 ms frames) on every frame -- including "speech" ones, so
    # a step up in steady cabin noise cannot hold the detector on forever.
    alpha = 0.5 if db < self.noise_db else 0.005
    self.noise_db += alpha * (db - self.noise_db)
    self._recent.append(raw)

    if raw:
      self._speech += 1
      self._silence = 0
      self._hangover = self.hangover_frames
    else:
      self._silence += 1
      if self._hangover > 0:
        self._hangover -= 1

    if not self.active and self._speech >= self.start_frames:
      self.active = True
    elif self.active and self._silence >= self.stop_frames and self._hangover == 0:
      self.active = False
      self._speech = 0

  def process(self, audio: np.ndarray) -> bool:
    """Feed float mono audio of any length; returns the current speech state."""
    buf = np.concatenate((self._pending, np.asarray(audio, dtype=np.float32)))
    n = buf.size // self.frame_len
    for i in range(n):
      self._frame(buf[i * self.frame_len:(i + 1) * self.frame_len])
    self._pending = buf[n * self.frame_len:]
    return self.active
