# EOP BLE / Bluetooth Integration Design

**Last updated:** 2026-05-30  
**Protocol:** NCP v4.1  
**Transport:** Classic SPP (RFCOMM) + BLE GATT (Nordic UART Service)

---

## Status

| Aspect | Status |
|--------|--------|
| Classic SPP (RFCOMM) | ✅ Complete — legacy OBD scanners, NavPilot fallback |
| BLE GATT (Nordic UART) | ✅ Complete — NavPilot iOS + Android |
| Pairing agent (6-digit PIN) | ✅ Complete — `DisplayOnly`, user must type PIN |
| Adaptive driving (adaptd) | ✅ Complete — consumes `ncpVehicleData` from NavPilot |

---

## 1. Overview

The device exposes two Bluetooth transports simultaneously:

| Transport | UUID / Channel | Clients | iOS |
|-----------|---------------|---------|-----|
| Classic SPP | RFCOMM ch 1, UUID `1101` | Torque, OBD2 Scan, Car Scanner | ❌ non-MFi blocked |
| BLE GATT (NUS) | `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` | NavPilot | ✅ |

NavPilot (`../navpilot`) uses BLE GATT exclusively. Standard OBD scanners use Classic SPP.

---

## 2. Architecture

### 2.1 System Context

```
┌─────────────────────────────────────────────────────────────────────┐
│                        NavPilot (Phone)                             │
│                  flutter_blue_plus / BLE GATT                       │
├─────────────────────────────────────────────────────────────────────┤
│  Sends  → CMD_NAVIGATE, CMD_VEHICLE_DATA, CMD_PAIR, CMD_AUTH…       │
│  Recvs  ← TELEMETRY_VEHICLE, TELEMETRY_NAV, TELEMETRY_ROUTE…       │
└────────────────────────┬────────────────────────────────────────────┘
                    BLE GATT (NUS)              Classic SPP (RFCOMM)
                    iOS + Android               Android OBD scanners
                         │                             │
┌────────────────────────▼─────────────────────────────▼─────────────┐
│                   bluetoothd (device)                               │
│  ┌──────────────────────────┐  ┌──────────────────────────────────┐ │
│  │  GATTD (ble_gatt.py)     │  │  SPPD (spp.py)                   │ │
│  │  Nordic UART Service     │  │  RFCOMM + dual ELM327/NCP mux    │ │
│  └────────────┬─────────────┘  └──────────────┬───────────────────┘ │
│               │ ncpVehicleData                 │ obdCommand/Response │
└───────────────┼────────────────────────────────┼─────────────────────┘
                │                                │
     ┌──────────▼──────────┐          ┌──────────▼──────────┐
     │  adaptd             │          │  obd2d              │
     │  adaptive driving   │          │  OBD2/UDS → CAN bus │
     └─────────────────────┘          └─────────────────────┘
```

### 2.2 NCP v4.1 Data Flow — Clean Separation

```
obd2d ──(raw OBD bytes, no interpretation)──► SPP/GATT ──► NavPilot
NavPilot ──(interprets Mode 22 PIDs, subscription value)──► CMD_VEHICLE_DATA
bluetoothd ──(ncpVehicleData cereal)──► adaptd ──(adaptiveDrivingState)──► controlsd
```

**Key principle:** obd2d does zero proprietary-PID interpretation. All Mode 22 decoding (BYD, MG, GAC, etc.) lives in NavPilot (subscription value). adaptd consumes the already-decoded data.

---

## 3. Pairing Flow

```
First time (OS-level bonding):
1. User opens phone Bluetooth settings
2. Finds "EXOPILOT 01" (or EXOPILOT 02M)
3. Initiates pair → BlueZ calls PairingAgent.DisplayPasskey(passkey)
   IO caps: device=DisplayOnly × phone=KeyboardDisplay → Passkey Entry
   ADAS screen shows 6-digit code from BluetoothPairingPin param
4. User types the 6 digits on phone → OS bonding complete

In-app connection:
5. Open NavPilot → BLE scan (withServices: NUS UUID)
   OR tap "My Devices" (already bonded list)
6. Select "EXOPILOT 01" → tap Connect
7. flutter_blue_plus connects GATT → discovers NUS characteristics
8. NavPilot writes DEVICE_CAPABILITIES to RX char (6E400002)
9. GATTD responds with DEVICE_INFO on TX char (6E400003) via notify
10. Telemetry begins at 10 Hz; navigation commands accepted
```

---

## 4. NCP v4.1 Wire Format

```
[2B length] [2B type] [N bytes JSON UTF-8] [2B CRC-CCITT]
length = 2(type) + N(payload) + 2(CRC)
CRC = binascii.crc_hqx(payload, 0xFFFF)
```

SPP carries this framed format plus raw ELM327 ASCII on the same socket (auto-detected by first byte: `A`, `0`–`9` → ELM327; binary → NCP frame).

### Key Message Types

| Code | Name | Direction | Description |
|------|------|-----------|-------------|
| `0x00` | `DEVICE_CAPABILITIES` | P→D | Request device capabilities |
| `0x01` | `DEVICE_INFO` | D→P | Device info + supported services |
| `0x10` | `TELEMETRY_VEHICLE` | D→P | Speed, steering, gear, OBD data @ 10 Hz |
| `0x11` | `TELEMETRY_NAV` | D→P | Navigation maneuver instruction |
| `0x15` | `TELEMETRY_ROUTE` | D→P | Route geometry (on new route) |
| `0x20` | `CMD_NAVIGATE` | P→D | Start navigation to lat/lon |
| `0x21` | `CMD_CANCEL_NAV` | P→D | Cancel navigation |
| `0x22` | `CMD_DRIVING_PROFILE` | P→D | Set personality (aggressive/standard/relaxed/traffic) |
| `0x24` | `CMD_GET_VEHICLE_INFO` | P→D | Request VIN + vehicle type |
| `0x2B` | `CMD_PAIR` | P→D | NCP-level pairing code entry |
| `0x2D` | `CMD_OBD_REQUEST` | P→D | Raw OBD PID request |
| `0x2E` | `CMD_VEHICLE_DATA` | P→D | Interpreted vehicle telemetry from NavPilot |
| `0x2F` | `CMD_OAUTH_TOKEN` | P→D | Google OAuth token for on-device Gemini |
| `0x30` | `RESPONSE_ACK` | D→P | Command acknowledged |
| `0x34` | `RESPONSE_VEHICLE_INFO` | D→P | VIN, type, make |
| `0x36` | `RESPONSE_PAIR` | D→P | Pair accept/reject |
| `0x40` | `PING` | P→D | Keepalive |
| `0x41` | `PONG` | D→P | Keepalive response |
| `0x60` | `AUTH_DEVICE_ID` | D→P | Hardware device ID |
| `0x61` | `AUTH_SUBSCRIPTION_STATE` | D→P | Subscription tier |

---

## 5. BLE GATT Profile

**Nordic UART Service (NUS)**

| UUID | Name | Properties | Role |
|------|------|-----------|------|
| `6E400001-…` | NUS Service | — | Service container |
| `6E400002-…` | RX Characteristic | Write, Write-Without-Response | Phone → Device (NCP frames) |
| `6E400003-…` | TX Characteristic | Notify | Device → Phone (NCP frames) |

**MTU:** BlueZ defaults to 512 bytes; NCP frames up to 500 bytes per BLE notification chunk (GATTD chunks automatically). NavPilot (`BleTransportService`) buffers incoming notify values and reassembles via `_rxBuffer`.

---

## 6. Device Naming

Device name is set from the `EOPDeviceName` param at startup:

| Hardware | Param value |
|----------|-------------|
| ExoPilot 01M (RK3588) | `EXOPILOT 01` |

Set at flash time: `params put EOPDeviceName "EXOPILOT 01"`

---

## 7. Implementation Files

**Device (OpenPilot):**

| File | Purpose |
|------|---------|
| `system/bluetoothd/bluetoothd.py` | Entry point, GLib loop, adapter name |
| `system/bluetoothd/ble_gatt.py` | BLE GATT server (Nordic UART Service) |
| `system/bluetoothd/spp.py` | Classic SPP server + NCP+ELM327 mux |
| `system/bluetoothd/protocol.py` | NCP frame codec (source of truth) |
| `system/bluetoothd/pairing_agent.py` | BlueZ Agent1, DisplayOnly, DisplayPasskey |
| `selfdrive/adaptd/adaptd.py` | Adaptive driving daemon (consumes ncpVehicleData) |

**NavPilot (Flutter):**

| File | Purpose |
|------|---------|
| `src/lib/src/protocol/frame_protocol.dart` | NCP frame codec (matches protocol.py) |
| `src/lib/src/protocol/vehicle_protocol.dart` | VehicleData, Mode 22 PID tables |
| `src/lib/src/protocol/client.dart` | NCP client, BLE rx/tx wiring |
| `src/lib/src/services/ble_transport_service.dart` | flutter_blue_plus BLE GATT transport |
| `src/lib/src/features/settings/widgets/ble_scan_sheet.dart` | Device picker UI |
| `src/lib/src/services/paired_device_service.dart` | Trusted device persistence |

---

## 8. Security

| Layer | Mechanism |
|-------|-----------|
| OS pairing | 6-digit Passkey Entry — user must type code shown on ADAS screen |
| GATT access | Requires OS-level bonding before GATT operations (BlueZ enforces) |
| NCP CMD_PAIR | Secondary NCP-level code validation (same 6-digit code) |
| OAuth token | Relayed from NavPilot to device for Gemini API; stored in non-logged param |

---

## 9. References

- `system/bluetoothd/README.md` — daemon-level detail
- `system/bluetoothd/protocol.py` — NCP wire format source of truth
- `../navpilot/src/lib/src/protocol/frame_protocol.dart` — Dart counterpart
- BlueZ GATT API: `https://git.kernel.org/pub/scm/bluetooth/bluez.git/tree/doc/gatt-api.txt`
- BlueZ Agent API: `https://git.kernel.org/pub/scm/bluetooth/bluez.git/tree/doc/agent-api.txt`
