#!/usr/bin/env python3
"""Recognize a short activation phrase locally on CPU; no local STT or TTS."""
import os
from openpilot.system.waked.detector import WakeDetector


def main():
  from cereal import messaging
  from openpilot.common.params import Params
  from openpilot.common.core_config import set_daemon_affinity
  from openpilot.common.realtime import Ratekeeper
  from openpilot.common.swaglog import cloudlog
  set_daemon_affinity('waked')
  params = Params()
  sock = messaging.sub_sock('voiceFrame', conflate=False)
  playback = messaging.SubMaster(['audioStatus'])
  try:
    detector = WakeDetector(os.environ.get('EOP_WAKE_MODEL', 'hey_jarvis'))
  except Exception:
    detector = None
    cloudlog.error('waked: install CPU runtime and a valid phrase model; wake activation disabled')
  rk = Ratekeeper(20)
  was_blocked = False
  while True:
    playback.update(0)
    blocked = params.get_bool('EOPVoiceRecording') or params.get_bool('EOPVoiceProcessing') or (
      playback.valid['audioStatus'] and playback['audioStatus'].ttsPlaying)
    if detector and blocked and not was_blocked:
      detector.reset()
    was_blocked = blocked
    for evt in messaging.drain_sock(sock):
      frame = evt.voiceFrame
      if detector and frame.sampleRate == 16000 and frame.channels == 1:
        if blocked:
          continue
        elif detector.process(bytes(frame.data)):
          params.put_bool('EOPVoiceWakeTrigger', True)
          blocked = True
    rk.keep_time()


if __name__ == '__main__':
  main()
