"""Capture gating, privacy, bounds and echo suppression without device I/O."""
import importlib
import sys
from types import ModuleType, SimpleNamespace as NS

import numpy as np
import pytest


class Params:
  def __init__(self): self.values = {'EOPCloudVoiceEnabled': True}
  def get_bool(self, name): return self.values.get(name, False)
  def put_bool(self, name, value): self.values[name] = value


class Playback:
  valid = {'audioStatus': True}
  playing = False
  def update(self, timeout): pass
  def __getitem__(self, name): return NS(ttsPlaying=self.playing)


@pytest.fixture
def capture(monkeypatch):
  messaging = ModuleType('cereal.messaging')
  events, sent = [], []
  messaging.new_message = lambda name, **kw: NS(**{name: NS()})
  messaging.drain_sock = lambda sock: [events.pop(0)] if events else []
  cereal = ModuleType('cereal');cereal.messaging = messaging
  monkeypatch.setitem(sys.modules, 'cereal', cereal)
  monkeypatch.setitem(sys.modules, 'cereal.messaging', messaging)
  for name in ['core_config', 'realtime', 'swaglog']:
    module = ModuleType(name)
    module.set_daemon_affinity = lambda _: None
    module.Ratekeeper = object
    module.cloudlog = NS(info=lambda _: None)
    monkeypatch.setitem(sys.modules, 'openpilot.common.' + name, module)
  module_name = 'openpilot.system.voiced.voiced'
  package = importlib.import_module('openpilot.system.voiced')
  previous_module = sys.modules.pop(module_name, None)
  previous_attribute = getattr(package, 'voiced', None)
  voiced = importlib.import_module(module_name)
  params, playback = Params(), Playback()
  d = voiced.VoiceD(sock=object(), pm=NS(send=lambda name, msg: sent.append((name, msg))),
                     params=params, playback=playback)
  def feed(seconds=1):
    data = np.full(int(16000 * seconds), 10000, dtype=np.int16)
    events.append(NS(rawAudioData=NS(data=data.tobytes(), sampleRate=16000, channels=1)))
    d.step()
  try:
    yield d, params, playback, sent, feed
  finally:
    sys.modules.pop(module_name, None)
    if previous_module is not None:
      sys.modules[module_name] = previous_module
    if previous_attribute is not None:
      package.voiced = previous_attribute
    elif hasattr(package, 'voiced'):
      delattr(package, 'voiced')


def test_no_upload_without_hold_and_enabled(capture):
  d, params, _, sent, feed = capture
  feed()
  assert not d._utterance
  params.put_bool('EOPVoiceRecording', True)
  feed()
  params.put_bool('EOPCloudVoiceEnabled', False)
  params.put_bool('EOPVoiceRecording', False)
  d.step()
  assert not d._utterance and all(name == 'micStatus' for name, _ in sent)


def test_release_sends_bounded_mono_pcm(capture):
  d, params, _, sent, feed = capture
  params.put_bool('EOPVoiceRecording', True)
  feed()
  params.put_bool('EOPVoiceRecording', False)
  d.step()
  uploads = [m.voiceAudioChunk for name, m in sent if name == 'voiceAudioChunk']
  assert len(uploads) == 1 and len(uploads[0].chunk) == 32000
  assert (uploads[0].sampleRate, uploads[0].channels, uploads[0].sampleWidth) == (16000, 1, 2)
  assert not d._utterance


def test_max_duration_resets_capture(capture):
  d, params, _, sent, feed = capture
  params.put_bool('EOPVoiceRecording', True)
  feed(16)
  uploads = [m.voiceAudioChunk for name, m in sent if name == 'voiceAudioChunk']
  assert len(uploads[0].chunk) == 16000 * 2 * 15
  assert not params.get_bool('EOPVoiceRecording') and not d._recording


def test_playback_cancels_recording_not_upload_echo(capture):
  d, params, playback, sent, feed = capture
  params.put_bool('EOPVoiceRecording', True)
  feed()
  playback.playing = True
  feed()
  assert not d._utterance and not params.get_bool('EOPVoiceRecording')
  assert not any(name == 'voiceAudioChunk' for name, _ in sent)


def test_wake_starts_capture_and_pause_submits(capture):
  d, params, _, sent, feed = capture
  params.put_bool('EOPVoiceWakeTrigger', True)
  feed()
  assert d._recording and params.get_bool('EOPVoiceRecording')
  # Endpoint receives continued speech, then a pause after the VAD releases.
  assert d._endpoint.feed(16000, True) == ''
  assert d._endpoint.feed(16000, False) == 'send'
  d._finish()
  assert any(name == 'voiceAudioChunk' for name, _ in sent)


def test_stalled_microphone_expires_wake_session(capture):
  import time
  d, params, _, sent, _ = capture
  params.put_bool('EOPVoiceWakeTrigger', True)
  d.step()
  assert d._recording
  d._recording_started = time.monotonic() - 4
  d.step()
  assert not d._recording and not params.get_bool('EOPVoiceRecording')
  assert not any(name == 'voiceAudioChunk' for name, _ in sent)
