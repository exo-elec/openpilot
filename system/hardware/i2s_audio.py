"""I2S audio HAL for ExoPilot.

On Rockchip hardware: wraps real I2S driver (BSP-provided).
On PC (dev machine): wraps sounddevice (ALSA/PulseAudio).

Interface:
  Playback: start_playback(), write_samples(int16_array), stop_playback()
  Capture:  start_capture(callback(int16 array, shape (frames, channels))),
            stop_capture(); capture_channels after start

Both ExoPilot boards (01M, 02M) carry the same simple-I2S audio: a MAX98357A
mono amp and a 2-mic INMP441-class stereo pair on one I2S bus, exposed as the
ALSA card "EOP01M-Simple-Audio" / "EOP02M-Simple-Audio" (exopilot
kernel/dts simple_sound; docs/02-HARDWARE/RK3588_PINMUX_01M.md and
RK3576_PINMUX_02M.md section 4). Capture opens that card with both channels
when present, else the default input device (dev PC, USB mic).
"""

from __future__ import annotations

import threading
import logging
import numpy as np

BOARD_CARD_TAG = "simple-audio"   # EOP01M-/EOP02M-Simple-Audio
MAX_CAPTURE_CHANNELS = 2          # the boards' mic pair

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
        self.capture_channels = 0

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

    def _board_input_device(self) -> tuple[int | None, int]:
        """(device index, channels) of the board's simple-audio card, or
        (None, channels of the default input) when there is none."""
        try:
            devices = self._sd.query_devices()
        except Exception:
            devices = []
        for i, d in enumerate(devices):
            if BOARD_CARD_TAG in str(d.get("name", "")).lower() and d.get("max_input_channels", 0) > 0:
                return i, min(MAX_CAPTURE_CHANNELS, int(d["max_input_channels"]))
        try:
            d = self._sd.query_devices(kind='input')
            return None, max(1, min(MAX_CAPTURE_CHANNELS, int(d.get("max_input_channels", 1))))
        except Exception:
            return None, 1

    def _open_input(self, device, channels, cb):
        stream = self._sd.InputStream(
            device=device,
            samplerate=self._capture_rate,
            channels=channels,
            dtype='int16',
            blocksize=self._block_size,
            callback=cb,
        )
        stream.start()
        return stream

    def start_capture(self, callback):
        """Start capture; callback(int16 (frames, channels)) for each block."""
        if self._cap_stream is not None:
            return
        self._cap_callback = callback

        def _cb(indata, frames, time_info, status):
            if self._cap_callback is not None:
                samples = np.asarray(indata).copy()
                if samples.ndim == 1:
                    samples = samples[:, None]
                if samples.dtype != np.int16:
                    samples = (samples * 32767).astype(np.int16)
                self._cap_callback(samples)

        device, channels = self._board_input_device()
        try:
            self._cap_stream = self._open_input(device, channels, _cb)
        except Exception as e:
            # e.g. the hw device refuses 16 kHz: fall back to the default
            # (plug) device, which resamples.
            logger.warning("I2S capture on device %s x%d failed (%s); using default input", device, channels, e)
            device, channels = None, 1
            self._cap_stream = self._open_input(None, 1, _cb)
        self.capture_channels = channels
        logger.info("I2S (sounddevice) capture started at %d Hz, %d ch, device %s",
                    self._capture_rate, channels, device)

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
