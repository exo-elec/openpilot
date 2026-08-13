"""I2S audio HAL for ExoPilot.

On Rockchip hardware: wraps real I2S driver (BSP-provided).
On PC (dev machine): wraps sounddevice (ALSA/PulseAudio).

Interface:
  Playback: start_playback(), write_samples(int16_array), stop_playback()
  Capture:  start_capture(callback(int16_array)), stop_capture()
"""

from __future__ import annotations

import threading
import logging
import numpy as np

logger = logging.getLogger(__name__)


class _SounddeviceAudio:
    """PC audio backend using sounddevice (ALSA/PulseAudio)."""

    def __init__(self, playback_rate: int = 48000, capture_rate: int = 16000,
                 block_size: int = 512):
        import sounddevice as sd
        self._sd = sd
        self._playback_rate = playback_rate
        self._capture_rate = capture_rate
        self._block_size = block_size

        # Playback: push model via a write queue and output stream
        self._play_stream = None
        self._play_lock = threading.Lock()
        self._play_buf: list[np.ndarray] = []

        # Capture: callback model via input stream
        self._cap_stream = None
        self._cap_callback = None

    # ---- playback ----

    def start_playback(self):
        if self._play_stream is not None:
            return
        self._play_stream = self._sd.OutputStream(
            samplerate=self._playback_rate,
            channels=1,
            dtype='int16',
        )
        self._play_stream.start()
        logger.info("I2S (sounddevice) playback started at %d Hz", self._playback_rate)

    def write_samples(self, audio: np.ndarray):
        if self._play_stream is None:
            return
        if audio.dtype != np.int16:  # type: ignore[unreachable]
            audio = audio.astype(np.int16)
        try:
            self._play_stream.write(audio)
        except Exception as e:
            logger.warning("sounddevice write_samples: %s", e)

    def stop_playback(self):
        if self._play_stream is None:
            return
        try:
            self._play_stream.stop()
            self._play_stream.close()
        except Exception:
            pass
        self._play_stream = None
        logger.info("I2S (sounddevice) playback stopped")

    # ---- capture ----

    def start_capture(self, callback):
        """Start capture; callback(audio_int16) called for each block."""
        if self._cap_stream is not None:
            return
        self._cap_callback = callback

        def _cb(indata, frames, time_info, status):
            if self._cap_callback is not None:
                samples = indata[:, 0].copy()  # mono
                if samples.dtype != np.int16:
                    samples = (samples * 32767).astype(np.int16)
                self._cap_callback(samples)

        self._cap_stream = self._sd.InputStream(
            samplerate=self._capture_rate,
            channels=1,
            dtype='int16',
            blocksize=self._block_size,
            callback=_cb,
        )
        self._cap_stream.start()
        logger.info("I2S (sounddevice) capture started at %d Hz", self._capture_rate)

    def stop_capture(self):
        if self._cap_stream is None:
            return
        try:
            self._cap_stream.stop()
            self._cap_stream.close()
        except Exception:
            pass
        self._cap_stream = None
        self._cap_callback = None
        logger.info("I2S (sounddevice) capture stopped")


def get_i2s_hal():
    """Return the best available I2S HAL.

    On Rockchip hardware: real I2S driver (not yet implemented — stub raises).
    On PC: sounddevice ALSA backend.
    """
    try:
        import sounddevice  # noqa: F401
        return _SounddeviceAudio()
    except ImportError as e:
        raise ImportError("sounddevice not available — install: pip install sounddevice") from e
