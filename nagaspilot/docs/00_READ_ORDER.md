# NGP10 read order

## 1. Product and runtime

1. [`PROJECT_CONCEPT.md`](PROJECT_CONCEPT.md) — scope and design rules.
2. [`NGP10_FEATURE_MATRIX.md`](NGP10_FEATURE_MATRIX.md) — implemented feature status.
3. [`SPEED_AND_TJA_POLICY.md`](SPEED_AND_TJA_POLICY.md) — CRAWL/WALK/CITY/URBAN/HIGHWAY and traffic-jam behavior.
4. [`NAMING_CONVENTIONS.md`](NAMING_CONVENTIONS.md) — `ngp_` settings and module naming.

Runtime code follows the same order:

1. `nagaspilot/speed_zones.py` defines the shared 2/6/12/24/36 m/s contract.
2. `nagaspilot/controls/ngp_tja.py` gates positive acceleration from lead state.
3. `selfdrive/controls/lib/longitudinal_planner.py` is the small upstream hook
   that composes `nagaspilot/controls/ngp_dlon.py` and `ngp_tja.py`.
4. `selfdrive/controls/controlsd.py` and car control produce commands.
5. OpenDBC/Panda enforces vehicle-model steering safety.
6. BrownPanda translates Tesla-format steering to learned BYD geometry.

## 2. Gateway and vehicle validation

Read [`BROWNPANDA_RADAR.md`](BROWNPANDA_RADAR.md), then the source BYD port
evidence and BrownPanda HIL documents for target-car capture requirements.
Host `paramsd` persists `LiveParametersV2`;
the BrownPanda learner independently persists validated gateway geometry in
DFLASH because the Tesla-compatible CAN contract has no verified geometry
update frame.

Read [`DEPENDENCY_POLICY.md`](DEPENDENCY_POLICY.md) before changing a gitlink.
NGP10's BrownPanda radar adapter is pinned from the shared OpenDBC fork; it is
not copied into this repository.

## 3. Validation rule

Software tests prove boundaries, state transitions, and fail-closed behavior.
They do not replace stationary bench, HIL, and controlled target-car testing.
