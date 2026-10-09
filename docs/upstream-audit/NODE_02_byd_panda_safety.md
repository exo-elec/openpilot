# Node 2 — Panda safety: `byd.h` + `can_common.h` layering (EDP10)

**Branch:** `dev/EDP10` · **Files:** `opendbc_repo/opendbc/safety/modes/byd.h`,
`opendbc_repo/opendbc/safety/safety.h`, `opendbc_repo/opendbc/safety/safety_declarations.h`,
`panda/board/drivers/can_common.h` · **Verdict: ✅ pass — no bugs found, correctly layered**

## `can_common.h` — ignition detection

The +12-line addition to `ignition_can_hook()` follows the **exact existing pattern**
already used for Rivian, Tesla Model 3/Y, and Mazda in that same function — a
per-car `if (addr == 0xNNN)` exception block that sets `ignition_can`. This is
not a layering violation; `ignition_can_hook` is the established location for
this kind of car-specific plumbing when a car has no dedicated ignition line.

Verified against source:
- DBC (`byd_atto3.dbc`): `SG_ VCU_Gear : 40|3@1+` → bit 40 = byte 5, bit 0,
  length 3 → `msg->data[5] & 0x7U`. Matches.
- `carstate.py`'s `GEAR_MAP = {1: park, 2: reverse, 3: neutral, 4: drive}` →
  the C code's `gear >= 1 && gear <= 4` covers exactly the valid-gear range
  (0 = not-yet-broadcasting/off, 5-7 = reserved/invalid). Consistent with the
  other exceptions in the file (Tesla/Mazda also gate on a specific decoded
  state, not a raw bit).
- The `0xF8` mask guard (bits 3-7 must be zero) before trusting the gear field
  is a reasonable defense against `0x242` being reused with a different frame
  shape in some other BYD ECU/mode — same spirit as the Rivian/Tesla counter
  checks in the same function guarding against ID collisions.

## `byd.h` — safety mode

Reviewed `byd_rx_hook`, `byd_tx_hook`, `byd_checksum_valid`, the zone-based
angle/rate backstop tables, and `byd_init`'s RX/TX message allow-lists.

- **RX signal decode cross-checked against DBC + `carstate.py`:** `gas_pressed`
  (byte0 > 10, matches `carstate.py`'s `VCU_AccelPedalRaw > 10` threshold
  exactly — also asserted by `test_byd.py`'s `GAS_PRESSED_THRESHOLD = 10`),
  `brake_pressed` (`GET_BIT(msg, 37)` = byte4 bit5, matches DBC
  `VCU_BrakePressed : 37|1@1+`), vehicle speed scale (`0.07142857f` ≈ 1/14).
- **`desired_angle_last` handling initially looked suspicious** — `byd_tx_hook`
  calls the shared `steer_angle_cmd_checks_vm()` helper (which itself sets
  `desired_angle_last = desired_angle` on no-violation, or clamps it to
  `angle_meas` on violation — see `opendbc/safety/lateral.h:324-356`), then
  *redundantly* re-clamps `desired_angle_last` if the accumulated `violation`
  flag from byd.h's **own** earlier checks (zone limits, magic-byte checks)
  is set. Traced against the precedent file (`tesla.h`, the only other
  `steer_angle_cmd_checks_vm` caller): Tesla does **not** do this second
  clamp, meaning Tesla has a latent gap — if Tesla's own post-VM-call checks
  raise a violation that the VM check itself didn't catch, `desired_angle_last`
  is left at the (violating) commanded value instead of being reset to the
  measured angle. **byd.h's redundant clamp is not a bug — it closes a gap
  that exists in the upstream precedent it's modeled on.** Not filed as an
  upstream issue since it's out of scope for this branch's audit, but worth
  noting `tesla.h` for comparison if this branch's authors want to submit it
  upstream later.
- **Tests exist and are consistent with the safety code's constants**
  (`test_byd.py`: `PandaCarSafetyTest` + `AngleSteeringSafetyTest` mixins,
  magic constants 251/-252 for `MPC_SteerAngleRateUpper/Lower` tested,
  `STEER_ANGLE_MAX = 390` matches `BYD_STEERING_LIMITS.max_angle = 3900` /
  `angle_deg_to_can = 10`, ISO lateral accel/jerk test present).
- Naming/provenance comments in `byd.h` are unusually good: they explicitly
  cite which constant came from which reference implementation
  (`shemps/byd-atto3-openpilot-port` vs `TC275_BrownPanda`) and flag the
  latter's `120 deg` as an **uncited placeholder that should be treated as
  unverified** — this is exactly the kind of primary-source discipline the
  rest of this audit should hold other branches to.

## Conclusion

No correctness bugs found. The car-specific addition is placed in the correct
layer (per-car exception inside the existing shared-hook pattern, not a
generic-plumbing change), decode fields are cross-verified against the DBC and
`carstate.py` independently, and the safety-limit design exceeds its own cited
precedent's rigor. No fixes applied.
