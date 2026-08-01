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

EOP10 also applies the grid to DLON, PathD scaling, NNFF torque friction,
road-condition speed caps, and hardware-specific perception. Those consumers
remain deferred because their supporting stacks or evidence are not present on
the original comma 3 BYD baseline.

The canonical constants live in `nagaspilot/speed_zones.py`. A new consumer may
import them only after its behavior, boundary equality, fallback, and transition
tests are documented. Crossing a boundary must not reset or extend an active
safety alert.

