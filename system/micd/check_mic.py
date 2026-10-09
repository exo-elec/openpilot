#!/usr/bin/env python3
"""Check the board's microphone pair (read-only, records a few seconds).

  python3 -m openpilot.system.micd.check_mic [--seconds 3]

Speak or clap near the unit while it records. Reports:
  1. the capture device i2s_audio picks (the '*-Simple-Audio' card) and its
     channel count -- 2 on 01M/02M;
  2. that 16 kHz (micd's rate) opens on it;
  3. each channel's level: a dead or unwired mic reads silence;
  4. the correlation between the two mics: one sound source heard by both
     correlates strongly; ~0 means one channel is noise, and a peak lag
     far from 0 means the L/R pair is not sample-synced.
Exit 0 when both channels are live and correlated, else 1.
"""
from __future__ import annotations

import argparse

import numpy as np

SILENT_DBFS = -70.0
MIN_CORRELATION = 0.3
MAX_LAG = 16                   # samples @ 16 kHz, ~34 cm of path difference


def level_dbfs(x: np.ndarray) -> float:
  rms = float(np.sqrt(np.mean(np.square(x.astype(np.float64) / 32768.0)))) if x.size else 0.0
  return 20 * np.log10(rms) if rms > 0 else -96.0


def pair_correlation(a: np.ndarray, b: np.ndarray, max_lag: int = MAX_LAG) -> tuple[float, int]:
  """(peak normalised cross-correlation, lag in samples) within +-max_lag."""
  a = a.astype(np.float64) - a.mean()
  b = b.astype(np.float64) - b.mean()
  denom = np.sqrt(np.sum(a * a) * np.sum(b * b))
  if denom == 0:
    return 0.0, 0
  best, best_lag = 0.0, 0
  for lag in range(-max_lag, max_lag + 1):
    if lag >= 0:
      c = np.sum(a[lag:] * b[:len(b) - lag])
    else:
      c = np.sum(a[:lag] * b[-lag:])
    if abs(c) > abs(best):
      best, best_lag = c, lag
  return float(best / denom), best_lag


def evaluate(frames: np.ndarray) -> tuple[bool, list[str]]:
  """frames: int16 (n, channels)."""
  lines, ok = [], True
  ch = frames.shape[1]
  levels = [level_dbfs(frames[:, i]) for i in range(ch)]
  for i, db in enumerate(levels):
    live = db > SILENT_DBFS
    ok &= live
    lines.append(f"channel {i}: {db:6.1f} dBFS {'OK' if live else 'SILENT (dead or unwired mic?)'}")
  if ch < 2:
    lines.append("only 1 channel: expected the 2-mic pair on 01M/02M")
    return False, lines
  corr, lag = pair_correlation(frames[:, 0], frames[:, 1])
  good = abs(corr) >= MIN_CORRELATION
  ok &= good
  verdict = "OK" if good else "(low: one channel may be noise, or nothing was said)"
  lines.append(f"pair correlation {corr:+.2f} at lag {lag:+d} samples {verdict}")
  return ok, lines


def main(argv=None, sd=None) -> int:
  ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
  ap.add_argument("--seconds", type=float, default=3.0)
  args = ap.parse_args(argv)

  from openpilot.system.hardware import i2s_audio
  from openpilot.system.micd.micd import SAMPLE_RATE
  if sd is None:
    import sounddevice as sd
  hal = i2s_audio._SounddeviceAudio.__new__(i2s_audio._SounddeviceAudio)
  hal._sd = sd
  device, channels = hal._board_input_device()
  name = sd.query_devices()[device]["name"] if device is not None else "default input"
  print(f"device: {name} ({channels} ch)")
  if device is None:
    print("no '*-Simple-Audio' card: check the DTS simple_sound node and `arecord -l`")
  try:
    rec = sd.rec(int(args.seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=channels,
                 dtype='int16', device=device)
    sd.wait()
  except Exception as e:
    print(f"FAIL: cannot record {SAMPLE_RATE} Hz x{channels} on it: {e}")
    return 1
  print(f"recorded {args.seconds:.1f} s at {SAMPLE_RATE} Hz")
  ok, lines = evaluate(np.asarray(rec).reshape(-1, channels))
  print("\n".join(lines))
  print("PASS" if ok and device is not None else "FAIL")
  return 0 if ok and device is not None else 1


if __name__ == "__main__":
  raise SystemExit(main())
