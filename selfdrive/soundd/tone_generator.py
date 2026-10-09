#!/usr/bin/env python3
"""
Alert Tone Generator for soundd

Generates beep/chime tones programmatically (no sound files needed).
Maps AudibleAlert enum values to synthesized tones.

Tones are simple sine waves with envelope shaping.
"""

from __future__ import annotations

import numpy as np

from cereal import car

AudibleAlert = car.CarControl.HUDControl.AudibleAlert

SAMPLE_RATE = 48000  # Match spkd I2S rate


def generate_tone(
    frequency: float,
    duration: float,
    volume: float = 0.5,
    fade_in: float = 0.01,
    fade_out: float = 0.05,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """Generate a sine wave tone with envelope shaping."""
    num_samples = int(duration * sample_rate)
    t = np.linspace(0, duration, num_samples, endpoint=False)

    # Sine wave
    audio = np.sin(2 * np.pi * frequency * t)

    # Envelope (fade in/out to avoid clicks)
    envelope = np.ones(num_samples)
    fade_in_samples = int(fade_in * sample_rate)
    fade_out_samples = int(fade_out * sample_rate)

    if fade_in_samples > 0:
        envelope[:fade_in_samples] = np.linspace(0, 1, fade_in_samples)
    if fade_out_samples > 0:
        envelope[-fade_out_samples:] = np.linspace(1, 0, fade_out_samples)

    audio = audio * envelope * volume
    return audio.astype(np.float32)


def generate_ascending_tone(
    start_freq: float,
    end_freq: float,
    duration: float,
    volume: float = 0.5,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """Generate a frequency-sweep tone."""
    num_samples = int(duration * sample_rate)
    freqs = np.linspace(start_freq, end_freq, num_samples)
    phase = np.cumsum(2 * np.pi * freqs / sample_rate)
    audio = np.sin(phase)

    # Envelope
    envelope = np.ones(num_samples)
    fade_samples = int(0.02 * sample_rate)
    envelope[:fade_samples] = np.linspace(0, 1, fade_samples)
    envelope[-fade_samples:] = np.linspace(1, 0, fade_samples)

    audio = audio * envelope * volume
    return audio.astype(np.float32)


def generate_descending_tone(
    start_freq: float,
    end_freq: float,
    duration: float,
    volume: float = 0.5,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """Generate a descending tone (for disengage)."""
    return generate_ascending_tone(end_freq, start_freq, duration, volume, sample_rate)


def generate_double_beep(
    frequency: float,
    duration: float,
    gap: float,
    volume: float = 0.5,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """Generate two beeps with a gap (for promptRepeat)."""
    beep = generate_tone(frequency, duration, volume, sample_rate=sample_rate)
    gap_samples = int(gap * sample_rate)
    silence = np.zeros(gap_samples, dtype=np.float32)
    return np.concatenate([beep, silence, beep])


# Alert tone definitions (AudibleAlert.none → missing key → get_alert_tone returns None)
ALERT_TONES: dict[int, tuple] = {
    AudibleAlert.engage: ("ascending", 880, 1320, 0.3, 0.4),
    AudibleAlert.disengage: ("descending", 1320, 880, 0.3, 0.4),
    AudibleAlert.refuse: ("tone", 400, 0.25, 0.5),
    AudibleAlert.warningSoft: ("tone", 800, 0.3, 0.5),
    AudibleAlert.warningImmediate: ("tone", 1200, 0.5, 0.6),
    AudibleAlert.prompt: ("tone", 600, 0.15, 0.35),
    AudibleAlert.promptRepeat: ("double", 600, 0.12, 0.15, 0.35),
    AudibleAlert.promptDistracted: ("tone", 700, 0.2, 0.4),
}


def get_alert_tone(audible_alert: AudibleAlert) -> np.ndarray | None:
    """
    Generate audio for an AudibleAlert value.

    Returns int16 PCM audio array or None if no tone for this alert.
    """
    tone_def = ALERT_TONES.get(audible_alert)
    if tone_def is None or tone_def[0] is None:
        return None

    tone_type = tone_def[0]

    if tone_type == "tone":
        freq, duration, volume = tone_def[1], tone_def[2], tone_def[3]
        audio = generate_tone(freq, duration, volume)
    elif tone_type == "ascending":
        start_freq, end_freq, duration, volume = tone_def[1], tone_def[2], tone_def[3], tone_def[4]
        audio = generate_ascending_tone(start_freq, end_freq, duration, volume)
    elif tone_type == "descending":
        start_freq, end_freq, duration, volume = tone_def[1], tone_def[2], tone_def[3], tone_def[4]
        audio = generate_descending_tone(start_freq, end_freq, duration, volume)
    elif tone_type == "double":
        freq, duration, gap, volume = tone_def[1], tone_def[2], tone_def[3], tone_def[4]
        audio = generate_double_beep(freq, duration, gap, volume)
    else:
        return None

    # Convert to int16
    return (audio * 32767).astype(np.int16)


def main():
    """Test tone generation."""
    for alert_name in ['engage', 'disengage', 'warningSoft', 'warningImmediate', 'prompt', 'promptRepeat', 'refuse']:
        alert_val = getattr(AudibleAlert, alert_name)
        tone = get_alert_tone(alert_val)
        if tone is not None:
            print(f"{alert_name}: {len(tone)} samples, {len(tone)/SAMPLE_RATE:.3f}s")
        else:
            print(f"{alert_name}: None")


if __name__ == "__main__":
    main()
