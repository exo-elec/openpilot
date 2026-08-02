# BYD Atto 3 parameter evidence

- Reviewed: 2026-07-31
- Target: 2024 RHD export BYD Atto 3 on original comma 3
- Scope: camera/chassis-CAN port; private radar excluded

## Decision table

| Parameter | NagasPilot baseline | Evidence and decision |
|---|---:|---|
| Mass | `1750 kg` | Confirmed by the BYD reference car and official BYD extended-range/UK specifications. Official Australian specifications list `1680/1750 kg` by battery variant, so verify the test-car trim. |
| Wheelbase | `2.72 m` | Confirmed by both official BYD specifications and both OpenDBC/reference implementations. |
| Steering ratio | `19.8` provisional | The executable BYD reference uses `19.8`; its README separately says a `19.5` drive fit paired with its stated speed fit. The current upstream OpenDBC draft uses `14.8` on a different test car. Preserve executable-reference parity until manual fitting resolves our car. |
| Wheel-speed scale | `0.07142857 kph/bit` (was `0.0713` provisional) | **Resolved from firmware, not target-car capture.** `TC275_BrownPanda/DBC/byd_atto3.c` case `0x1F0` reads `ReadPhys(rdata, 0, 16, BIT_LSB_FIRST, 0.07142857f, 0.0f, UNSIGNED)` — the exact `1/14` value, not the `0.0713` rounding this table previously carried nor the old reference safety file's `0.0758`. `opendbc/dbc/byd_atto3.dbc`'s `ESP_VehicleSpeed` and `opendbc/safety/modes/byd.h` were both updated to `0.07142857` in the same commit so the DBC and safety C model can't drift apart on this. Still needs an independent-speed-source cross-check per [BYD_CANAPE_OPEN_QUESTIONS.md](BYD_CANAPE_OPEN_QUESTIONS.md) before being called target-car confirmed, but this is no longer a three-way guess between community sources — it's the one value with a real firmware witness. |
| Steering angle limit | `390 deg` | Shared by the reference and upstream OpenDBC draft; also isolated in upstream commit `085bdcf`. |
| Command rate | `50 Hz` | Shared by both implementations and the observed continuous EPS stream requirement. |
| Maximum command delta | Continuous ISO accel/jerk vehicle-model limit + speed-zoned backstop LUT | Superseded three times: flat `3 deg/20 ms` -> provisional 3-step CRAWL/CITY/HIGHWAY taper -> continuous ISO formula (current). Both `opendbc/safety/modes/byd.h` (`steer_angle_cmd_checks_vm`) and `opendbc/car/byd/carcontroller.py` (`apply_steer_angle_limits_vm`) derive the rate/angle cap continuously from `MAX_LATERAL_ACCEL`/`MAX_LATERAL_JERK` and BYD's own `steer_ratio`/`wheelbase`/slip factor. A 7-point speed-zoned LUT (`ZONE_MAX_ANGLE_*`/`ZONE_MAX_RATE_*`, 0/6/12/18/24/30/36 m/s) sits on top as defense-in-depth, 100% on `byd.h`/TC275/TC375, 80% on the controller side - see `nagaspilot/docs/STEERING_LIMIT_POLICY.md` for the full design and verified numbers. `CarControllerParams.ANGLE_LIMITS`'s `0/2/6/12/24` breakpoint-list positional args are still not read by `apply_steer_angle_limits_vm` - informational only. The BYD-specific slip factor is still a provisional design (not a route-tested capture) - see `MIGRATION_PLAN.md` task 3. |
| Lateral accel/jerk bound | about `3.6 m/s²`, `3.6 m/s³` | Same formula in the reference and upstream draft, including the 0.06 road-roll allowance. |
| Steering actuator delay | `0.2 s` | Same in the reference and upstream draft. Confirm by timestamped requested-versus-measured response. |
| Steering limit timer | `0.4 s` | Same in the reference and upstream draft. |
| Driver override threshold | raw `10` | Same in both source implementations; verify driver-torque distribution from the target capture. |
| Longitudinal actuator delay | `0.5 s` | Reference reports about 550 ms best-fit plant lag. Upstream draft is lateral-only, so manual capture must confirm this trial-only value. |
| Longitudinal controller values | Reference values | `vEgoStopping/Starting=0.3`, `stopAccel=-0.5`, `startAccel=1.5`, and the light PID gains are copied from the driven reference and remain trial-only. |

## Why 19.8 is the current baseline

There are three published numbers, but they are not equally applicable:

1. The [official OpenDBC Atto 3 draft](https://github.com/commaai/opendbc/pull/3337)
   uses `14.8`. It is still a draft, has no completed review, and describes
   remote testing followed by planned rental-car validation.
2. The [BYD reference README](https://github.com/shemps/byd-atto3-openpilot-port)
   says `19.5` was fitted together with its documented vehicle-speed scale.
3. The same reference's executable
   [`values.py`](https://github.com/shemps/byd-atto3-openpilot-port/blob/main/port/opendbc/car/byd/values.py)
   uses `19.8`, while its executable DBC/CarState path uses `0.0713`.

NagasPilot is currently porting the executable reference path and its exact
DBC, so `19.8` avoids mixing the README's fitted ratio with a different speed
calibration. This is a compatibility baseline, not a claim that 19.8 is the
physical rack ratio.

## Manual resolution method

Fit steering ratio and speed scale together from manually synchronized factory-MPC video
and raw CAN:

1. Derive an independent speed trace from GPS or a calibrated measurement and
   fit raw `0x1F0` against it over several steady speeds.
2. Use multiple constant-radius, low-roll turns with stable speed. Align factory
   MPC video curvature, yaw response, and `0x11F` steering angle by timestamp.
3. Evaluate `14.8`, `19.5`, `19.8`, and a continuous fitted value using the same
   speed scale. Do not compare ratios under different speed calibrations.
4. Prefer the value with near-zero signed curvature error across left/right
   turns and no systematic speed-dependent bias; record confidence intervals.
5. Change the baseline only together with its evidence report and regression
   replay.

## Primary online sources

- [Official BYD UK Atto 3 specification](https://www.byd.com/content/dam/byd-site/uk/pdfs/atto-3/ATTO3-0125-BPS-EN-V10-Right-web.pdf)
- [Official BYD Australia Atto 3 specification](https://www.byd.com/content/dam/byd-site/au/product/BYD%20ATTO%203%20Vehicle%20Specifications.pdf)
- [OpenDBC Atto 3 draft PR #3337](https://github.com/commaai/opendbc/pull/3337)
- [BYD Atto 3 driven reference](https://github.com/shemps/byd-atto3-openpilot-port)
