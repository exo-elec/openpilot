# Code Review — Commit `93374f4c` [SYSTEM]

**Commit:** `93374f4c75cace3b2bdee7c6ac9e065c5703f901`  
**Subject:** `[SYSTEM] Rework daemons for RK3576/RK3588 edge platform`  
**Reviewed:** 2026-05-27  
**Files changed:** 230 · **Hunks:** 260  
**Method:** 3-angle review (line scan / removed-behavior / cross-file) + verification

---

## Bugs Found and Fixed

All 8 bugs below were **fixed in the same session** via follow-up edits.

---

### Bug 1 — CRITICAL: `pandaStates` not published → ADAS permanently blocked

| | |
|---|---|
| **File** | `selfdrive/selfdrived/selfdrived.py:90` |
| **Root cause** | `pandad` was removed from `process_config.py` with no replacement publisher of `pandaStates`. `selfdrived` subscribes to `pandaStates` and it is NOT in `ignore_alive` / `ignore_valid`. |
| **Failure** | Every control cycle: `sm.valid['pandaStates'] == False` → `EventName.usbError` (NO_ENTRY + SOFT_DISABLE). ADAS can never be engaged. |
| **Fix** | Added `'pandaStates'` to `ignore_alive`, `ignore_avg_freq`, and `ignore_valid` in `selfdrived.__init__`. |

---

### Bug 2 — CRITICAL: `peripheralState` not published → commIssue every cycle

| | |
|---|---|
| **File** | `selfdrive/selfdrived/selfdrived.py:90` |
| **Root cause** | `peripheralState` was published only by `pandad.cc` (removed). Not in ignore lists. |
| **Failure** | Stale `peripheralState` → `EventName.commIssue` → SOFT_DISABLE every cycle. Fan-malfunction guard silently uses zero values. |
| **Fix** | Added `'peripheralState'` to the same ignore lists alongside `pandaStates`. |

---

### Bug 3 — HIGH: `CMD_OBD_REQUEST` not in `MessageType` enum → AttributeError at import

| | |
|---|---|
| **File** | `system/bluetoothd/spp.py:224` and `spp.py:327` |
| **Root cause** | `spp.py` references `protocol.MessageType.CMD_OBD_REQUEST` in `_handle_frame()` and `_handle_obd()`. The name was never added to `protocol.py`'s `MessageType` enum (range 0x20–0x2C has no such entry). |
| **Failure** | `AttributeError` on the enum attribute access. Because `spp.py` is imported at module level by `bluetoothd/__init__.py`, this crashes `bluetoothd` before it starts. |
| **Fix** | Added `CMD_OBD_REQUEST = 0x2D` to `MessageType` in `protocol.py`. |

---

### Bug 4 — HIGH: CRC-corrupt NCP frame never consumed → connection stalls

| | |
|---|---|
| **File** | `system/bluetoothd/spp.py:61` |
| **Root cause** | `Frame.decode()` returns `(None, total)` on a CRC failure (`total` = full frame byte count). `Client.run()` only advances `_buffer` when `frame is not None`. The corrupt frame bytes are never consumed. |
| **Failure** | After any single corrupt byte, the inner `while True` loop breaks immediately on every `recv()`. No further frames are ever processed; the connection stalls silently until the socket closes. |
| **Fix** | Changed `Client.run()` to always advance the buffer by `consumed` bytes regardless of whether `frame` is `None`, so corrupt frames are discarded and parsing continues. |

---

### Bug 5 — HIGH: `sendall()` race between telemetry thread and command thread

| | |
|---|---|
| **File** | `system/bluetoothd/spp.py:216` and `spp.py:68` |
| **Root cause** | `_telemetry_loop()` calls `sock.sendall()` at 10 Hz from a background thread. `Client.run()` also calls `sock.sendall()` when replying to inbound command frames, from the receiver thread. No mutex. |
| **Failure** | Interleaved `sendall()` calls corrupt the NCP byte stream, producing garbled frames on the phone side. |
| **Fix** | Added `self._send_lock = threading.Lock()` to `Client` and wrapped both `sendall()` call sites with `with self._send_lock`. |

---

### Bug 6 — HIGH: `UbloxAvailable` never set → RTK GPS silently unused

| | |
|---|---|
| **File** | `system/ubloxd/pigeond.py` |
| **Root cause** | `get_gps_location_service()` in `common/gps.py` reads the `UbloxAvailable` param to pick the GPS topic (`gpsLocationExternal` vs `gpsLocation`). The only setter in upstream was `ublox()` in `process_config.py` (removed). `pigeond.py` never writes this param. |
| **Failure** | `selfdrived` and `locationd` always subscribe to `gpsLocation` (cell-tower/internal). `pigeond` publishes `gpsLocationExternal` (ublox NTRIP RTK) but nobody reads it. RTK accuracy (~2 cm) is silently thrown away. |
| **Fix** | Added `self.params.put_bool('UbloxAvailable', True)` in `PigeonD.run()` after successful GPS initialization. |

---

### Bug 7 — MEDIUM: Hailo throttle cleared on failed read (stale 0°C used)

| | |
|---|---|
| **File** | `system/thermald/thermald.py:295` |
| **Root cause** | In `_read_hailo_temps()`, when a monitor's `read()` returns `None`, the hot-path is skipped. The cool-down check at line 295 calls `m.should_throttle()` with no argument, which falls back to `_last_temp`. If `_last_temp` was never updated (init value 0.0), `should_throttle` returns `False` → `all_cool=True` → `hailo_throttling` cleared. |
| **Failure** | Hailo thermal throttling is prematurely removed during a read failure. Fan stays at normal speed while the NPU may still be at critical temperature. |
| **Fix** | Restructured `_read_hailo_temps()` to collect results first, then evaluate `all_cool` after the loop — only clearing `hailo_throttling` when all monitors read successfully (`len(temps) == len(self.hailo_monitors)`) and none are throttling. |

---

### Bug 8 — LOW: `LOG_ROOT=''` treated as unset (falsy empty string)

| | |
|---|---|
| **File** | `system/hardware/hw.py:33` |
| **Root cause** | `if os.environ.get('LOG_ROOT', False):` — an empty string `''` is falsy, so `LOG_ROOT=''` is silently ignored, falling through to the SD card probe. Same pattern in `download_cache_root()` for `COMMA_CACHE`. |
| **Failure** | Operator sets `LOG_ROOT=` intentionally (to force internal storage). SD card probe runs instead; if a card is mounted, logs go to external storage. |
| **Fix** | Changed to `if 'LOG_ROOT' in os.environ:` (with same pattern for `COMMA_CACHE`). |

---

## Findings Not Fixed (documented only)

### F9 — dev PC gets `PC=False`, wrong Paths (low priority for hardware-only project)

`PlatformRegistry.detect()` defaults to `'rk3588'` on any machine without `/proc/device-tree/compatible` and without `HARDWARE=` env var. All 45 module-level importers of `system.hardware` silently behave as RK3588. `Paths.swaglog_root()`, `Paths.log_root()`, etc. point at `/data/` which is unwritable on stock Linux.

**Workaround:** set `HARDWARE=pc` in the dev environment. Not fixed in code since this is hardware-only firmware and dev PC testing uses the RK3588 paths intentionally.

---

## Reference Hardware Comparison

Compared against lubancat/rongpin example code in `~/pilot/` (non-openpilot):

| Area | Finding |
|------|---------|
| `/proc/device-tree/compatible` | Reference (lubancat) calls `exit(-1)` on missing file; EOP silently defaults to rk3588. Intentional difference. |
| V4L2 STREAMOFF | Reference (nagasware) calls `VIDIOC_STREAMOFF` in signal handler on exit. EOP `v4l2d.py` does not — may cause EBUSY on daemon restart. Track for future fix. |
| RGA struct layout | Nagasware and EOP use same librga ctypes layout. No divergence found. |
| RKNN quantization | Reference uses INT8 quantized models. EOP must ship matching quantized `.rknn` files. |
| Thermal zone indices | RK3576 BSP uses named zones, not only numbered. EOP `thermal_zones.py` should be validated against target BSP. |
