#!/usr/bin/env python3
"""
soundd - Sound Output Daemon

Handles audio output:
- TTS (Piper): navigation announcements and alerts from navd/selfdrived
- Alert tones/beeps: AudibleAlert values from selfdriveState

Sends PCM audio to spkd for I2S hardware output.

Priority:
  1. Critical alert tones (warningImmediate) — interrupt everything
  2. TTS (navigation, alerts) — interruptible by higher priority
  3. Normal alert tones (prompt, engage) — play when idle
"""

from __future__ import annotations

import time
import threading

import numpy as np

from cereal import messaging, car
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.common.core_config import set_daemon_affinity

from openpilot.selfdrive.soundd.piper_tts import PiperTTS, resolve_voice
from openpilot.selfdrive.soundd.tone_generator import get_alert_tone, SAMPLE_RATE as TONE_SAMPLE_RATE

AudibleAlert = car.CarControl.HUDControl.AudibleAlert


class SoundD:
    """Sound output daemon — TTS + alert tones."""

    def __init__(self):
        set_daemon_affinity("soundd")

        cloudlog.info("soundd: Initializing...")

        # Resolve voice from EOPLanguage + EOPTTSVoice params
        params = Params()
        language = (params.get("EOPLanguage") or b"en").decode()
        voice_param = (params.get("EOPTTSVoice") or b"auto").decode()
        voice_id = resolve_voice(voice_param, language)
        cloudlog.info(f"soundd: language={language} voice_param={voice_param} → voice={voice_id}")

        self.params = params
        self._current_language = language
        self._current_voice_param = voice_param

        self.tts = PiperTTS(voice_id=voice_id)
        self._voice_reload_lock = threading.Lock()

        # Messaging
        self.pm = messaging.PubMaster(['audioData', 'sounddStatus'])
        self.sm = messaging.SubMaster(['ttsRequest', 'selfdriveState'])

        # Playback state
        self.is_playing = False

        # Alert sound tracking
        self._last_alert_sound = AudibleAlert.none
        self._alert_tone_cooldown = 0.0

        # Param poll: check EOPLanguage/EOPTTSVoice every 5s for hot-reload
        self._last_param_check = 0.0

        cloudlog.info("soundd: Initialized")
    
    def _send_audio(self, audio_int16: np.ndarray, sample_rate: int):
        """Send audio data to spkd via audioData message."""
        msg = messaging.new_message('audioData', valid=True)
        msg.audioData.data = audio_int16.tobytes()
        msg.audioData.sampleRate = sample_rate
        self.pm.send('audioData', msg)
        self.is_playing = True
    
    def _play_tts(self, text: str):
        """Play TTS using Piper."""
        cloudlog.info(f"soundd: TTS: {text}")

        with self._voice_reload_lock:
            audio = self.tts.synthesize(text)
        if audio is None:
            return
        
        # Convert to int16
        audio_int16 = (audio * 32767).astype(np.int16)
        
        # Send to spkd
        self._send_audio(audio_int16, self.tts.sample_rate)
    
    def _play_alert_tone(self, audible_alert: AudibleAlert):
        """Play alert tone for AudibleAlert value."""
        tone = get_alert_tone(audible_alert)
        if tone is None:
            return
        
        cloudlog.info(f"soundd: Alert tone: {audible_alert}")
        self._send_audio(tone, TONE_SAMPLE_RATE)
    
    def _check_alert_sound(self):
        """Check for alert sound changes in selfdriveState."""
        if not self.sm.updated['selfdriveState']:
            return
        
        ss = self.sm['selfdriveState']
        current_alert_sound = ss.alertSound
        
        # Only play when alert sound changes to a new non-none value
        if current_alert_sound == AudibleAlert.none:
            self._last_alert_sound = AudibleAlert.none
            return
        
        if current_alert_sound == self._last_alert_sound:
            # Same alert sound still active — don't repeat too fast
            return
        
        # Cooldown check (prevent rapid-fire tones)
        now = time.monotonic()
        if now < self._alert_tone_cooldown:
            return
        
        self._last_alert_sound = current_alert_sound
        self._alert_tone_cooldown = now + 0.5  # 500ms minimum between tones
        
        self._play_alert_tone(current_alert_sound)
    
    def _check_voice_params(self):
        """Poll EOPLanguage + EOPTTSVoice every 5s; reload voice in background if changed."""
        now = time.monotonic()
        if now - self._last_param_check < 5.0:
            return
        self._last_param_check = now

        language = (self.params.get("EOPLanguage") or b"en").decode()
        voice_param = (self.params.get("EOPTTSVoice") or b"auto").decode()

        if language == self._current_language and voice_param == self._current_voice_param:
            return

        new_voice_id = resolve_voice(voice_param, language)
        cloudlog.info(f"soundd: voice param changed → language={language} voice={new_voice_id}")
        self._current_language = language
        self._current_voice_param = voice_param

        # Load new model in background so 100Hz loop is not blocked
        def _reload():
            new_tts = PiperTTS(voice_id=new_voice_id)
            with self._voice_reload_lock:
                self.tts = new_tts
            cloudlog.info(f"soundd: voice hot-reloaded → {new_voice_id}")

        threading.Thread(target=_reload, daemon=True).start()

    def update(self):
        """Main update loop."""
        self.sm.update(0)

        # Hot-reload voice if params changed (non-blocking)
        self._check_voice_params()

        # Check for alert sounds first (higher priority)
        self._check_alert_sound()

        # Check for TTS requests
        if self.sm.updated['ttsRequest']:
            tts = self.sm['ttsRequest']
            self._play_tts(tts.text)

        # Publish status
        msg = messaging.new_message('sounddStatus', valid=True)
        msg.sounddStatus.isPlaying = self.is_playing
        self.pm.send('sounddStatus', msg)

        # Reset playing flag (spkd handles actual playback timing)
        self.is_playing = False
    
    def run(self):
        """Main daemon loop."""
        rk = Ratekeeper(100)
        cloudlog.info("soundd: Running")
        
        while True:
            self.update()
            rk.keep_time()
    
    def stop(self):
        """Stop daemon."""
        cloudlog.info("soundd: Stopped")


def main():
    daemon = SoundD()
    try:
        daemon.run()
    except KeyboardInterrupt:
        daemon.stop()


if __name__ == "__main__":
    main()
