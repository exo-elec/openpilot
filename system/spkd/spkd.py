#!/usr/bin/env python3
"""
spkd - Speaker Output Daemon

Audio output via I2S to speaker/amplifier.
Receives audio data from higher-level daemons (soundd for TTS/tones, ui for alerts).

Hardware: I2S DAC (PCM5102A)
- RK3588 (ExoPilot 01M): Has speaker for alert tones and TTS output
  (no microphone — voice input pipeline disabled)
"""

from __future__ import annotations

import threading
import queue

import numpy as np

from cereal import messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.common.core_config import set_daemon_affinity
from openpilot.system.hardware import HAS_SPEAKER

# BSP I2S HAL
try:
    from openpilot.system.hardware.i2s_audio import get_i2s_hal
    I2S_HAL_AVAILABLE = True
except ImportError:
    I2S_HAL_AVAILABLE = False
    cloudlog.warning("spkd: I2S HAL not available, using mock")

    class MockI2S:
        def start_playback(self): pass
        def stop_playback(self): pass
        def write_samples(self, audio): pass

    def get_i2s_hal():
        return MockI2S()

SAMPLE_RATE = 48000
CHUNK_SIZE = 480  # 10ms at 48kHz


class SpkD:
    """Speaker output daemon."""

    def __init__(self):
        set_daemon_affinity("spkd")

        # Auto-detect speaker hardware
        self.hardware_available = HAS_SPEAKER and I2S_HAL_AVAILABLE

        if not self.hardware_available:
            cloudlog.info("spkd: No speaker hardware detected, entering standby mode")
        else:
            cloudlog.info("spkd: Speaker hardware detected, starting playback")

        # BSP I2S HAL (only used if hardware available)
        self.i2s = get_i2s_hal() if self.hardware_available else None
        if self.hardware_available:
            self.i2s.start_playback()

        # Messaging
        self.pm = messaging.PubMaster(['spkdStatus'])
        self.sm = messaging.SubMaster(['audioData'])  # From soundd

        # Audio queue
        self.audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=100)
        self.volume = 1.0
        self.is_playing = False

        # Playback thread
        self.playback_thread: threading.Thread | None = None
        self.exit_event = threading.Event()

        cloudlog.info("spkd: Initialized")

    def _playback_loop(self):
        """Audio playback thread."""
        if not self.hardware_available:
            # Standby: no actual playback
            while not self.exit_event.is_set():
                self.exit_event.wait(0.1)
            return

        while not self.exit_event.is_set():
            try:
                # Get audio chunk from queue
                audio = self.audio_queue.get(timeout=0.1)

                # Apply volume
                audio = audio * self.volume

                # Play via I2S
                self.i2s.write_samples(audio)
                self.is_playing = True

            except queue.Empty:
                self.is_playing = False
                continue
            except Exception as e:
                cloudlog.error(f"spkd: Playback error: {e}")

    def _handle_audio_data(self):
        """Handle incoming audio data."""
        if not self.sm.updated['audioData']:
            return

        if not self.hardware_available:
            # Discard audio data in standby mode
            return

        audio_msg = self.sm['audioData']

        # Convert bytes to numpy array
        audio_bytes = audio_msg.data
        audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        # Split into chunks
        for i in range(0, len(audio), CHUNK_SIZE):
            chunk = audio[i:i + CHUNK_SIZE]
            if len(chunk) < CHUNK_SIZE:
                # Pad last chunk
                chunk = np.pad(chunk, (0, CHUNK_SIZE - len(chunk)))

            try:
                self.audio_queue.put(chunk, block=False)
            except queue.Full:
                cloudlog.warning("spkd: Audio queue full, dropping chunk")
                break

    def update(self):
        """Main update loop."""
        self.sm.update(0)
        self._handle_audio_data()

        # Publish status
        msg = messaging.new_message('spkdStatus', valid=True)
        msg.spkdStatus.isPlaying = self.is_playing
        msg.spkdStatus.queueDepth = self.audio_queue.qsize()
        msg.spkdStatus.volume = self.volume
        self.pm.send('spkdStatus', msg)

    def run(self):
        """Main daemon loop."""
        # Start playback thread
        self.playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self.playback_thread.start()

        rk = Ratekeeper(100)  # 100Hz
        cloudlog.info("spkd: Running")

        try:
            while True:
                self.update()
                rk.keep_time()
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """Stop daemon."""
        self.exit_event.set()
        if self.playback_thread:
            self.playback_thread.join(timeout=1.0)
        if self.i2s:
            self.i2s.stop_playback()
        cloudlog.info("spkd: Stopped")


def main():
    daemon = SpkD()
    try:
        daemon.run()
    except KeyboardInterrupt:
        daemon.stop()


if __name__ == "__main__":
    main()
