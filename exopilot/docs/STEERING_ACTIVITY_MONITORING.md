# Steering activity monitoring (SAM)

NGP10, EOP10, 01M and 02M had identical steering activity cores and daemons
before this update. No separate steeringd or driverd producer exists in those
branches. Keep the portable implementation in NGP10 and rebase descendants;
do not add parallel monitoring daemons or require an Exopilot hardware import.

`driveractivityd` publishes `driverMonitoringState` at 20 Hz. It replaces the
camera monitoring producer while retaining the control interface:

- `selfdrived` consumes its existing pre/prompt/critical unresponsive events.
- `controlsd` sets `forceDecel` when `awarenessStatus < 0`; the critical stage
  always publishes a negative value, including the exact-zero boundary.
- The planner consumes that forced-deceleration request through the existing
  controls path. Shared `monitoring_force_decel` and `monitoring_speed_target`
  hooks preserve this contract in both controllers and planners. The final MPC
  target is zero during forced deceleration, even with positive cruise offsets.
  Optional DDSC remains a separate speed-cap feature.
- Driver steeringPressed, brakePressed or gasPressed restores awareness.
  Automated steering torque, wheel angle and vehicle motion never restore it.
- Disengaging restores awareness. Standstill holds awareness. Changing speed
  bands or policy preserves accumulated decay, including a critical warning.

The setting is `ngp_dm_policy` on every descendant because its origin is NGP.
The daemon refreshes it once per second without restarting or resetting the
driver's awareness. There is no monitoring-off setting.

| Speed band | Relaxed | Tight |
|---|---:|---:|
| Below 11 m/s | Hold | Hold |
| 11–22 m/s | 60 s | 30 s |
| 22–33 m/s | 30 s | 15 s |
| Above 33 m/s | 15 s | 10 s |

Times are from full awareness to critical. Soft and prompt stages occur after
50% and 75% of that time. Each boundary has 0.5 m/s hysteresis: enter a higher
band at edge + 0.5, return below edge - 0.5. The persisted `strict` value
retains the deployed relaxed table and remains the default for compatibility.
Unknown policy values fall back to that same deployed table.

Warnings refer to missing driver input, not missing faces. A steering/pedal
availability signal cannot establish gaze, attention, sleep or consciousness.
These custom timings have not been certified against ISO 11270 or another
standard. The below-11 m/s hold and pedal reset are preserved legacy behavior,
not a certification claim. Vehicle validation remains required.
