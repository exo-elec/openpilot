#!/usr/bin/env python3
"""
voiced - local voice front end (beamformer + VAD), 01M/02M.

Reads micd's rawAudioData (the boards' 2-mic pair, interleaved), beamforms
broadside (see beamformer.py), runs voice activity detection and publishes
micStatus (vadActive, micLevelDb) at 10 Hz.

Speech recognition and synthesis run online through cloudd. This daemon only
beamforms, measures activity and buffers explicitly requested utterances.
"""
from __future__ import annotations

import time

import numpy as np

from cereal import messaging
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.system.voiced.beamformer import MONO, Beamformer, MicArray
from openpilot.system.voiced.vad import VAD
from openpilot.system.voiced.utterance import Endpoint

RATE_HZ = 10
DEFAULT_SAMPLE_RATE = 16000


class VoiceD:
  def __init__(self, sock=None, pm=None, params=None, playback=None):
    # Every chunk, not just the latest (SubMaster conflates): the VAD needs
    # contiguous audio.
    self.sock = sock or messaging.sub_sock('rawAudioData', conflate=False)
    self.pm = pm or messaging.PubMaster(['micStatus', 'voiceAudioChunk', 'voiceFrame'])
    if params is None:
      from openpilot.common.params import Params
      params = Params()
    self.params = params
    self.params.put_bool("EOPVoiceRecording", False)
    self.playback = playback or messaging.SubMaster(['audioStatus'])
    self._recording = False
    self._recording_started = 0.0
    self._utterance = bytearray()
    self._pre_roll = bytearray()
    self._sequence = 0
    self._endpoint = None
    self.params.put_bool("EOPVoiceWakeTrigger", False)
    self.sample_rate = DEFAULT_SAMPLE_RATE
    self.beamformer = Beamformer(MONO, self.sample_rate)
    self.vad = VAD(self.sample_rate)

  def _reset(self, sample_rate: int, channels: int) -> None:
    cloudlog.info(f"voiced: {sample_rate} Hz, {channels} ch")
    self._utterance.clear()
    self.sample_rate = sample_rate
    array = MONO if channels == 1 else MicArray(num_channels=channels, spacing_m=0.0, target_angle_deg=0.0)
    self.beamformer = Beamformer(array, sample_rate)
    self.vad = VAD(sample_rate)

  def step(self) -> None:
    self.playback.update(0)
    speaking = self.playback.valid['audioStatus'] and self.playback['audioStatus'].ttsPlaying
    if self.params.get_bool('EOPVoiceWakeTrigger'):
      self.params.put_bool('EOPVoiceWakeTrigger', False)
      if self.params.get_bool('EOPCloudVoiceEnabled') and not speaking and not self._recording:
        self._utterance = bytearray(self._pre_roll)
        self._endpoint = Endpoint()
        self.params.put_bool('EOPVoiceRecording', True)
    requested = (self.params.get_bool('EOPCloudVoiceEnabled') and
                 self.params.get_bool('EOPVoiceRecording') and not speaking)
    if speaking:
      self._utterance.clear()
      self.params.put_bool('EOPVoiceRecording', False)
    if self._recording and not requested:
      self._finish()
    if requested and not self._recording:
      self._recording_started = time.monotonic()
    self._recording = requested
    elapsed = time.monotonic() - self._recording_started
    if requested and (elapsed >= 15 or (self._endpoint and not self._endpoint.heard_speech and elapsed >= 3)):
      if self._endpoint and not self._endpoint.heard_speech:
        self._utterance.clear()
      self._finish()
      self.params.put_bool('EOPVoiceRecording', False)
      self._recording = False
    for evt in messaging.drain_sock(self.sock):
      audio = evt.rawAudioData
      rate = int(audio.sampleRate) or DEFAULT_SAMPLE_RATE
      channels = max(1, int(audio.channels))
      if rate != self.sample_rate or channels != self.beamformer.array.num_channels:
        self._reset(rate, channels)
      pcm = np.frombuffer(audio.data, dtype=np.int16)
      mono = self.beamformer.process(pcm)
      self.vad.process(mono)
      if rate == 16000:
        self._pre_roll.extend((np.clip(mono, -1, 1) * 32767).astype("<i2").tobytes())
        self._pre_roll = self._pre_roll[-8000:]
      if self.params.get_bool('EOPWakeEnabled') and rate == 16000 and not speaking:
        frame = messaging.new_message('voiceFrame', valid=True)
        frame.voiceFrame.data = (np.clip(mono, -1, 1) * 32767).astype('<i2').tobytes()
        frame.voiceFrame.sampleRate, frame.voiceFrame.channels = 16000, 1
        self.pm.send('voiceFrame', frame)
      if self._recording and rate == 16000:
        chunk = (np.clip(mono, -1, 1) * 32767).astype('<i2').tobytes()
        remaining = 16000 * 2 * 15 - len(self._utterance)
        self._utterance.extend(chunk[:remaining])
        end = self._endpoint.feed(len(mono), self.vad.active) if self._endpoint else ''
        if end == 'cancel':
          self._utterance.clear()
        if end or len(self._utterance) >= 16000 * 2 * 15:
          self._finish()
          self.params.put_bool('EOPVoiceRecording', False)
          self._recording = False

    msg = messaging.new_message('micStatus', valid=True)
    msg.micStatus.vadActive = bool(self.vad.active)
    msg.micStatus.micLevelDb = float(self.vad.level_db)
    msg.micStatus.wakeWordActive = False
    msg.micStatus.sttActive = False
    self.pm.send('micStatus', msg)


  def _finish(self):
    # Do not upload canceled/disabled sessions or very short taps.
    if self.params.get_bool('EOPCloudVoiceEnabled') and len(self._utterance) >= 16000:
      msg = messaging.new_message('voiceAudioChunk', valid=True)
      a = msg.voiceAudioChunk
      a.timestamp = time.monotonic_ns()
      a.sampleRate, a.sampleWidth, a.channels = 16000, 2, 1
      self._sequence += 1
      a.sequenceId, a.chunk = self._sequence, bytes(self._utterance)
      self.pm.send('voiceAudioChunk', msg)
    self._utterance.clear()
    self._endpoint = None


def main():
  set_daemon_affinity("voiced")
  d = VoiceD()
  rk = Ratekeeper(RATE_HZ, print_delay_threshold=None)
  cloudlog.info("voiced: local capture + beamformer/VAD; online inference via cloudd")
  while True:
    d.step()
    rk.keep_time()


if __name__ == "__main__":
  main()
