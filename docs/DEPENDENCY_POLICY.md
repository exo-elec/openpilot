# EOP10 and NGP10 dependency policy

Both products derive from the same official openpilot v0.10.0 commit:
`c085b8af19438956c15592828bd082803f43dfaf`. Product commits build above that
exact baseline; the version label alone is not the authority.

Submodule rules shared with NGP10:

1. Use the commaai repository directly when no product patch is required.
2. Pin every dependency to an exact public commit. A branch field is only an
   update hint and never replaces the gitlink.
3. When either product must modify a dependency, create one
   `exo-electronics/<dependency>` fork and keep the shared stable dependency on
   that fork's `master` branch.
4. Never pin a local-only or unreachable commit. A clean recursive clone must
   reproduce the source tree without developer-machine directories.
5. Different product pins are allowed when runtime/API requirements differ,
   but the reason and upstream/fork authority must be documented.

EOP10 uses commaai msgq directly at public commit
`0e1ec5eb42404bfed9f5ad6ca06f3044488b3a15`. Starting at EOP10,
OpenDBC is removed as a runtime and submodule dependency. NGP10 retains
normal comma card/OpenDBC at official public commit
`4b203ff5d1ad867de127de6b27382ba73e6e31a7`.

EOP owns the minimal MIT-derived Tesla-party CAN protocol under
`system/socketd/vehicle/protocol/`; NOTICE identifies the original reference
commit. The decoder/controller retain their existing checksum, cadence,
vehicle-model steering limits, driver override and AEB guards. Reference
fixtures compare emitted CAN bytes and decoded vehicle state before/after
removing the dependency. `cereal/car.capnp` is the wire-identical owned copy;
its MIT provenance is in `cereal/LICENSE.car-schema`.

The inherited tinygrad pin is
`d3f09c9bbd542fbfbe68c8569a1173550c408969`, matching official openpilot
`b9c815d56a2827796ad73ca8f09d186243dd8c17`. NGP compiled driving/YOLO
artifacts must be rebuilt with that compiler. EOP keeps RKNN as the default
Rockchip driving backend; the private Chestnut driving transport remains
closed until its existing replay/HIL readiness gates are satisfied.

The shared steering activity core remains NGP-owned and has no Exopilot
hardware dependency. Forward UART radar3d and surround BLE/WiFi radar4d
belong to EOP; radar2d is a planar compatibility view and portable BSD logic.
The BrownPanda gateway exports actual left/right blind-spot flags and no
fabricated Continental radar targets.
