# LubanCat RK3588 / RK3588 Hardware Pin Interfaces

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |

---


This document serves as the authoritative mapping for hardware-level software interactions on the RK3588 platform (ExoPilot 01M) for the EOP project. The RK3588 is pin-compatible with RK3588.

## 1. Camera MIPI Interfaces (CSI)
The system uses 4 CSI lanes via the Rockchip ISP (RKISP1).

Camera bar layout (front view, Y offset from vehicle centerline):

```
  wide_road (+40 mm)  —  road (−40 mm)
  stereo_right (+40 mm) — stereo_left (−40 mm)
```

| Camera | Sensor | Lens | Bus | Device Node | Y Offset | Notes |
|--------|--------|------|-----|-------------|----------|-------|
| **Road** | OX03C10 | 8mm | CSI0 | `/dev/video0` | **−40 mm** | Main vision (right of center) |
| **Wide road** | OX03C10 | 1.7mm | CSI0 | `/dev/video1` | **+40 mm** | Peripheral vision (left of center) |
| **Stereo Left** | GC4653 | **3.6mm** | DPHY0 | `/dev/video22` | **−40 mm** | Master, shares road position |
| **Stereo Right** | GC4653 | **3.6mm** | DPHY1 | `/dev/video31` | **+40 mm** | Slave, shares wide_road position |

**Stereo baseline:** 80 mm

**BGT60TR13C radar:** mounted at Y=0 (vehicle centerline), between
`stereo_left` (−40 mm) and `stereo_right` (+40 mm) on the camera bar,
boresight forward. See §3.1 for SPI/GPIO wiring.

## 2. Serial Interfaces (UART)
High-speed serial for GNSS and RTK communication.

| Interface | Device | Speed | Purpose |
|-----------|--------|-------|---------|
| **UART7** | `/dev/ttyS7` | 115200 | U-blox NEO-M8U (GPS/UDR) |
| **UART2** | `/dev/ttyS2` | 115200 | RTK Correction (Optional) |

## 2.1 EC25 4G Modem (USB-over-Mini-PCIe / M.2)

RK3588 (LubanCat-5) uses EC25 via USB signals on the Mini PCIe / M.2 connector.

| Signal | Connection | Notes |
|--------|-----------|-------|
| USB 2.0 DP/DM | Mini PCIe USB pins | Data to RK3588 USB Host |
| 3.3V / 3.8V | PCIe power rail | Modem VBAT |
| RESET# | GPIO (optional) | Active low reset |
| SIM | External SIM holder | 1.8V/3.0V USIM |

**Software interface:**
- `usb0` — CDC-ECM / RNDIS network interface
- `/dev/ttyUSB2` — AT command port
- Driver: `cdc_ether` or `rndis_host` + `usbserial`

## 3. I2C Bus Mapping
| Bus | Address | Sensor | Function |
|-----|---------|--------|----------|
| **I2C0** | `0x6A` | LSM6DS3 | IMU (Accel/Gyro) |
| **I2C0** | `0x30` | MMC5603NJ | Magnetometer |
| **I2C0** | `0x51` | BM8563 | RTC (Time Sync) |
| **I2C3** | (Config) | OX03C10 | Road Camera Controller |
| **I2C4** | (Config) | OX03C10 | Wide Road Camera Controller |

## 3.1 SPI Bus Mapping

| Bus | Device Node | Sensor | Notes |
|-----|-------------|--------|-------|
| **SPI0** | `/dev/spidev0.0` | BGT60TR13C (radar4d) | Freed from the MCP2518FD SPI-CAN transceiver — RK3588 has built-in CANFD, so SPI-CAN was removed (§5). TXS0108E level shifter required: BGT60TR13C's digital I/O is 1.8V, RK3588 GPIO is 3.3V. |

Machine-readable source: `hal.platform.rk3588_pins.SPI` (in the `exopilot`
repo's `hal` package) — this table should be kept in sync with it, not
treated as the sole source of truth.

## 4. Critical GPIO Assignments
Follows the LubanCat-5 physical mapping.

| Pin Name | RK3588 GPIO | Physical Pin | Logic |
|----------|-------------|--------------|-------|
| `IMU_INT` | `GPIO0_D3` | Pin 27 | IRQ (Rising Edge) |
| `GNSS_PWR_EN` | `GPIO1_C4` | Pin 52 | High = On |
| `UBLOX_RST_N` | `GPIO0_D6` | Pin 30 | Low = Reset |
| `UBLOX_SAFEBOOT_N` | `GPIO0_D5` | Pin 29 | High = Normal |
| `CAM0_PWR_EN` | `GPIO3_B2` | Pin 106 | High = On |
| `CAM0_RST_N` | `GPIO3_B3` | Pin 107 | Low = Reset |
| `BGT60_IRQ` ⚠️ | `GPIO3_B0` | Pin 104 | IRQ (Rising Edge) — **UNCONFIRMED**, DTS overlay placeholder pending LubanCat5 schematic |
| `BGT60_RST` ⚠️ | `GPIO3_B1` | Pin 105 | Low = Reset (DIO3) — **UNCONFIRMED**, DTS overlay placeholder pending LubanCat5 schematic |

## 5. CAN Controller Interfaces
Managed via the `SocketD` bridge with **USB-C SBU orientation detection**.

The RK3588 has two built-in CAN controllers exposed as SocketCAN interfaces (`can0`, `can1`).
Because the debug connector is USB-C, the SBU (Sideband Use) pins swap when the connector is
flipped. The system detects orientation in real-time and dynamically remaps logical buses to
physical interfaces so that `canmpc` (main processor camera / Bosch ADAS bus) and `canpwrt`
(powertrain bus) are always correct regardless of insertion orientation.

### Dynamic Bus Mapping

| Orientation | Logical Bus 0 (`canmpc`) | Logical Bus 1 (`canpwrt`) |
|-------------|-------------------------|--------------------------|
| **NORMAL**  | `can0`                  | `can1`                   |
| **FLIPPED** | `can1`                  | `can0`                   |

**Detection:** SBU1/SBU2 sampled via ADC at 8 Hz. Threshold = VDD/2 (typically 1650 mV).
`SBU1 < threshold AND SBU2 > threshold` → NORMAL; swapped → FLIPPED.

**Implementation:** `common/sbu_detection.py` provides `SBUDetector` + `SemanticCANMapper`
for host-side detection. `socketd` consumes the resolved mapping and opens the correct
physical SocketCAN interface.

> **Rule:** Application code never references `can0`/`can1` directly. Always use semantic
> names (`canmpc`, `canpwrt`) or logical bus numbers (0, 1).

## 6. WiFi/BLE (RTL8821CE PCIe)

**Module:** Realtek RTL8821CE
**Interface:** PCIe 1.1 x1
**Driver:** `rtw88_8821ce` (WiFi) + `btrtl` (BT)

| Function | GPIO | Notes |
|----------|------|-------|
| PCIe Reset | GPIO3_D1 | Active high |
| WLAN_EN | GPIO0_C4 | Power enable |
| BT_EN | GPIO0_C5 | BT power enable |

**Note:** RK3576 uses AP6275P (SDIO) instead. See WIFI_BLE_HARDWARE.md for details.

## 7. Tracking Status

| Requirement | Status | Note |
|-------------|--------|------|
| CSI Mappings | ✅ Done | Matches `hardware.py` camera defaults. |
| UART7 GPS | ✅ Done | Verified with `pigeond` driver. |
| I2C Bus Config | ✅ Done | Bus 6/0 for sensors verified. |
| GPIO Mappings | ✅ Done | Matches `LSM6DS3` and `GNSS` drivers. |
| CAN Nodes | ✅ Done | `can0`, `can1` active in `socketd`; SBU-aware dynamic remap via `common/sbu_detection.py`. |
| PWM Fan | ✅ Done | PWM0_CH0 active in `hardwared`. |
| SPI0 Radar (BGT60TR13C) | ⚠️ Partial | Bus assignment confirmed (freed from MCP2518FD); `BGT60_IRQ`/`BGT60_RST` GPIO pin numbers still unconfirmed placeholders — see docs/eop/bgt60_radar.md. |
