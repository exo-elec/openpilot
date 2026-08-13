# Voice Pipeline Architecture

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Implementation** | ✅ Complete |

---

## Overview

> **Applicability:** Full voice interaction requires ExoPilot 02M-class hardware
> (mic array). openpilot supports ExoPilot 01M (RK3588) only, where voice
> hardware detection permanently resolves false and the voice daemons stay in
> silent standby — see Hardware Requirements below.

The Voice Pipeline provides hands-free voice interaction for EOP, enabling
drivers to control navigation, adjust settings, and query information using
natural language commands.

**Architecture:** Cloud-assisted, minimal local footprint.
- Local: microphone capture (`micd`), alert-tone playback (`soundd`).
- Cloud: wake-word detection, STT, intent resolution, and language TTS are
  handled by the Azure voice server. EOP sends compressed audio upstream and
  receives intents / pre-rendered voice audio downstream.

**Safety-critical design:** EOP does **not** run local STT/TTS models for
language commands. Deterministic, auditable command handling remains the goal;
the heavy perception models live in the cloud where they can be updated and
monitored centrally. Only non-language alert tones are synthesized locally.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           VOICE PIPELINE                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────┐    ┌─────────────────────┐    ┌──────────┐                  │
│  │  micd    │───▶│  Azure Voice Server │───▶│  soundd  │                  │
│  │(capture +│    │ (STT/TTS/intent)    │    │(tones +  │                  │
│  │ compress)│    │                     │    │ play)    │                  │
│  └──────────┘    └─────────────────────┘    └──────────┘                  │
│       ▲                                              │                       │
│       └────────────── intent / UI cmd ───────────────┘                       │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Components

### 1. micd (Microphone Daemon)

**Location:** `system/micd/micd.py`

- **Purpose:** Captures audio from microphone array, compresses it, and streams
  it to the Azure voice server.
- **Features:**
  - 2× INMP441 microphones (160-200mm spacing)
  - Beamforming (180° coverage)
  - AEC (Acoustic Echo Cancellation)
  - Opus/PCM compression for upstream transmission
- **Output:** Compressed audio to Azure; local `MicrophoneState` for UI.

### 2. Azure Voice Server

- **Purpose:** Wake-word detection, Whisper-based STT, deterministic intent
  resolution, and language TTS.
- **Input:** Compressed audio stream from `micd`.
- **Output:**
  - `voiceIntent` (action, params, reply text)
  - Pre-rendered voice audio for `soundd`
  - Optional `uiCommand` for UI control

### 3. soundd (Audio Output)

**Location:** `selfdrive/soundd/soundd.py`

- **Purpose:** Plays **local alert tones only**. Language/voice audio is played
  from the Azure server's downstream stream; EOP does **not** run local Piper
  TTS or Whisper STT.
- **Outputs:**
  - Local alert tones (engagement, warnings, etc.)
  - Azure voice audio passthrough
  - Quiet-mode amplitude scaling (see `QuietMode` param)

---

## Message Flow

```
1. User says "Hi EXO, navigate to nearest gas station"
           │
           ▼
2. micd captures + compresses audio ──▶ Azure voice server
           │
           ▼
3. Azure: wake word + Whisper STT + deterministic intent
           │
           ▼
4. Intent: NAVIGATE, POI: gas_station ──▶ intent handler
           │
           ▼
5. Azure TTS: "Navigating to gas station" ──▶ soundd playback
```

---

## Supported Commands

| Category | Example Commands |
|----------|------------------|
| **Navigation** | "Navigate to [destination]", "Take me home", "Cancel navigation" |
| **Settings** | "Open settings", "Show navigation card" |
| **Vehicle** | "Turn on autopilot", "Set temperature to 22" |
| **Media** | "Play music", "Volume up", "Next song" |

---

## Hardware Requirements

- **Platform:** RK3588 (ExoPilot 01M) — the only platform openpilot supports
- **Microphones:** 2× INMP441 (I2S1) — ExoPilot 02M only (VisionPilot); not present on 01M
- **Speaker:** MAX98357A 3.2W amplifier — ExoPilot 02M only (VisionPilot); not present on 01M
- **Network:** Active cellular/Wi-Fi link required for Azure voice server.

**Note:** ExoPilot 01M (RK3588) has no microphone/speaker hardware. Voice
daemons run but stay silent; alert tones are not available on 01M.

---

## Parameters

| Key | Default | Purpose |
|-----|---------|---------|
| `EOPVoiceEnabled` | 0 | Enable voice pipeline |
| `EOPWakeWordSensitivity` | 0.7 | Wake word detection threshold (server-side) |
| `EOPVoiceLanguage` | "en" | STT/TTS language |
| `QuietMode` | 0 | Reduce local alert-tone volume |

---

## Cereal Messages

- `MicrophoneState` - Audio levels, beamforming status
- `voiceState` - Voice assistant state (idle/listening/processing)
- `voiceIntent` - Resolved intent with action, params, reply
- `ttsRequest` - TTS playback request (from control paths, not from local STT)
- `ttsStatus` - TTS playback status
- `bargeIn` - User interruption event
- `uiCommand` - UI control commands

---

## Safety Design

**No local language models:** EOP does not store Whisper or Piper weights. All
language STT/TTS runs in the Azure voice service. This removes a large attack
surface, keeps the on-device image small, and allows centralized model updates.

**Deterministic command handling:** The cloud intent resolver uses
dictionary/regex matching for safety-critical commands. No probabilistic LLM in
the ADAS command path.

**Why no local STT/TTS:**
- Smaller on-device image and faster OTA updates
- No NPU/GPU contention with driving models
- Centralized model updates and monitoring
- Consistent behavior across the fleet

---

**Last Updated:** 2026-08-14
