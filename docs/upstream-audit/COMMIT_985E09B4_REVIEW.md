# Code Review — Commit `985e09b49` [update]

**Commit:** `985e09b492f59e62a97852bdb9ce715bacff20c5`  
**Subject:** `update` (undescribed mega-commit — rollup of 9 prior review fix sessions + vehicled/tesla Continental radar migration)  
**Reviewed:** 2026-05-31  
**Files changed:** 49 (+1310 / −531) · scope: `selfdrive/vehicled/tesla/` (removed `arc408_interface.py`, `tesla_packer.py`; added `continental_interface.py`; modified `tesla_parser.py`, `teslacan.py`), `system/inferenced/`, `selfdrive/controls/`, `selfdrive/selfdrived/`, `tools/sim/`, `tools/foxglove/`, `system/thermald/`, `system/ubloxd/`, `tools/calibration/`, `tools/convert_models_to_rknn.py`  
**Method:** 3-angle review (line scan / removed-behavior / cross-file) + verification

---

## Summary

This commit is a **rollup of bug-fixes documented in 9 prior review files** (`COMMIT_23527F7F_REVIEW.md` through `COMMIT_73044C6B_REVIEW.md`) plus a **Tesla vehicle-layer refactor**: replaces the ARC408-21 radar interface with a Continental ARS4-xx interface, rewrites `teslacan.py` to be DBC-compliant, and removes the obsolete `tesla_packer.py`.

**All previously-documented bugs were already validated in their respective review sessions.** The focus of this review is the **new vehicled/tesla code** and whether the rollup introduced any cross-file inconsistencies.

---

## Findings Summary Table

| Severity | Issue | File | Status |
|---|---|---|---|
| **MEDIUM** | `_sensor_ok` latches `True` forever — radar loss after init is never reported | `continental_interface.py` | **Documented** |
| **MEDIUM** | No CAN-bus filtering — address collisions on other buses could corrupt radar data | `continental_interface.py` | **Documented** |
| **MEDIUM** | `_le()` lacks bounds checking — `IndexError` on short frames | `continental_interface.py` | **Documented** |
| **LOW** | Systemic bit-position mismatches in simulator `_pack_*` methods (pre-existing) | `tools/sim/lib/simulated_car.py` | **Documented** |
| **LOW** | Stale docstring says `0x399`; code sends `0x39B` | `tools/sim/lib/simulated_car.py` | **Documented** |
| **LOW** | `get_perf_detail()` accesses private `RKNNBackend._stats` | `selfdrive/modeld/runners/rknn_runner.py` | **Documented** |
| **INFO** | Continental radar message defs in `tesla_parser.py` are unused dead code | `tesla_parser.py` | **Documented** |
| **INFO** | Trigger depends on slot-39 `Object_B` — fragile if TC275 skips empty slots | `continental_interface.py` | **Documented** |

---

## Detailed Findings

---

### Finding 1 — MEDIUM: `continental_interface.py` `_sensor_ok` latches `True`; radar fault after initial detection is silent

| | |
|---|---|
| **File** | `selfdrive/vehicled/tesla/continental_interface.py:77` |
| **Root cause** | `self._sensor_ok` is set to `True` when `0x401` (RadarStatus) is first seen and **never reset**. The old `ARC408RadarInterface` checked `Object_SensorState` on **every** trigger cycle. |
| **Failure** | If the Continental radar fails after initial startup (stops sending `0x401` but the CAN bus remains alive), `update()` continues to return `RadarData` with `errors=[]`. Downstream `radard.py` receives non-faulty radar data with stale or empty points, potentially suppressing BSD alerts. |
| **Fix** | Reset `_sensor_ok = False` at the top of `update()` each cycle, so absence of `0x401` within the current burst correctly sets `ret.errors = ['fault']`. |

**Code:**
```python
# Current (latching)
if addr == _STATUS_ID:
    self._sensor_ok = True   # ← never reset

ret.errors = [] if self._sensor_ok else ['fault']
```

---

### Finding 2 — MEDIUM: `continental_interface.py` processes all buses — address-collision risk

| | |
|---|---|
| **File** | `selfdrive/vehicled/tesla/continental_interface.py:66-105` |
| **Root cause** | `update()` iterates `can_list` without checking `m.src` (CAN bus number). The old `ARC408RadarInterface` explicitly filtered to `CANBUS.party` (`m.src == CANBUS.party`). |
| **Failure** | Today the Continental address range (`0x401`–`0x45F`) does not overlap with Tesla message IDs, so there is no immediate collision. If a future device on another bus uses an address in that range (e.g., a custom logger or diagnostic tool), the radar interface will misinterpret it as radar data. |
| **Fix** | Add a bus filter matching the TC275 CAN3 → comma bus mapping (likely `m.src == 1` or a new `CANBUS.radar` constant). Verify against TC275 firmware DBC. |

---

### Finding 3 — MEDIUM: `_le()` helper has no bounds checking — `IndexError` on malformed frames

| | |
|---|---|
| **File** | `selfdrive/vehicled/tesla/continental_interface.py:37-44` |
| **Root cause** | `_le()` does `data[bit >> 3]` without validating that the index is within `len(data)`. The callers guard with `len(dat) >= 8`, but the helper is a standalone utility that could be reused or called with truncated frames in the future. |
| **Failure** | A single malformed CAN frame (< 8 bytes) causes `IndexError`, aborting the entire `update()` call and dropping the whole burst. |
| **Fix** | Add a bounds check: `byte_idx = bit >> 3; if byte_idx >= len(data): return 0.0`. |

**Code:**
```python
def _le(data: bytes, start_bit: int, size: int, scale: float = 1.0, offset: float = 0.0) -> float:
  v = 0
  for i in range(size):
    bit = start_bit + i
    if data[bit >> 3] & (1 << (bit & 7)):   # ← IndexError if bit>>3 >= len(data)
      v |= (1 << i)
  return v * scale + offset
```

---

### Finding 4 — LOW: Simulator `_pack_*` methods still have bit-position mismatches (pre-existing, not fixed by address correction)

| | |
|---|---|
| **File** | `tools/sim/lib/simulated_car.py` |
| **Root cause** | Commit `d11239cc` fixed the **addresses** of 5 simulator CAN messages so they reach `tesla_parser.py` (see `COMMIT_D11239CC_REVIEW.md` Bug 1). However, the **bit positions** inside each `_pack_*` method remain wrong. The commit does not touch the `_pack_*` implementations. |
| **Failure** | Even though the parser now receives the messages, it extracts signals from the wrong bit positions, so simulator-generated steering angle, gear, cruise state, blinkers, and blindspot info are still garbage. This was noted in `COMMIT_D11239CC_REVIEW.md` as "Systemic packer mismatch — separate commit needed." |
| **Affected pairs** | See table below. |

| Packer method | Signal | Sim packs at | Parser reads at | Match? |
|---|---|---|---|---|
| `_pack_system_status` | `DI_accelPedalPos` | bit 0, 16 bits | bit 32, 8 bits | ❌ |
| `_pack_system_status` | `DI_gear` | bit 8, 3 bits | bit 21, 3 bits | ❌ |
| `_pack_epas` | `EPAS3S_internalSAS` | bit 0, 16 bits | bit 37, 14 bits (Motorola) | ❌ |
| `_pack_epas` | `EPAS3S_torsionBarTorque` | bit 16, 8 bits | bit 19, 12 bits (Motorola) | ❌ |
| `_pack_di_state` | `DI_cruiseState` | bit 0, 3 bits | bit 12, 3 bits | ❌ |
| `_pack_di_state` | `DI_digitalSpeed` | bit 4, 9 bits | bit 15, 9 bits | ❌ |
| `_pack_ui_warning` | `leftBlinkerBlinking` | bit 1, 2 bits | bit 25, 2 bits | ❌ |
| `_pack_ui_warning` | `rightBlinkerBlinking` | bit 3, 2 bits | bit 26, 2 bits | ❌ |
| `_pack_sccm` | `SCCM_steeringAngleSpeed` | bit 0, 16 bits | bit 32, 14 bits | ❌ |
| `_pack_das_status` | `DAS_blindSpotRearLeft` | bit 0, 2 bits | bit 4, 2 bits | ❌ |
| `_pack_das_status` | `DAS_blindSpotRearRight` | bit 2, 2 bits | bit 6, 2 bits | ❌ |

**Fix:** Rewrite all `_pack_*` methods to match the exact start_bit, size, scale, and Motorola/Intel format declared in `tesla_parser.py` (or switch to using `teslacan.py` / a shared packer library).

---

### Finding 5 — LOW: Stale docstring in `_pack_das_status`

| | |
|---|---|
| **File** | `tools/sim/lib/simulated_car.py:111` |
| **Root cause** | Docstring says `DAS_status (0x399)` but `send_can_messages()` sends it at `0x39B`. This misleads anyone reading the simulator code into thinking the address is still wrong. |
| **Fix** | Update docstring to `0x39B`. |

---

### Finding 6 — LOW: `RKNNRunner.get_perf_detail()` accesses private backend attribute

| | |
|---|---|
| **File** | `selfdrive/modeld/runners/rknn_runner.py:125-130` |
| **Root cause** | `get_perf_detail()` reads `self.npu._stats` directly. `_stats` is a private field of `RKNNBackend` (defined in `system/inferenced/rockchip_npu.py`). |
| **Failure** | If `RKNNBackend` is refactored to rename or restructure `_stats`, `rknn_runner.py` crashes with `AttributeError`. No abstraction boundary exists for metrics. |
| **Fix** | Expose a public `get_stats()` method on `HardwareBackend` (already exists) and call `self.npu.get_stats()` instead. |

**Code:**
```python
  def get_perf_detail(self) -> dict[str, Any]:
    """Return available performance stats from the NPU backend."""
    stats = self.npu._stats   # ← private attribute access
    return {
      'tasks_completed': stats.tasks_completed,
      'tasks_failed': stats.tasks_failed,
      'total_exec_time_ms': stats.total_exec_time_ms,
    }
```

---

### Finding 7 — INFO: Continental radar message definitions in `tesla_parser.py` are unused dead code

| | |
|---|---|
| **File** | `selfdrive/vehicled/tesla/tesla_parser.py:159-198` |
| **Root cause** | The `TeslaParser` message definitions for `RadarStatus`, `Continental_A_0..39`, and `Continental_B_0..39` were added to the parser, but `continental_interface.py` parses those frames manually with `_le()` and never uses `TeslaParser`. No other code references the Continental names. |
| **Impact** | Zero runtime impact — dead code. Increases maintenance burden if the Continental protocol changes (two places to update). |
| **Fix** | Either (a) remove the unused definitions, or (b) refactor `continental_interface.py` to use `TeslaParser` for consistency. |

---

### Finding 8 — INFO: Trigger depends on slot-39 `Object_B` arriving

| | |
|---|---|
| **File** | `selfdrive/vehicled/tesla/continental_interface.py:30,103-104` |
| **Root cause** | `_TRIGGER_ID = 0x45F` (slot 39 `Object_B`). The interface only returns `RadarData` when this specific frame is seen. The docstring notes that slots 2–39 are always empty (`Tracked=0`). |
| **Impact** | If TC275 firmware is ever optimized to skip empty slots (2–39), the trigger never fires and the radar interface returns `None` permanently, starving `radard.py`. This is a design contract assumption. |
| **Mitigation** | Document the requirement in the TC275 firmware spec, or switch to a time-based trigger (return accumulated data if 100ms elapsed since last burst). |

---

## Previously-Documented Fixes (Verified in this Commit)

The following fixes — previously documented in their respective review files — are all present and correct in this rollup:

| Original Review | Area | Fix | Verified |
|---|---|---|---|
| `COMMIT_CABE4693` | `rknn_runner.py` | `_query_model_info` no-op, `run_async` → sync, `get_async_result` returns stored outputs | ✅ |
| `COMMIT_CABE4693` | `rockchip_npu.py` | `RKNN` → `RKNNLite`, fix `sorted(inputs.keys())`, multi-output key `'outputs'` | ✅ |
| `COMMIT_CABE4693` | `rockchip_rga.py` | `'scale'` alias, case-normalize, packed-array `'src'` path | ✅ |
| `COMMIT_CABE4693` | `hailo_hef.py` | Import `hailo_platform`, model-loaded guard, `_hailort = None` | ✅ |
| `COMMIT_73317B36` | `inferenced.py` | Timeout guard `> 0`, queue lock snapshot, dropped-job response | ✅ |
| `COMMIT_73317B36` | `compute.py` | `CPU = 3` enum, `timeout_ms is not None` guard | ✅ |
| `COMMIT_3610E2E2` | `compute.py` | Executor shutdown on timeout, monitor `.get()` guards, remove `ACL_CPU` fallback | ✅ |
| `COMMIT_3610E2E2` | `gridd/costmap.py` | `except RuntimeError` → `self.backend = None`, CPU numpy fallback | ✅ |
| `COMMIT_93374F4C` | `selfdrived.py` | Add `pandaStates` / `peripheralState` to `ignore_alive`/`ignore_valid`/`ignore_avg_freq` | ✅ |
| `COMMIT_93374F4C` | `pigeond.py` | `params.put_bool('UbloxAvailable', True)` | ✅ |
| `COMMIT_93374F4C` | `thermald.py` | Hailo throttle only cleared when all monitors read successfully | ✅ |
| `COMMIT_93374F4C` | `hw.py` | `'LOG_ROOT' in os.environ` (empty-string fix) | ✅ |
| `COMMIT_9E5B84ED` | `aeb.py` | Re-read `EOPAEBEnabled` param inside `update()` | ✅ |
| `COMMIT_9E5B84ED` | `longitudinal_planner.py` | Reset `mtsc_v_target` / `mslc_v_target` each cycle | ✅ |
| `COMMIT_9E5B84ED` | `red.py` + `controlsd.py` | Add `edge_side` to return dict, fix sign inversion | ✅ |
| `COMMIT_9E5B84ED` | `desire_helper.py` | Reset `lane_change_completed` on BSD abort | ✅ |
| `COMMIT_9E5B84ED` | `plannerd.py` | Add EOP sockets to `ignore_alive` | ✅ |
| `COMMIT_9E5B84ED` | `latcontrol_torque.py` | Fix `error_in` to 18 elements | ✅ |
| `COMMIT_D11239CC` | `simulated_car.py` | Correct 5 CAN addresses, add `cruise_button` logic | ✅ |
| `COMMIT_D11239CC` | `camera_sim.py` | Stereo server name `"stereod"` | ✅ |
| `COMMIT_D11239CC` | `foxglove_bridge.py` | `self.sm.logMonoTime[service]`, `await asyncio.sleep` | ✅ |
| `COMMIT_D11239CC` | `rlog_to_mcap.py` | Use `register_schema()` return value | ✅ |
| `COMMIT_D11239CC` | `camera_calibrator.py` | RMS reprojection error | ✅ |
| `COMMIT_D11239CC` | `convert_models_to_rknn.py` | Remove `input_size_list` for policy model | ✅ |
| `COMMIT_A0034410` | 7 daemons | `except Exception: raise` instead of `return 1` (preserves Sentry capture) | ✅ |
| `COMMIT_A0034410` | `coordinationd.py` | Remove dead `KeyboardInterrupt` handler | ✅ |
| `COMMIT_A0034410` | `reard.py`, `coordinationd.py` | Remove double-logging | ✅ |
| `COMMIT_23527F7F` | `radard.py` | Add `return 0` after except block | ✅ |
| `COMMIT_23527F7F` | `selfdrived.py` | `t.join(timeout=2.0)` | ✅ |
| `COMMIT_93374F4C` | `bluetoothd/spp.py` | `sendall()` lock, CRC buffer advance, `CMD_OBD_REQUEST` enum | ✅ |

---

## Priority Fix Order

### P1 — Before next hardware test drive
1. **`continental_interface.py`**: Reset `_sensor_ok` each cycle (Finding 1). A silent radar fault is a safety issue.
2. **`continental_interface.py`**: Add bus filter (Finding 2). Prevents future address collisions.
3. **`continental_interface.py`**: Bounds-check `_le()` (Finding 3). Hardens against malformed CAN frames.

### P2 — Before simulator regression test
4. **`tools/sim/lib/simulated_car.py`**: Fix bit positions in all `_pack_*` methods (Finding 4). Without this, simulator steering angle, gear, and cruise state are garbage.
5. **`tools/sim/lib/simulated_car.py`**: Correct `_pack_das_status` docstring (Finding 5).

### P3 — Cleanup / tech debt
6. **`rknn_runner.py`**: Use public `get_stats()` instead of `_stats` (Finding 6).
7. **`tesla_parser.py`**: Remove unused Continental definitions or integrate them (Finding 7).
8. **`continental_interface.py`**: Document trigger contract with TC275 firmware team (Finding 8).

---

## Action Required

- [ ] **P1-1** Reset `_sensor_ok` at start of `continental_interface.py:update()`
- [ ] **P1-2** Add `m.src` bus filter to `continental_interface.py` (confirm TC275 bus mapping)
- [ ] **P1-3** Add `byte_idx < len(data)` guard to `_le()`
- [ ] **P2-1** Rewrite simulator `_pack_*` methods to match `tesla_parser.py` bit layouts
- [ ] **P2-2** Fix `_pack_das_status` docstring (`0x399` → `0x39B`)
- [ ] **P3-1** Replace `self.npu._stats` with `self.npu.get_stats()` in `rknn_runner.py`
- [ ] **P3-2** Remove or wire up Continental radar defs in `tesla_parser.py`
- [ ] **P3-3** Add TC275 firmware contract note about slot-39 trigger requirement
