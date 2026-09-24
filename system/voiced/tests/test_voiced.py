import numpy as np
import pytest

from openpilot.system.voiced.beamformer import SPEED_OF_SOUND_MPS, Beamformer, MicArray, steering_delays
from openpilot.system.voiced.vad import FRAME_MS, SILENCE_DB, VAD, level_db

RATE = 16000
FRAME = RATE * FRAME_MS // 1000


def tone(seconds, amp, freq=440.0):
  t = np.arange(int(RATE * seconds)) / RATE
  return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def noise(seconds, amp, seed=0):
  return (amp * np.random.default_rng(seed).standard_normal(int(RATE * seconds))).astype(np.float32)


class TestBeamformer:
  def test_mono_is_passthrough(self):
    pcm = np.array([0, 16384, -16384, 32767], dtype=np.int16)
    out = Beamformer().process(pcm)
    assert out.dtype == np.float32
    np.testing.assert_allclose(out, pcm / 32768.0)

  def test_broadside_is_channel_average(self):
    arr = MicArray(num_channels=2, spacing_m=0.05, target_angle_deg=0.0)
    left = np.full(8, 1000, dtype=np.int16)
    right = np.full(8, 3000, dtype=np.int16)
    out = Beamformer(arr, RATE).process(np.stack([left, right], axis=1).ravel())
    np.testing.assert_allclose(out, 2000 / 32768.0)

  def test_steering_realigns_delayed_channel(self):
    # 90 deg end-fire: channel 1 hears the wave `delay` samples later.
    spacing = 5 * SPEED_OF_SOUND_MPS / RATE
    arr = MicArray(num_channels=2, spacing_m=spacing, target_angle_deg=90.0)
    assert list(steering_delays(arr, RATE)) == [0, 5]
    sig = (tone(0.02, 0.5) * 32767).astype(np.int16)
    ch1 = np.concatenate((np.zeros(5, np.int16), sig[:-5]))
    out = Beamformer(arr, RATE).process(np.stack([sig, ch1], axis=1).ravel())
    np.testing.assert_allclose(out[:-5], sig[:-5] / 32768.0, atol=1e-6)

  def test_rejects_zero_channels(self):
    with pytest.raises(ValueError):
      Beamformer(MicArray(num_channels=0))


class TestVAD:
  def test_level_db(self):
    assert level_db(np.zeros(10, np.float32)) == SILENCE_DB
    assert level_db(np.full(10, 0.5, np.float32)) == pytest.approx(-6.02, abs=0.01)

  def test_silence_then_speech_then_silence(self):
    vad = VAD(RATE)
    assert vad.process(noise(1.0, 1e-3)) is False
    assert vad.process(tone(0.5, 0.3)) is True
    assert vad.confidence == 1.0
    # Hangover keeps it active briefly, then it releases.
    assert vad.process(noise(0.05, 1e-3, seed=1)) is True
    assert vad.process(noise(1.0, 1e-3, seed=2)) is False

  def test_needs_three_frames_to_start(self):
    vad = VAD(RATE)
    vad.process(noise(0.5, 1e-3))
    assert vad.process(tone(2 * FRAME / RATE, 0.3)) is False
    assert vad.process(tone(FRAME / RATE, 0.3)) is True

  def test_step_up_in_noise_releases(self):
    """Quiet cabin, then a steady 20 dB louder fan: may trigger, must release."""
    vad = VAD(RATE)
    vad.process(noise(1.0, 1e-3))           # ~-60 dBFS
    vad.process(noise(10.0, 0.01, seed=3))  # ~-40 dBFS, steady
    assert vad.active is False
    assert -47 < vad.noise_db < -38         # floor settles near the noise's quiet frames
    assert vad.process(tone(0.5, 0.3)) is True   # speech still detected over it

  def test_partial_frames_are_buffered(self):
    vad = VAD(RATE)
    for chunk in np.array_split(tone(0.3, 0.3), 37):
      vad.process(chunk)
    assert vad.active is True


class TestDaemon:
  def test_publishes_mic_status(self, monkeypatch):
    from cereal import messaging
    from openpilot.system.voiced import voiced

    audio = messaging.new_message('rawAudioData')
    audio.rawAudioData.sampleRate = RATE
    audio.rawAudioData.data = (tone(0.5, 0.3) * 32767).astype(np.int16).tobytes()
    events = [audio.as_reader()]
    monkeypatch.setattr(voiced.messaging, "drain_sock", lambda sock: [events.pop()] if events else [])

    sent = []

    class PM:
      def send(self, name, msg):
        sent.append((name, msg.as_reader()))

    d = voiced.VoiceD(sock=object(), pm=PM())
    d.step()
    name, msg = sent[-1]
    assert name == 'micStatus'
    assert msg.micStatus.vadActive is True
    assert msg.micStatus.micLevelDb > -20
    assert msg.micStatus.wakeWordActive is False and msg.micStatus.sttActive is False


  def test_stereo_pair_is_beamformed(self, monkeypatch):
    from cereal import messaging
    from openpilot.system.voiced import voiced

    left = (tone(0.5, 0.3) * 32767).astype(np.int16)
    audio = messaging.new_message('rawAudioData')
    audio.rawAudioData.sampleRate = RATE
    audio.rawAudioData.channels = 2
    audio.rawAudioData.data = np.stack([left, left], axis=1).tobytes()
    events = [audio.as_reader()]
    monkeypatch.setattr(voiced.messaging, "drain_sock", lambda sock: [events.pop()] if events else [])

    class PM:
      def send(self, name, msg):
        self.last = msg.as_reader()

    d = voiced.VoiceD(sock=object(), pm=PM())
    d.step()
    assert d.beamformer.array.num_channels == 2
    assert d.pm.last.micStatus.vadActive is True
