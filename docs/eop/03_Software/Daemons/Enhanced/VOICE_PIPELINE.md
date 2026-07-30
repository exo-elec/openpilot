# Voice Pipeline Architecture

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Implementation** | ✅ Complete |

---

## Overview

> **Applicability:** Full voice interaction (wake word + STT + intent) requires
> ExoPilot 02M-class hardware (mic array + Hailo-8), which is VisionPilot's
> platform. openpilot supports ExoPilot 01M (RK3588) only, where voice hardware
> detection permanently resolves false and the voice daemons stay in silent
> standby — see Hardware Requirements below.

The Voice Pipeline provides hands-free voice interaction for EOP, enabling drivers to control navigation, adjust settings, and query information using natural language commands.

**Architecture:** 1-tier deterministic pipeline. No cloud AI. No LLM.
- Tier 1: Dictionary/regex matching (CPU, deterministic, <1ms)

**Safety-critical design:** EOP uses deterministic pattern matching ONLY.
No probabilistic LLM inference in ADAS voice command paths.
Predictable, testable, auditable behavior for every command.

**Hardware note:** Hailo-8 supports Whisper STT (HEF models) but NOT LLMs (no external DRAM).

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           VOICE PIPELINE                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐                              │
│  │  waked   │───▶│  voiced  │───▶│ intentd  │                              │
│  │(Wake Word│    │ (STT/    │    │(Intent    │                              │
│  │ Detection│    │ Pipeline)│    │ Processing│                             │
│  └──────────┘    └──────────┘    └──────────┘                              │
│       │               │               │                                      │
│       ▼               ▼               ▼                                      │
│  ┌──────────────────────────────────────────────────────────┐              │
│  │                    system/micd                            │              │
│  │         (Microphone capture + beamforming)                │              │
│  └──────────────────────────────────────────────────────────┘              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Components

### 1. waked (Wake Word Detection)

**Location:** `selfdrive/waked/waked.py` *(not implemented)*

- **Purpose:** Detects wake word using openWakeWord on CPU
- **Input:** Audio stream from micd
- **Output:** `VoiceWake` event to voiced
- **Hardware:** CPU (A55 cores)

### 2. voiced (Speech-to-Text Pipeline)

**Location:** `selfdrive/voiced/voiced.py` *(not implemented)*

- **Purpose:** Converts speech to text using Whisper on Hailo-8
- **Components:**
  - `hailo_whisper.py` - Whisper HEF model on Hailo-8
  - `stt_engine.py` - STT processing
  - `mic_beamformer.py` - Audio beamforming
  - `intent_pipeline.py` - 1-tier deterministic intent resolution
- **Input:** Audio from micd, wake events from waked
- **Output:** `voiceIntent` to intentd

### 3. intentd (Intent Processing)

**Location:** `selfdrive/intentd/intentd.py` *(not implemented)*

- **Purpose:** Receives resolved intents from voiced, executes commands
- **Components:**
  - `handlers/` - Intent handlers for different command types
  - `pipeline.py` - 1-tier deterministic pipeline
  - `poi_cache.py` - Point-of-interest caching
- **Input:** `voiceIntent` from voiced
- **Output:** TTS requests, UI commands, vehicle commands

### 4. micd (Microphone Daemon)

**Location:** `system/micd/micd.py`

- **Purpose:** Captures audio from microphone array
- **Features:**
  - 2× INMP441 microphones (160-200mm spacing)
  - Beamforming (180° coverage)
  - AEC (Acoustic Echo Cancellation)
- **Output:** Raw audio to voice pipeline

---

## Message Flow

```
1. User says "Hi EXO, navigate to nearest gas station"
           │
           ▼
2. waked detects wake word ──▶ wakes voiced
           │
           ▼
3. voiced captures audio ──▶ Whisper STT on Hailo-8
           │
           ▼
4. "navigate to nearest gas station" ──▶ Tier 1 dictionary match
           │
           ▼
5. Intent: NAVIGATE, POI: gas_station ──▶ intentd
           │
           ▼
6. intentd executes ──▶ TTS: "Navigating to gas station"
           │
           ▼
7. Response played via soundd
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
- **Hailo-8:** Required for Whisper STT (vision/audio HEF models)
- **Microphones:** 2× INMP441 (I2S1) — ExoPilot 02M only (VisionPilot); not present on 01M
- **Speaker:** MAX98357A 3.2W amplifier — ExoPilot 02M only (VisionPilot); not present on 01M

**Note:** ExoPilot 01M (RK3588) has no microphone/Hailo hardware. Voice daemons run but stay silent.

---

## Parameters

| Key | Default | Purpose |
|-----|---------|---------|
| `EOPVoiceEnabled` | 0 | Enable voice pipeline |
| `EOPWakeWordSensitivity` | 0.7 | Wake word detection threshold |
| `EOPVoiceLanguage` | "en" | STT/TTS language |

---

## Cereal Messages

- `MicrophoneState` - Audio levels, beamforming status
- `voiceState` - Voice assistant state (idle/listening/processing)
- `voiceIntent` - Resolved intent with action, params, reply
- `ttsRequest` - TTS playback request
- `ttsStatus` - TTS playback status
- `bargeIn` - User interruption event
- `uiCommand` - UI control commands

---

## Safety Design

**Deterministic-only:** EOP uses dictionary/regex matching for ALL voice commands.
No probabilistic LLM, no neural network inference in command paths.
Every command maps to a predictable, testable action.

**Why no LLM:**
- Deterministic behavior is auditable and certifiable
- No model weights to store (~300MB+ saved)
- No inference latency variability (<1ms vs 50-200ms)
- No risk of hallucinated commands in safety-critical context

---

**Last Updated:** 2026-04-20
