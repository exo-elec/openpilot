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
| Wheel-speed scale | `0.0713 kph/bit` provisional | This is the byte-identical reference DBC value used by its Python CarState. The reference safety file instead uses `0.0758`, and its README describes another cluster fit. Keep parser/controller units internally consistent and resolve from manual capture plus an independent speed source. |
| Steering angle limit | `390 deg` | Shared by the reference and upstream OpenDBC draft; also isolated in upstream commit `085bdcf`. |
| Command rate | `50 Hz` | Shared by both implementations and the observed continuous EPS stream requirement. |
| Maximum command delta | `3 deg/20 ms` | The upstream draft starts at `5`; the reference later reports route evidence that `5` caused spikes and that `3` tracked better while staying below the observed stock peak. Use the later route-tested value. |
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

