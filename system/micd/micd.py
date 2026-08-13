#!/usr/bin/env python3
"""
micd - Microphone Input Daemon

Captures audio from microphone via I2S.
Publishes raw audio data for voiced (STT) and sound pressure levels.

Hardware: RK3588 (ExoPilot 01M) has no on-board microphone —
daemon always runs in standby mode.
"""

from __future__ import annotations

import numpy as np
from functools import cache
import threading

from cereal import messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.common.core_config import set_daemon_affinity
from openpilot.system.hardware import HAS_VOICE_INPUT

# BSP I2S HAL
try:
    from openpilot.system.hardware.i2s_audio import get_i2s_hal
    I2S_HAL_AVAILABLE = True
except ImportError:
    I2S_HAL_AVAILABLE = False
    cloudlog.warning("micd: I2S HAL not available, using mock")

    class MockI2S:
        def start_capture(self, cb): pass
        def stop_capture(self): pass

    def get_i2s_hal():
        return MockI2S()

RATE = 10  # 10Hz publish rate
FFT_SAMPLES = 1600  # 100ms
REFERENCE_SPL = 2e-5  # newtons/m^2
SAMPLE_RATE = 16000
SAMPLE_BUFFER = 800  # 50ms


@cache
def get_a_weighting_filter():
    """Calculate A-weighting filter for SPL measurement."""
    freqs = np.fft.fftfreq(FFT_SAMPLES, d=1 / SAMPLE_RATE)
    A = 12194 ** 2 * freqs ** 4 / ((freqs ** 2 + 20.6 ** 2) * (freqs ** 2 + 12194 ** 2) * np.sqrt((freqs ** 2 + 107.7 ** 2) * (freqs ** 2 + 737.9 ** 2)))
    return A / np.max(A)


def calculate_spl(measurements):
    """Calculate sound pressure level in dB."""
    sound_pressure = np.sqrt(np.mean(measurements ** 2))
    if sound_pressure > 0:
        return 20 * np.log10(sound_pressure / REFERENCE_SPL)
    return 0


def apply_a_weighting(measurements: np.ndarray) -> np.ndarray:
    """Apply A-weighting filter to audio signal."""
    measurements_windowed = measurements * np.hanning(len(measurements))
    return np.abs(np.fft.ifft(np.fft.fft(measurements_windowed) * get_a_weighting_filter()))


class MicD:
    """Microphone input daemon."""

    def __init__(self):
        set_daemon_affinity("micd")

        # Auto-detect voice input hardware (RK3588 has no on-board mic)
        self.hardware_available = HAS_VOICE_INPUT and I2S_HAL_AVAILABLE

        if not self.hardware_available:
            cloudlog.info("micd: No voice input hardware detected, entering standby mode")
        else:
            cloudlog.info("micd: Microphone hardware detected, starting capture")

        self.rk = Ratekeeper(RATE)
        self.pm = messaging.PubMaster(['soundPressure', 'rawAudioData'])

        self.measurements = np.empty(0)
        self.sound_pressure = 0
        self.sound_pressure_weighted = 0
        self.sound_pressure_level_weighted = 0

        self.lock = threading.Lock()

        # BSP I2S HAL (only used if hardware available)
        self.i2s = get_i2s_hal() if self.hardware_available else None

        cloudlog.info("micd: Initialized")

    def _audio_callback(self, audio_int16: np.ndarray):
        """Called by I2S HAL with audio data."""
        if not self.hardware_available:
            return

        # Publish raw audio for voiced
        msg = messaging.new_message('rawAudioData', valid=True)
        msg.rawAudioData.data = audio_int16.tobytes()
        msg.rawAudioData.sampleRate = SAMPLE_RATE
        self.pm.send('rawAudioData', msg)

        # Process for SPL
        audio_float = audio_int16.astype(np.float32) / 32768.0

        with self.lock:
            self.measurements = np.concatenate((self.measurements, audio_float))

            while self.measurements.size >= FFT_SAMPLES:
                measurements = self.measurements[:FFT_SAMPLES]

                self.sound_pressure, _ = calculate_spl(measurements)
                measurements_weighted = apply_a_weighting(measurements)
                self.sound_pressure_weighted, self.sound_pressure_level_weighted = calculate_spl(measurements_weighted)

                self.measurements = self.measurements[FFT_SAMPLES:]

    def update(self):
        """Update loop - publish SPL measurements."""
        if not self.hardware_available:
            # Standby: publish silent SPL and empty audio
            msg = messaging.new_message('soundPressure', valid=True)
            msg.soundPressure.soundPressure = 0.0
            msg.soundPressure.soundPressureWeighted = 0.0
            msg.soundPressure.soundPressureWeightedDb = 0.0
            self.pm.send('soundPressure', msg)
            self.rk.keep_time()
            return

        with self.lock:
            spl = self.sound_pressure
            spl_w = self.sound_pressure_weighted
            spl_db = self.sound_pressure_level_weighted

        msg = messaging.new_message('soundPressure', valid=True)
        msg.soundPressure.soundPressure = float(spl)
        msg.soundPressure.soundPressureWeighted = float(spl_w)
        msg.soundPressure.soundPressureWeightedDb = float(spl_db)

        self.pm.send('soundPressure', msg)
        self.rk.keep_time()

    def run(self):
        """Main daemon loop."""
        if self.hardware_available:
            # Start I2S capture
            self.i2s.start_capture(self._audio_callback)
            cloudlog.info("micd: Capture started")

        try:
            while True:
                self.update()
        finally:
            if self.i2s:
                self.i2s.stop_capture()

    def stop(self):
        """Stop daemon."""
        if self.i2s:
            self.i2s.stop_capture()
        cloudlog.info("micd: Stopped")


def main():
    daemon = MicD()
    try:
        daemon.run()
    except KeyboardInterrupt:
        daemon.stop()


if __name__ == "__main__":
    main()
