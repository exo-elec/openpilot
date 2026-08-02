# NagasPilot speed-zone policy

NagasPilot defines `CITY_SPEED_MPS`, `HIGHWAY_SPEED_MPS`, and `MAX_SPEED_MPS`
at 12, 24, and 36 m/s (approximately 43, 86, and 130 km/h). They describe
reviewable operating zones; they are not automatically a vehicle speed limiter.

| Consumer | Zone behavior | Decision |
|---|---|---|
| `steerd` | No-camera nudge above 12; attentive-camera nudge above 24; short timing for both at or above 36 | Implement in NagasPilot |
| Camera distraction | Existing vision 3/5/11-second alerts | Preserve; stricter alert wins |
| LCA | Fixed 12 m/s entry and fixed 3.0 s automatic delay; NagasPilot hides both DragonPilot controls | Implement |
| ALKA | Safety/engagement conditions, not a lane-change speed gate | Do not couple to zones |
| Road-edge detection | Always-on model-confidence block for the requested lane-change side | Implement; no speed gate |
| SOC | Selectable fixed 0.20 m offset away from one-sided BYD BSM; sustained four-line 2.8–3.6 m geometry at or above 24 m/s | Rear-approach trial only; full overtaking support requires validated adjacent tracks |
| Longitudinal acceleration | 0/12/24/36 m/s interpolation with the existing acceleration values | Implement; values unchanged |
| Turn acceleration limit | 24/36 m/s interpolation with the existing total-acceleration values | Implement; values unchanged |
| Wide-camera UI | Wide view below 12 m/s, existing 12–15 m/s hysteresis, road view above 15 m/s | Implement on original comma 3 UI |
| BYD `0x1E2` angle-rate limit | 3-step interpolation at `CRAWL`(0)/`CITY_SPEED_MPS`(12)/`HIGHWAY_SPEED_MPS`(24) m/s: `4`/`2`/`.5` deg-per-50Hz-cycle windup, `4`/`3`/`1.5` unwind. `opendbc/safety/modes/byd.h`'s `BYD_STEERING_LIMITS` and `opendbc/car/byd/values.py`'s `CarControllerParams.ANGLE_LIMITS` | Implemented, hardcoded not imported (see note below); rates are a provisional design pending target-car steering-rate capture, not route-tested values like the other rows in this table |

EOP10 also applies the grid to DLON, PathD scaling, NNFF torque friction,
road-condition speed caps, and hardware-specific perception. Those consumers
remain deferred because their supporting stacks or evidence are not present on
the original comma 3 BYD baseline.

The canonical constants live in `nagaspilot/speed_zones.py`. A new consumer may
import them only after its behavior, boundary equality, fallback, and transition
tests are documented. Crossing a boundary must not reset or extend an active
safety alert.

**`nagaspilot/speed_zones.py` does not currently exist in this tree** (only
stale `.pyc` caches do), and nothing under `nagaspilot/selfdrive/` or
`nagaspilot/system/` currently imports it or references
`CITY_SPEED_MPS`/`HIGHWAY_SPEED_MPS` by name (checked 2026-08-02). Every row
in the table above other than the BYD one is therefore this policy's intended
design, not yet-implemented code, as far as this check found - worth
confirming before treating any of those rows as live behavior. The BYD row
is coded but cannot import the module even once it exists, since
`opendbc_repo` (where `byd.h`/`values.py` live) has no dependency on
`nagaspilot/`; its `12`/`24` m/s values are hardcoded in
`opendbc/car/byd/values.py` with a citation to this file instead. If this
file's canonical values ever change or `speed_zones.py` is restored,
`opendbc/car/byd/values.py` and `opendbc/safety/modes/byd.h` will not update
automatically and must be checked by hand.

