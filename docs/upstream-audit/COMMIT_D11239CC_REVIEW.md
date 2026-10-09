# Code Review — Commit `d11239cc` [TOOLS]

**Commit:** `d11239cc8` (HEAD at time of review)  
**Subject:** [TOOLS] Developer tooling for EOP edge platform  
**Reviewed:** 2026-05-28  
**Files changed:** ~40 · scope: `tools/sim/`, `tools/foxglove/`, `tools/calibration/`, `tools/convert_models_to_rknn.py`  
**Method:** 3-angle review (line scan / removed-behavior / cross-file) + verification

---

## Bugs Found and Fixed

All 8 bugs below were fixed in the same session.

---

### Bug 1 — HIGH: 5 of 8 Tesla CAN addresses wrong — vehicled cannot parse sensor messages

| | |
|---|---|
| **File** | `tools/sim/lib/simulated_car.py:124–134` |
| **Root cause** | The CAN address constants in `send_can_messages()` were taken from a different Tesla DBC revision than the one in `selfdrive/vehicled/tesla/tesla_parser.py`. Five messages use wrong addresses: `DI_systemStatus` sent at `0x273` (parser expects `0x118`), `EPAS3S_sysStatus` at `0x3c2` (expects `0x370`), `DI_state` at `0x118` (expects `0x286`), `UI_warning` at `0x3e9` (expects `0x311`), `DAS_status` at `0x399` (expects `0x39B`). |
| **Failure** | vehicled never receives steering angle, gear, cruise state, blinkers, or blindspot info. Cruise control cannot engage; steer-by-wire feedback absent; UI warnings never set. |
| **Fix** | Corrected all 5 addresses to match `tesla_parser.py` message definitions. |

---

### Bug 2 — HIGH: `cruise_button` never packed — startup auto-engage broken

| | |
|---|---|
| **File** | `tools/sim/lib/simulated_car.py:131` |
| **Root cause** | `SimulatorState.cruise_button` is populated by the sim framework when the user triggers cruise engagement, but `send_can_messages()` only checks `is_engaged` to set `DI_cruiseState`. A button-press event (`cruise_button > 0`) is never reflected in the CAN stream. vehicled reads `DI_cruiseState` from `DI_state` (0x286) and maps value `2` → ENABLED; the button-press path never sets the state to ENABLED. |
| **Failure** | Simulator cannot auto-engage ADAS on startup; operator must manually manipulate `is_engaged` state through external means. |
| **Fix** | `cruise_state = 2 if (simulator_state.is_engaged or simulator_state.cruise_button > 0) else 1` |

---

### Bug 3 — HIGH: `VisionIpcServer("v4l2d")` used for stereo server — shared-memory name collision

| | |
|---|---|
| **File** | `tools/sim/lib/camera_sim.py:53` |
| **Root cause** | Both the main v4l2d server (road/wide/tele) and the stereo server are created with the same name `"v4l2d"`. VisionIpcServer uses the name as a POSIX shared-memory key; the second call overwrites the first server's shared-memory region. |
| **Failure** | When stereo is enabled, the road/wide cameras lose their VisionIPC server handle. modeld and stereod cannot both receive camera frames; road camera frames are silently dropped. |
| **Fix** | Changed stereo server name to `"stereod"` to match the real daemon's server name. |

---

### Bug 4 — HIGH: `msg.logMonoTime` on inner sub-struct → AttributeError

| | |
|---|---|
| **File** | `tools/foxglove/foxglove_bridge.py:310` |
| **Root cause** | `self.sm[service]` returns the inner capnp sub-struct (e.g., `carState`), not the outer `Event` wrapper. `logMonoTime` is a field on the outer `Event`, not on the inner sub-struct. Accessing `msg.logMonoTime` raises `AttributeError`. |
| **Failure** | The bridge crashes on the first received message; no data is ever streamed to Foxglove Studio. |
| **Fix** | Changed to `self.sm.logMonoTime[service]` which reads from the SubMaster's outer-event timestamp cache. |

---

### Bug 5 — HIGH: `Ratekeeper.keep_time()` blocks asyncio event loop

| | |
|---|---|
| **File** | `tools/foxglove/foxglove_bridge.py:314` |
| **Root cause** | `stream_loop()` is an `async def` coroutine run by `asyncio.gather()` alongside `run_server()`. `Ratekeeper.keep_time()` calls `time.sleep(self._remaining)` — a blocking call. Any sleep > 0 ms stalls the entire event loop, preventing `run_server()` from processing WebSocket messages, handling connections, or sending data to clients. |
| **Failure** | At 20 Hz, `keep_time()` sleeps ~50 ms each cycle. The WebSocket server is blocked for 50 ms per frame; client messages (subscriptions, disconnects) queue up; broadcast latency grows unboundedly. |
| **Fix** | Replaced `rk.keep_time()` with `if rk.remaining > 0: await asyncio.sleep(rk.remaining)` followed by `rk.monitor_time()`. This yields to the event loop during the wait. |

---

### Bug 6 — MEDIUM: `input_size_list=[[1,3,256,512]]` wrong for policy model

| | |
|---|---|
| **File** | `tools/convert_models_to_rknn.py:112` |
| **Root cause** | `convert_driving_policy()` passes `input_size_list=[[1,3,256,512]]` — an image tensor shape copied from the vision model converter. The policy model does not take a raw image as input; it takes feature tensors (desire, traffic convention, recurrent state, etc.) with shapes inferred from the ONNX model graph. Overriding with vision's image shape causes RKNN to reshape the policy model's actual inputs incorrectly. |
| **Failure** | `rknn.build()` produces a model with incorrect input shapes, or fails outright. Any deployed policy RKNN is silently malformed and produces garbage inference output. |
| **Fix** | Removed `input_size_list` argument; RKNN infers shapes from the ONNX model's own graph. |

---

### Bug 7 — MEDIUM: Manual `schema_id` counter ignores `register_schema()` return value

| | |
|---|---|
| **File** | `tools/foxglove/rlog_to_mcap.py:144–161` |
| **Root cause** | `_register_schemas()` maintains a local `schema_id = 1` counter and increments it manually, using it as the `schema_id` argument to `register_channel()`. The MCAP `Writer.register_schema()` returns the actual assigned schema ID. If the library starts IDs at 0 (or any value ≠ 1), or if schemas are registered elsewhere before `_register_schemas()` is called, the manually-tracked counter diverges from the library's internal IDs. |
| **Failure** | `register_channel()` is called with schema IDs that don't match any registered schema, producing a malformed MCAP file. Foxglove Studio fails to decode messages or shows schema-not-found errors. |
| **Fix** | Captured the return value of `register_schema()` and passed it directly to `register_channel()`, eliminating the manual counter. |

---

### Bug 8 — LOW: Non-standard reprojection error formula (mean-L2 instead of RMS)

| | |
|---|---|
| **File** | `tools/calibration/camera_calibrator.py:349–357` |
| **Root cause** | Reprojection error is computed as the mean of per-image `L2_norm / n_points`, where `L2_norm = sqrt(sum_of_squared_dists)`. This gives `mean(sqrt(sum_sq) / n)` — not RMS. The standard OpenCV calibration metric is `sqrt(sum_all_sq / total_points)` (RMS per pixel). The non-standard formula over-smooths per-image outliers and produces a lower reported error for datasets with high-variance images. |
| **Failure** | Calibration quality is systematically under-reported. Calibrations that should trigger re-collection pass quality checks. Results are not comparable to OpenCV docs or other tools. |
| **Fix** | Accumulate squared L2 norms and total point count across all images; compute `sqrt(total_sq / total_points)` as the final metric. |

---

## Other Findings (documented, not fixed)

| Finding | Severity | Notes |
|---------|----------|-------|
| `simulated_car.py`: bit positions in `_pack_*` methods don't match tesla_parser.py signal definitions (e.g., `DI_accelPedalPos` packed at bit 0 but parser reads from bit 32) | Medium | Systemic packer mismatch — separate commit needed; fixing addresses first enables the parser to at least receive messages. |
| `foxglove_bridge.py`: converter functions access `msg.logMonoTime` directly (e.g., `convert_car_state`) — same AttributeError for pre-recorded log replay | Info | `convert_car_state` is passed the inner sub-struct; callers that pass the outer event would need to extract `cs = event.carState` first. Acceptable for a dev tool. |
