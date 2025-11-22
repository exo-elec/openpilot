# BYD ATTO 3 Port Tracking

_Last updated: 2025-11-22_

## Snapshot vs `dev/features`
- `opendbc_repo/opendbc/car/gateway/values.py` now keeps `CAR.BYD_ATTO3` on `byd_atto3` (classic 500 kbps) while `CAR.BYD_DOLPHIN` stays on the CAN-FD `byd_dolphin` DBC (lines 454-472), so both database files remain live.
- Gateway is now a first-class brand: `opendbc_repo/opendbc/car/values.py` and `car/fingerprints.py` import the gateway platform, and `gateway/interface.py` sets `ret.brand = "gateway"` so docs/tests can surface the BYD models.
- BYD-specific tuning tables (lines 99-210) still carry "clone dolphin" placeholders for Atto 3 gains, rate limits, and accel bounds; we need live CAN data before diverging from those defaults.
- `opendbc_repo/opendbc/dbc/byd_atto3.dbc` was rewritten (1,113 insertions / 960 deletions) to normalized line endings plus new ESP/VCU signal notes; rerun lint and regen steps once we settle on the final message set.
- BYD Atto 3 fingerprint now reflects the 2025-11-22 BLF captures (`opendbc_repo/opendbc/car/gateway/fingerprints.py`), replacing the old Dolphin clone so fingerprinting can distinguish the two platforms.
- Tesla car/safety support has been removed (car interface, tests, panda ignition exception, tooling references); BYD gateway is now the only active platform in this branch.
- `docs/CARS.md` now lists the BYD Atto 3 (gateway port) in place of Tesla, with footnotes pointing to this tracking document for install details.
- The original gateway documentation/tests (`.../steering_wrapping_architecture.md`, `.../tuning_guideline.md`, `.../tests/test_gateway.py`) were dropped in dragonpilot, so there is no automated coverage or design doc for the port.

## Open Questions / Gaps
- Validate that future Dolphin fixes stay on the CAN-FD `byd_dolphin` DBC; do not regress by pointing it at the Atto 3 database again.
- We lack steering/longitudinal tuning unique to Atto 3; placeholders at `values.py:99-210` and the safety model currently mirror Dolphin behavior.
- No CI coverage exists for the gateway port after dropping `tests/test_gateway.py`; we still need a regression test and docs update reflecting the Tesla removal.

## Next Up
1. Collect Atto 3 CAN logs and update the steering/longitudinal tables plus torque limits in `gateway/values.py` and `safety/modes/gateway.h` to remove the "clone" placeholders.
2. Decide on DBC ownership: either fork Dolphin + Atto files cleanly or consolidate them with documented differences, then regenerate bindings.
3. Restore a minimal regression test (ported from the deleted `tests/test_gateway.py`) so we can exercise fingerprinting and command packing in CI.
4. Update docs/launch notes to mention the Tesla removal so downstream forks understand the delta.
