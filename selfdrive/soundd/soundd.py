#!/usr/bin/env python3
"""soundd - Sound Output Daemon

Handles only local alert tones. Language/voice audio (TTS/STT) is sent to the
Azure server; no local Piper model is loaded.

Sends PCM alert tones to spkd for I2S hardware output.
"""

from __future__ import annotations

import time

import numpy as np

from cereal import messaging, car
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.common.core_config import set_daemon_affinity

from openpilot.selfdrive.soundd.tone_generator import get_alert_tone, SAMPLE_RATE as TONE_SAMPLE_RATE

AudibleAlert = car.CarControl.HUDControl.AudibleAlert


class SoundD:
    """Sound output daemon — local alert tones only."""

    def __init__(self):
        set_daemon_affinity("soundd")

        cloudlog.info("soundd: Initializing...")

        params = Params()
        self.params = params
        self._quiet_mode = params.get_bool("QuietMode")

        # Messaging
        self.pm = messaging.PubMaster(['audioData', 'sounddStatus'])
        self.sm = messaging.SubMaster(['selfdriveState'])

        # Playback state
        self.is_playing = False

        # Alert sound tracking
        self._last_alert_sound = AudibleAlert.none
        self._alert_tone_cooldown = 0.0

        # Param poll: check QuietMode every 5s
        self._last_param_check = 0.0
        # Alerts suppressed in quiet mode (keep warnings and prompts)
        self._QUIET_SUPPRESSED_ALERTS = {
          AudibleAlert.engage,
          AudibleAlert.disengage,
        }

        cloudlog.info("soundd: Initialized")

    def _send_audio(self, audio_int16: np.ndarray, sample_rate: int):
        """Send audio data to spkd via audioData message."""
        # Quiet mode: scale alert tone output to ~25% to avoid cabin disturbance
        if self._quiet_mode:
            audio_int16 = (audio_int16 * 0.25).astype(np.int16)

        msg = messaging.new_message('audioData', valid=True)
        msg.audioData.data = audio_int16.tobytes()
        msg.audioData.sampleRate = sample_rate
        self.pm.send('audioData', msg)
        self.is_playing = True

    def _play_alert_tone(self, audible_alert: AudibleAlert):
        """Play alert tone for AudibleAlert value."""
        if self._quiet_mode and audible_alert in self._QUIET_SUPPRESSED_ALERTS:
            cloudlog.debug(f"soundd: Suppressing {audible_alert} in quiet mode")
            return

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

    def _check_params(self):
        """Poll QuietMode every 5s."""
        now = time.monotonic()
        if now - self._last_param_check < 5.0:
            return
        self._last_param_check = now

        quiet_mode = self.params.get_bool("QuietMode")
        if quiet_mode != self._quiet_mode:
            cloudlog.info(f"soundd: quiet mode changed → {quiet_mode}")
            self._quiet_mode = quiet_mode

    def update(self):
        """Main update loop."""
        self.sm.update(0)

        # Check quiet-mode param
        self._check_params()

        # Check for alert sounds
        self._check_alert_sound()

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
