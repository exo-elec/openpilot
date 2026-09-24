#!/usr/bin/env python3
"""
voiced - local voice front end (beamformer + VAD), 02M.

Reads micd's rawAudioData, beamforms (pass-through while the mic array
geometry is unknown, see beamformer.py), runs voice activity detection and
publishes micStatus (vadActive, micLevelDb) at 10 Hz.

Deliberately local and inert (user decision 2026-09-24): no wake word, no
speech-to-text, no cloud assistant, no network, and nothing acts on the
result yet. Runs only when EOPVoiceEnabled is set (manager defaults it to the
hardware's HAS_VOICE_INPUT).
"""
from __future__ import annotations

import numpy as np

from cereal import messaging
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.system.voiced.beamformer import MONO, Beamformer
from openpilot.system.voiced.vad import VAD

RATE_HZ = 10
DEFAULT_SAMPLE_RATE = 16000


class VoiceD:
  def __init__(self, sock=None, pm=None):
    # Every chunk, not just the latest (SubMaster conflates): the VAD needs
    # contiguous audio.
    self.sock = sock or messaging.sub_sock('rawAudioData', conflate=False)
    self.pm = pm or messaging.PubMaster(['micStatus'])
    self.sample_rate = DEFAULT_SAMPLE_RATE
    self.beamformer = Beamformer(MONO, self.sample_rate)
    self.vad = VAD(self.sample_rate)

  def _reset(self, sample_rate: int) -> None:
    cloudlog.info(f"voiced: sample rate {sample_rate} Hz")
    self.sample_rate = sample_rate
    self.beamformer = Beamformer(self.beamformer.array, sample_rate)
    self.vad = VAD(sample_rate)

  def step(self) -> None:
    for evt in messaging.drain_sock(self.sock):
      audio = evt.rawAudioData
      rate = int(audio.sampleRate) or DEFAULT_SAMPLE_RATE
      if rate != self.sample_rate:
        self._reset(rate)
      pcm = np.frombuffer(audio.data, dtype=np.int16)
      self.vad.process(self.beamformer.process(pcm))

    msg = messaging.new_message('micStatus', valid=True)
    msg.micStatus.vadActive = bool(self.vad.active)
    msg.micStatus.micLevelDb = float(self.vad.level_db)
    msg.micStatus.wakeWordActive = False
    msg.micStatus.sttActive = False
    self.pm.send('micStatus', msg)


def main():
  set_daemon_affinity("voiced")
  d = VoiceD()
  rk = Ratekeeper(RATE_HZ, print_delay_threshold=None)
  cloudlog.info("voiced: local beamformer + VAD running (no wake word/STT/cloud)")
  while True:
    d.step()
    rk.keep_time()


if __name__ == "__main__":
  main()
