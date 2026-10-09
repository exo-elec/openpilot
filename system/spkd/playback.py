"""Bounded PCM speaker arbitration: alert tones always preempt online speech."""
from collections import deque
from threading import Lock

import numpy as np


class SpeechBuffer:
  def __init__(self):
    self._lock = Lock()
    self._samples = deque()

  def replace(self, pcm: bytes, rate=48000, channels=1):
    if rate != 48000 or channels != 1 or len(pcm) % 2 or len(pcm) > 48000 * 2 * 30:
      raise ValueError('Expected bounded 48 kHz mono PCM16')
    samples = np.frombuffer(pcm, dtype='<i2')
    with self._lock:
      self._samples.clear()
      self._samples.extend(samples[i:i + 480].copy() for i in range(0, len(samples), 480))

  def clear(self):
    with self._lock:
      self._samples.clear()

  def pop(self):
    with self._lock:
      return self._samples.popleft() if self._samples else None

  def __bool__(self):
    with self._lock:
      return bool(self._samples)


def volume_pcm(samples, volume):
  return np.clip(samples.astype(np.float32) * volume, -32768, 32767).astype(np.int16)
