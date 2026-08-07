# OpenPilot Bluetooth Daemon (bluetoothd)

Dual-transport Bluetooth daemon: Classic SPP (RFCOMM) for legacy OBD scanners + BLE GATT (Nordic UART) for NavPilot on iOS and Android.

> **Note:** HFP (Hands-Free Profile) has been removed. TTS uses the built-in I2S speaker.

## Transports

| Transport | Profile | Purpose | iOS |
|-----------|---------|---------|-----|
| **Classic SPP** | RFCOMM, channel 1 | Any ELM327-compatible OBD scanner | ❌ iOS blocks non-MFi |
| **BLE GATT** | Nordic UART Service | NavPilot companion app (NCP v4.1) | ✅ |

Both run simultaneously. NavPilot uses BLE GATT. Torque, Car Scanner, OBD2 Scan use Classic SPP.

## Architecture

```
BluetoothD (bluetoothd.py)
├── GLib main loop thread   — required for D-Bus callbacks (pairing + GATT)
├── PairingAgent            — BlueZ Agent1, DisplayOnly capability
├── SPPD (spp.py)           — RFCOMM server, dual NCP+ELM327 mux
│   └── Client              — per-connection buffer + frame dispatch
└── GATTD (ble_gatt.py)     — BlueZ GATT Application
    ├── NUSService          — 6E400001-B5A3-F393-E0A9-E50E24DCCA9E
    ├── RXChar              — phone writes NCP frames (6E400002…)
    └── TXChar              — device notifies NCP frames (6E400003…)
```

```
carState ───────────────────► SPPD + GATTD ──► TELEMETRY_VEHICLE (10 Hz)
navInstruction ─────────────► SPPD + GATTD ──► TELEMETRY_NAV
navRoute ───────────────────► SPPD + GATTD ──► TELEMETRY_ROUTE
blindSpotAlert + radar2d ───► SPPD + GATTD ──► BLIND_SPOT 0x0602 (see below)

Phone (ELM327 raw) ──► SPPD ──► obdCommand ──► obd2d ──► obdResponse ──► SPPD ──► phone
Phone (CMD_NAVIGATE) ───────► SPPD / GATTD ──► NavDestination param ──► navd
Phone (CMD_VEHICLE_DATA) ───► SPPD / GATTD ──► ncpVehicleData ──► adaptd
```

## Blind-spot feed (BLIND_SPOT 0x0602, 10 Hz)

Sources: cereal `blindSpotAlert` (fused BSD/RCTA/LCA decision from controlsd)
+ cereal `radar2d` (raw corner presence, published by `ble_central.py`).
Payload schema is the cross-repo contract with navpilot — see
`ncp_session.build_blindspot_payload`, do not rename fields.

Parked-degraded semantics: controlsd runs ignition_on only, so when parked
`valid=false` and the decision fields are nulled — but `radarPresence`
(frontLeft/frontRight/rearLeft/rearRight booleans) keeps streaming from
radar2d, which bluetoothd receives always_run (walk-around mode).
Single-publisher rule untouched: NCP telemetry is read-only on cereal;
`radar2d` remains solely owned by `ble_central.py`.

## Corner-radar BLE central (ble_central.py)

Connects to up to 4 ESP32-S3 corner radars (GATT notifications → `radar2d`
at 20 Hz). Admission mirrors the WiFi fleet's MAC-ACL semantics: paired units
(`BLECornerPairs`) always connect; learning NEW units requires
bootstrap/pairing-window (`BLERadarPairingOpen`) PLUS eligibility —
advertising dwell ≥ 10 s (presence stability) and the node's claimed WiFi
STA MAC (BLE mfg data, Espressif company id 0x02E5) present in
`/etc/hostapd/ap0.accept` (the identity check). RSSI is advisory-only
(weak-signal anomaly warning, never a gate — BLE RSSI is not distance).
Without that roster the identity check degrades to dwell-only (one startup
warning). Full contract in the module docstring.

### Radar pairing service tool (NCP)

NavPilot doubles as the commissioning UI for the corner radars:

- `RADAR_PAIR_CONTROL` `0x0610` (phone → device): `{"open": bool}` —
  persists `BLERadarPairingOpen`; ACK/error like other commands.
- `RADAR_PAIR_STATUS` `0x0611` (device → phone): `{windowOpen, pairs,
  candidates}` — ~1 Hz while the window is open, one frame on any
  windowOpen/pair-set transition, quiet otherwise. Candidates carry
  `address/wifiMac/corner/dwellS/eligible/reason` (`corner` null until the
  node connects and its datagrams identify it). Payload keys are the pinned
  cross-repo contract — see `ncp_session.build_radar_pair_status`.

## Pairing Flow (6-digit PIN, user must type)

```
1. Phone Bluetooth settings → finds "EXOPILOT 01" (or 02M)
2. Phone initiates pairing → BlueZ calls PairingAgent.DisplayPasskey(passkey)
   IO capability: device=DisplayOnly × phone=KeyboardDisplay → Passkey Entry
   → Agent stores passkey in BluetoothPairingPin param
   → ADAS UI reads param and shows 6-digit code on screen
3. Phone user types the 6 digits — phone confirms → OS bonding complete

4. User opens NavPilot → BLE scan finds device by NUS UUID (6E400001…)
   or selects from "My Devices" (already bonded)
5. Taps Connect → GATT connected → GATTD sends DEVICE_INFO
6. NCP v4.1 session live — telemetry streams, commands accepted
```

## Wire Protocol — NCP v4.1

Same frame format on both SPP and GATT:
```
[2B length] [2B type] [N bytes JSON payload] [2B CRC-CCITT]
length = 2(type) + N(payload) + 2(CRC)
CRC    = binascii.crc_hqx(payload, 0xFFFF)
```

SPP additionally carries raw ELM327 ASCII on the same socket (detected by first-byte pattern).

## Message Type Ranges

| Range | Direction | Purpose |
|-------|-----------|---------|
| `0x00–0x0F` | bidirectional | Device info / capabilities |
| `0x10–0x1F` | device → phone | Telemetry (vehicle, nav, ADAS, alerts, route, batch) |
| `0x20–0x2F` | phone → device | Commands (navigate, OBD, vehicle data, pair, auth, driving profile) |
| `0x30–0x3F` | device → phone | Responses (ACK, error, vehicle info, pair result) |
| `0x40–0x4F` | bidirectional | Control (PING/PONG) |
| `0x50–0x5F` | phone → device (fallback) | Search |
| `0x60–0x6F` | device → phone | Auth / subscription state |
| `0x70–0x7F` | phone → device (0x70/0x71), device → phone (0x72) | Convoy (follow-a-friend) — capability-gated via `convoyFollow` |
| `0x0602` | device → phone | BlindSpot — BSD/LCA/RCTA decision + corner-radar presence |
| `0x0610/0x0611` | bidirectional | Radar pairing service tool (window control / live status) |

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `EOPBluetoothEnabled` | bool | `true` | Master enable |
| `EOPBluetoothRadarEnabled` | bool | `false` | BLE central for ESP32 corner radars (`ble_central.py`, sole `radar2d` publisher when on) |
| `BLECornerPairs` | str (JSON) | — | Learned BLE MAC → corner_id pair set (`ble_central.py`), written automatically |
| `BLERadarPairingOpen` | bool | `false` | Corner-radar pairing window — while `1`, unknown units may be learned/connected; close after pairing (mirrors WiFi MAC-ACL ritual) |
| `EOPDeviceName` | str | `EXOPILOT` | BT adapter name — set per unit: `EXOPILOT 01`, `EXOPILOT 02M`, `EXOPILOT 02M` |
| `EOPSPPEnabled` | bool | `false` | Classic SPP sub-daemon enable |
| `EOPSPPAutoReconnect` | bool | `true` | Outward-connect to saved mobile device |
| `EOPSPPPairedDevice` | str | — | Paired mobile device MAC |
| `BluetoothPairingPin` | str | — | 6-digit pairing code — set by agent, read by ADAS UI |
| `BluetoothPairingAddr` | str | — | MAC of device being paired |
| `BluetoothPairingActive` | str | `0` | `1` while pairing dialog is active |

## Files

| File | Purpose |
|------|---------|
| `bluetoothd.py` | Entry point — GLib loop, adapter name, SPP + GATT startup, pairing agent |
| `spp.py` | Classic SPP — RFCOMM server, dual ELM327+NCP mux, telemetry loop |
| `ble_gatt.py` | BLE GATT — Nordic UART Service via BlueZ D-Bus API, telemetry loop |
| `ble_central.py` | BLE central — ESP32 corner radar GATT client, decodes object datagrams, publishes `radar2d` |
| `protocol.py` | NCP v4.1 frame codec — source of truth for wire format |
| `pairing_agent.py` | BlueZ Agent1 — `DisplayOnly`, `DisplayPasskey` handler, PIN param store |
| `device.py` | Bluetooth device classifier (MOBILE / UNKNOWN) |

## Reconnection

| Transport | Mode | Backoff |
|-----------|------|---------|
| SPP outward | 10 attempts | 3 s → 60 s exponential |
| SPP server | Always accepting | — |
| GATT | Phone reconnects | OS-managed |

## Quick Start

```bash
params put_bool EOPBluetoothEnabled true
params put_bool EOPSPPEnabled true
params put EOPDeviceName "EXOPILOT 01"   # set per unit at flash time

python3 system/bluetoothd/bluetoothd.py
```

## Tests

```bash
pytest system/bluetoothd/tests/
```

## Cross-project protocol

`protocol.py` is the single source of truth. Any `MessageType` or frame format change must also be applied to NavPilot's `navpilot/src/lib/src/protocol/frame_protocol.dart`.
