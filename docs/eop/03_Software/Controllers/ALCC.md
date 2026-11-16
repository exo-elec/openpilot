# ALCC - Always-on Lane Centering Control

**Type:** state machine, run by `controlsd`
**File:** `exopilot/selfdrive/controls/lib/alcc.py` (one implementation on every branch, written on NGP10)
**Tests:** `exopilot/tests/test_alcc.py`

## What it does

With `LatALCC` on, ALCC keeps lane centering (lateral only) while the cruise main switch is on, even when openpilot
itself is not engaged. It cannot create steering authority:

- `controlsd` steers for ALCC only when the state machine suggests it **and** the Panda's live safety state
  authorizes it (`lateral_authorization.lateral_authorized`: every configured safety model matches, controls
  allowed, pandaStates fresh within 0.5 s).
- While the driver steers (`|steeringTorque| > 1.0`) ALCC is OVERRIDING and does not steer. It takes over again
  when the driver lets go.
- Vehicle limits and the car's `steerAtStandstill` still apply. At a standstill on a car that cannot steer there,
  ALCC stays latched but PAUSED and resumes when the car moves.

## States

| State | Steers | Entered when |
|-------|--------|--------------|
| DISABLED | no | feature off, cruise main off, door open / seatbelt unlatched / steering fault, or released by the brake (Disengage) |
| PAUSED | no | latched but not calibrated, wrong gear, standstill (car cannot steer there), or brake pressed (Pause) |
| ENABLED | yes | latched and nothing above applies |
| SOFT_DISABLING | yes, 3 s | openpilot is soft-disabling |
| OVERRIDING | no | the driver is steering |

`controlsState.alccActive` is true while ALCC is steering. EOP10 and up also publish `alccState` (state,
`enabled` = latched, `active`, `available`) for the UI.

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `LatALCC` | bool | 0 | Turn ALCC on (read at start) |
| `ALCCBrakeMode` | int | 0 | What a brake press does (read at start) |

### Brake behaviour (`ALCCBrakeMode`)

| Value | Mode | Brake pressed | After the brake |
|-------|------|---------------|-----------------|
| 0 | Maintain | keeps steering | - |
| 1 | Pause | PAUSED | steers again when released |
| 2 | Disengage | DISABLED | engages again on an LKAS / SET / RESUME press or when cruise main is switched off and on |

## History

EOP10 had its own `selfdrive/controls/lib/alcc.py` (`AlccController`). It was replaced by the shared one on
2026-11-16 (owner 2026-11-15: NGP10's behaviour on every branch). What was dropped and why:

- It kept steering while the driver overrode; the shared one gives up authority on override.
- `ALCCAllowAlways`, `ALCCUnifiedEngagement` and the per-brand "no main button" flags were read but never used.
- `ALCCHoldAtStandstill` let ALCC steer at a standstill on cars whose `steerAtStandstill` is false; removed.
- Its brake mode read `ALCCSteeringModeOnBrake`, while the UI wrote `ALCCBrakeMode`, so the setting never applied.
  `ALCCBrakeMode` is now the one key (the index both UIs already stored, so saved values carry over).
- Its Panda mismatch counter read `pandaState.controlsAllowedLateral`, which is not in the schema, so it never
  counted. Panda authorization is now checked on every frame instead.
- Door / seatbelt at a standstill paused it instead of disabling it; the shared one disables it.

## Related

- DLAT.md - Laneful / laneless arbitration
- LCA.md - Lane Change Assist
- RED.md - Road Edge Detection
