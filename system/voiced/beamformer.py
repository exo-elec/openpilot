"""Delay-and-sum microphone beamformer (ported from VisionPilot src/voice/beamformer).

Multi-channel int16 audio -> one channel steered toward the driver. Local
only: no model, no network.

Both boards carry a 2-mic stereo pair (exopilot PINMUX docs section 4), so
voiced runs a 2-channel array steered broadside (0 deg): straight ahead of
the pair. At 0 deg delay-and-sum is the channel average whatever the
spacing, so no geometry is assumed. Steering toward the driver needs the
real mic spacing and orientation on the PCB, which are not documented yet --
do not fill them from VisionPilot's defaults (4 ch, 6 cm): those were
placeholders there too.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

SPEED_OF_SOUND_MPS = 343.0


@dataclass(frozen=True)
class MicArray:
  """Uniform linear array: channel i sits at i * spacing_m along the array axis."""
  num_channels: int = 1
  spacing_m: float = 0.0
  target_angle_deg: float = 0.0   # steering angle, 0 = broadside


MONO = MicArray()


def steering_delays(array: MicArray, sample_rate: int) -> np.ndarray:
  """Whole-sample delay per channel that aligns a plane wave from the target angle."""
  s = math.sin(math.radians(array.target_angle_deg))
  d = np.arange(array.num_channels) * array.spacing_m * s * sample_rate / SPEED_OF_SOUND_MPS
  return np.round(d).astype(np.int64)


class Beamformer:
  def __init__(self, array: MicArray = MONO, sample_rate: int = 16000):
    if array.num_channels < 1:
      raise ValueError("num_channels must be >= 1")
    self.array = array
    self.delays = steering_delays(array, sample_rate)

  def process(self, interleaved: np.ndarray) -> np.ndarray:
    """int16 interleaved (frames * channels) -> float32 mono in [-1, 1).

    Trailing samples that do not fill a whole multi-channel frame are dropped.
    """
    n = self.array.num_channels
    x = np.asarray(interleaved, dtype=np.float32) / 32768.0
    if n == 1:
      return x
    frames = x[: (x.size // n) * n].reshape(-1, n).T      # (channels, samples)
    out = np.zeros(frames.shape[1], dtype=np.float32)
    for ch, delay in enumerate(self.delays):
      shifted = np.roll(frames[ch], -int(delay))
      if delay > 0:
        shifted[-delay:] = 0.0
      elif delay < 0:
        shifted[:-delay] = 0.0
      out += shifted
    return out / n
