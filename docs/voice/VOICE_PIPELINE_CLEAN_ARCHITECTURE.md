# OpenPilot Voice Pipeline - Architecture

**Date:** 2026-05-23
**Status:** Partially Implemented — adaptive loudness pending

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    Audio Stack                                   │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────┐  soundPressure  ┌──────────────────────┐             │
│  │ micd │────────────────→│       soundd          │──→ spkd    │
│  │(mic) │  (ambient dB)   │ (TTS + alerts +      │            │
│  └──────┘                 │  adaptive loudness)   │            │
│                           └──────────────────────┘             │
│                                  ↑                              │
│  NavPilot (phone)  ──BLE──→  subscribed ──ttsRequest──┘        │
│  (wake+STT+intent on phone)  (dispatch)                         │
└──────────────────────────────────────────────────────────────────┘
```

Device microphone is **only** used for ambient loudness measurement.
All voice recognition (wake word, STT, intent) runs on the NavPilot phone app.

---

## Daemons

### micd — Microphone Capture
**Module:** `system.micd.micd` (always running)

Reads I2S audio from INMP441 hardware microphone (RK3576 only; standby on RK3588).

**Publishes:**
- `soundPressure` — dB SPL A-weighted at 10 Hz (consumed by soundd for loudness)

**Not used for:** STT, wake word, barge-in, intent detection.

---

### soundd — TTS + Alert Tones + Adaptive Loudness
**Module:** `selfdrive.soundd.soundd` (onroad only)

Synthesises speech via Piper TTS, plays openpilot alert tones, and adjusts output
volume to ambient noise level.

**Subscribes:**
- `ttsRequest` — text → Piper synthesis → PCM → spkd
- `selfdriveState` — AudibleAlert enum (tones, 500 ms cooldown)
- `soundPressure` — ambient dB SPL for adaptive loudness *(not yet implemented)*

**Hot-reloads** Piper voice model when `EOPLanguage` / `EOPTTSVoice` params change.

**Adaptive loudness (planned):** scale Piper TTS output gain proportional to
`soundPressure` so TTS is always audible without manual volume adjustment.

---

### spkd — Speaker Driver
**Module:** `system.spkd.spkd` (always running)

Drives hardware speaker output (I2S/amp). Receives PCM from soundd.

---

### subscribed — BLE / Command Dispatch
**Module:** `system.subscribed.subscribed` (always running)

Handles subscription auth, hardware fingerprinting, JSON-RPC over BLE.
Dispatches NavPilot voice commands (navigate, vehicleCommand, uiCommand, etc.)
to the appropriate on-device daemons.

Voice command bridge (`voiced → NavPilot`) is **not yet implemented**.

---

## Cereal Messages

| Message | Publisher | Subscribers | Status |
|---------|-----------|-------------|--------|
| `soundPressure` | micd | soundd | Running — loudness consumer pending |
| `ttsRequest` | navd, subscribed | soundd | Running |
| `ttsStatus` | soundd | *(none)* | Running |

---

## Hardware Requirements

| Component | ExoPilot 01M (RK3588) | ExoPilot 02M (RK3576) |
|-----------|---------------------|---------------------|
| Microphone (INMP441) | ❌ No | ✅ Yes (loudness only) |
| micd | Standby | ✅ soundPressure |
| soundd (TTS) | ✅ CPU (Piper) | ✅ CPU (Piper) |
| spkd | ✅ | ✅ |
| Wake / STT / Intent | NavPilot (phone) | NavPilot (phone) |
