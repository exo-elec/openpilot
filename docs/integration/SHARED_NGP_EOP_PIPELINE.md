# Shared NGP10 → EOP10 → 01M → 02M pipeline

NGP10 owns portable policy, map and runtime helpers. EOP10 inherits those exact
implementations and adds normalized sensor, vehicle transport and hardware
adapters. 01M and 02M inherit the entire EOP source tree; only their build
profile changes. Product differences belong at the input/configuration boundary.

`nagaspilot/runtime/feature_keys.py` maps NGP and EOP settings names.
`LongitudinalSettings` keeps a separate two-second cache for each settings
instance, preserving normal acceleration and disabled adaptive-gap defaults.
`DriveModeApplier` uses the same mappings without changing user parameter names.

SOC shares its observation type and lane geometry gates. NGP's ramped offset
and EOP's advisory result retain their existing confirmation and authority.
Turn signal direction is common; turn speed, lateral engagement and lane-change
gates remain explicit product behavior. This does not enable an extra controller.

Run `python3 nagaspilot/lineage.py --parent dev/NGP10 --child dev/EOP10` to
reject changes or deletions of inherited portable Python source. EOP-only
adapters are allowed. Run it against 01M and 02M as well. This complements
`footprint.py`, which measures changes against official openpilot instead.

The NGP refresh uses its current remote parent rather than the older local
fork point. EOP's reviewed product delta is replayed as one layer, retaining
its schemas/ordinals, controllers, SocketCAN transport, native UI and hardware
services. Old branch histories remain in local backup refs. Child profile
commits are replayed after that parent layer. No merge commit substitutes for
the linear ancestry check.

Source parity and host policy tests do not establish on-road behavior. Camera
calibration, real actuator outputs, NPU allocation, live cloud voice and native
builds still require their existing hardware/replay validation. Schema and
transport differences between NGP and EOP are intentional adapters; their
full managers and complete source trees are not interchangeable.
