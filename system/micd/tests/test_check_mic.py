import numpy as np

from openpilot.system.micd import check_mic as cm

RATE = 16000


def voice(n, seed=0):
  return np.random.default_rng(seed).standard_normal(n) * 3000


def test_correlated_pair_passes():
  s = voice(RATE)
  frames = np.stack([s, np.roll(s, 3)], axis=1).astype(np.int16)
  ok, lines = cm.evaluate(frames)
  assert ok, lines
  corr, lag = cm.pair_correlation(frames[:, 0], frames[:, 1])
  assert corr > 0.9 and lag == -3


def test_dead_channel_fails():
  frames = np.stack([voice(RATE), np.zeros(RATE)], axis=1).astype(np.int16)
  ok, lines = cm.evaluate(frames)
  assert not ok and any("SILENT" in ln for ln in lines)


def test_uncorrelated_channels_fail():
  frames = np.stack([voice(RATE, 1), voice(RATE, 2)], axis=1).astype(np.int16)
  ok, _ = cm.evaluate(frames)
  assert not ok


def test_mono_fails():
  ok, lines = cm.evaluate(voice(RATE)[:, None].astype(np.int16))
  assert not ok and "only 1 channel" in lines[-1]


class FakeSD:
  def __init__(self, frames):
    self.frames = frames

  def query_devices(self, kind=None):
    if kind == 'input':
      return {"name": "default", "max_input_channels": 1}
    return [{"name": "EOP01M-Simple-Audio: - (hw:0,0)", "max_input_channels": 2}]

  def rec(self, n, samplerate, channels, dtype, device):
    assert (samplerate, channels, device) == (16000, 2, 0)
    return self.frames[:n]

  def wait(self):
    pass


def test_main_end_to_end(capsys):
  s = voice(RATE * 3)
  sd = FakeSD(np.stack([s, s], axis=1).astype(np.int16))
  assert cm.main(["--seconds", "1"], sd=sd) == 0
  out = capsys.readouterr().out
  assert "EOP01M-Simple-Audio" in out and "PASS" in out
