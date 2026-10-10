# Control / HAL boundary audit (2026-10-09)

HAL changes transport, device discovery and inference backend selection. It
does not justify replacing driver-monitoring policy, vehicle-model equations
or actuator safety limits. The proven NGP v0.10 baseline remains the reference.

The inherited portable controls/runtime/mapd source is enforced byte-for-byte
by `nagaspilot/lineage.py`. EOP still has legacy product overrides of controlsd
and longitudinal_planner for ALCC, adaptive tuning, radar zones, nudge, AEB and
extra speed controllers. These overrides are not requirements of Rockchip HAL.
They are not claimed to be identical to the complete NGP control files.

The audit found and corrected two concrete integration defects:

1. EOP controlsd omitted driverMonitoringState and ignored critical awareness.
   Both controllers now use the shared NGP monitoring_force_decel decision.
2. EOP applied a positive cruise offset after setting the forced target to zero.
   Both planners now apply monitoring_speed_target last, before their MPC call.
   EOP also skips preference offsets during forced deceleration.

Both controls paths use the NGP vehicle-model equations, copied unchanged
from its exact official OpenDBC baseline under MIT provenance. EOP's previous
fallback parameter normalization is a thin input adapter. Reference checks
cover complete and incomplete parameters, calibration updates, low speed and
11/22/33 m/s. No board or HAL import exists in the shared physics module.

NGP retains comma card/OpenDBC. EOP vehicled/socketd decodes Tesla-party CAN
into the same carState / CarParams and consumes carControl. Its minimal native
protocol removes the external dependency while preserving 482 reference CAN
messages, 40 state snapshots and 256 complete controller frames, including
driver override, longitudinal enable, cancel, counters, limits and AEB bits.
Car schema IDs/ordinals remain unchanged. Forward radar3d and surround
radar4d remain sensing adapters; BSD presence never fabricates ranged objects.

Monitoring has one producer: shared driveractivityd at 20 Hz. selfdrived,
controlsd, both planners and the UIs consume its existing monitoring contract.
Road-camera monod detects external objects and is not a driver-monitoring
replacement. Driver-camera/gaze UI and stale driverPoseState subscriptions are
removed. Rear cameras remain rear-view sensors, independent of monitoring.

Host regression tests establish these contracts and reference equivalence.
They do not establish timing on a board, vehicle safety approval, a complete
moving-vehicle replay or firmware/HIL validation. Broader controller sharing
must preserve the listed EOP feature hooks and repeat those validations.
