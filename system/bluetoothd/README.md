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

Phone (ELM327 raw) ──► SPPD ──► obdCommand ──► obd2d ──► obdResponse ──► SPPD ──► phone
Phone (CMD_NAVIGATE) ───────► SPPD / GATTD ──► NavDestination param ──► navd
Phone (CMD_VEHICLE_DATA) ───► SPPD / GATTD ──► ncpVehicleData ──► adaptd
```

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
| `0x60–0x6F` | device → phone | Auth / subscription state |

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `EOPBluetoothEnabled` | bool | `true` | Master enable |
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
