# Code Review — Commit `1d5f050ef` [update]

**Commit:** `1d5f050ef25e67f018f9cb711ca900f598c7b60f`  
**Subject:** update  
**Reviewed:** 2026-05-31  
**Files changed:** 35 (+2,664 / −851)  
**Method:** structural review + syntax validation + capnp schema check  

---

## Summary of Findings

| Severity | Issue | File | Status |
|---|---|---|---|
| 🟠 HIGH | Commit message is a single word ("update") for a 2,600+ line diff spanning 35 files, 4 subsystems, and a new daemon — completely unusable for `git log`, bisect, or revert | `git history` | Open |
| 🟡 MEDIUM | `cereal/log.capnp` event IDs `@284` (`adaptiveDrivingState`) and `@285` (`ncpVehicleData`) are high-numbered and may conflict with future upstream additions if this fork is ever rebased | `cereal/log.capnp` | Open |
| 🟡 MEDIUM | `system/manager/process_config.py` registers `adaptd` as `always_run` even though `EOPAdaptdEnabled` defaults to `"0"`; daemon starts, polls, and returns early, consuming scheduler resources | `system/manager/process_config.py` | Open |
| 🟢 LOW | `CLAUDE.md` references `docs/eop/04_Integration/BLE_DESIGN.md` but the actual file is at `docs/eop/04_Integration/BLE_DESIGN.md` — path is correct, however the doc header says "EOP BLE / Bluetooth Integration Design" which is a rename from "OpenPilot BLE Integration Design"; verify no other docs link to the old title | `CLAUDE.md` | Open |
| ✅ OK | All new Python files (`adaptd.py`, `ble_gatt.py`, `ncp_session.py`, `pairing_agent.py`) pass `ast.parse()` — no syntax errors | Multiple | — |
| ✅ OK | `cereal/custom.capnp` and `cereal/log.capnp` load successfully with `capnp.load()` | `cereal/` | — |
| ✅ OK | `controlsd.py` and `selfdrived.py` correctly guard `adaptiveDrivingState` access with `sm.valid` and range checks | `selfdrive/controls/controlsd.py`, `selfdrive/selfdrived/selfdrived.py` | — |

---

## Other Findings (documented, not fixed)

| Finding | Severity | Notes |
|---------|----------|-------|
| `selfdrive/adaptd/adaptd.py` `_obd_state_to_vehicle_data()` maps `obdState` fields to `NcpVehicleData`; if `obdState` schema drifts, this mapping becomes stale | Low | Current mapping is 1:1 with the schema in this commit. Add a schema-version comment if the struct is expected to evolve. |
| `system/bluetoothd/spp.py` is heavily refactored; diff indicates missing `import struct` and missing handlers were fixed (per `CLAUDE.md`) | Low | Good bug fixes, but the diff is large enough that a separate `fix(bluetoothd): ...` commit would have been clearer. |
| `pairing_agent.py` uses `DisplayOnly` BlueZ capability and `DisplayPasskey` handler — consistent with the security model documented in `BLE_DESIGN.md` | Low | Correct. |
| `docs/eop/04_Integration/BLE_DESIGN.md` is almost entirely rewritten (325 lines changed); upstream attribution lost | Low | Expected for EOP-only documentation, but DELTA_AUDIT.md should note the rewrite if the file is ever considered for upstream revert. |
| `selfdrive/adaptd/tests/test_adaptd.py` adds 311 lines of unit tests for the new daemon | Low | Good coverage. Verify they run on dev PC (pure Python, no Cython deps — should pass). |

---

## Priority Fix Order

1. **P0 — Commit message** — Rewrite the commit message before any rebase or upstream presentation. Suggested:  
   `feat(adaptd,bluetoothd): add adaptive driving daemon, BLE GATT/SPP refactor, NCP v4.1`  
2. **P1 — `adaptd` registration** — Consider making `adaptd` conditional on `EOPAdaptdEnabled` param at manager level, or keep as `always_run` with fast early-exit (current behavior is acceptable but wasteful).  
3. **P2 — capnp event IDs** — Document the EOP-specific event ID reservation range (e.g., 280–299) in `cereal/log.capnp` comments to prevent future collisions.

---

## Verdict

🟡 **Safe to keep after commit-message rewrite.** The code changes are sound: new daemon is well-structured, capnp schemas validate, syntax checks pass, and `controlsd`/`selfdrived` consume the new message safely. The only blocker for upstream hygiene is the non-descriptive commit message. Recommend splitting this into 3–4 focused commits if history is ever rewritten.
