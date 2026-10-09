import numpy as np
import pytest

from openpilot.system.hardware import i2s_audio


class FakeStream:
  def __init__(self, **kw):
    self.kw = kw

  def start(self):
    if self.kw.get("device") == "bad":
      raise RuntimeError("rate not supported")


class FakeSD:
  def __init__(self, devices, default_in=1):
    self.devices = devices
    self.default_in = default_in
    self.opened = []

  def query_devices(self, kind=None):
    if kind == 'input':
      return {"name": "default", "max_input_channels": self.default_in}
    return self.devices

  def InputStream(self, **kw):
    s = FakeStream(**kw)
    self.opened.append(kw)
    return s


def _hal(sd):
  h = i2s_audio._SounddeviceAudio.__new__(i2s_audio._SounddeviceAudio)
  h._sd = sd
  h._capture_rate = 16000
  h._block_size = 512
  h._cap_stream = None
  h._cap_callback = None
  h.capture_channels = 0
  return h


def test_board_card_opened_with_both_mics():
  sd = FakeSD([{"name": "HDMI", "max_input_channels": 0},
               {"name": "EOP02M-Simple-Audio: - (hw:1,0)", "max_input_channels": 2}])
  h = _hal(sd)
  h.start_capture(lambda x: None)
  assert (sd.opened[-1]["device"], sd.opened[-1]["channels"]) == (1, 2)
  assert h.capture_channels == 2


def test_01m_card_name_matches_too():
  sd = FakeSD([{"name": "EOP01M-Simple-Audio: - (hw:0,0)", "max_input_channels": 8}])
  h = _hal(sd)
  h.start_capture(lambda x: None)
  assert sd.opened[-1]["channels"] == 2   # capped at the mic pair


def test_no_board_card_uses_default_input():
  sd = FakeSD([{"name": "USB mic", "max_input_channels": 1}], default_in=1)
  h = _hal(sd)
  h.start_capture(lambda x: None)
  assert (sd.opened[-1]["device"], h.capture_channels) == (None, 1)


def test_callback_delivers_frames_by_channels():
  sd = FakeSD([{"name": "EOP02M-Simple-Audio", "max_input_channels": 2}])
  h = _hal(sd)
  got = []
  h.start_capture(got.append)
  cb = sd.opened[-1]["callback"]
  cb(np.array([[1, 2], [3, 4]], dtype=np.int16), 2, None, None)
  assert got[0].shape == (2, 2) and got[0].dtype == np.int16


class _PM:
  def __init__(self):
    self.sent = []

  def send(self, name, msg):
    self.sent.append((name, msg.as_reader()))


def test_micd_publishes_interleaved_stereo(monkeypatch):
  from openpilot.system.micd import micd
  d = micd.MicD.__new__(micd.MicD)
  d.hardware_available = True
  d.pm = _PM()
  d.measurements = np.empty(0)
  import threading
  d.lock = threading.Lock()
  block = np.array([[100, -100], [200, -200], [300, -300]], dtype=np.int16)
  d._audio_callback(block)
  name, msg = d.pm.sent[-1]
  assert name == 'rawAudioData'
  assert msg.rawAudioData.channels == 2
  assert np.frombuffer(msg.rawAudioData.data, np.int16).tolist() == [100, -100, 200, -200, 300, -300]
  # SPL input is the channel mix (here exactly zero)
  assert d.measurements.tolist() == pytest.approx([0.0, 0.0, 0.0])
