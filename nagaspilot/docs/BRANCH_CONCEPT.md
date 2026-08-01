# dev/EDP10 Branch Concept

## Purpose

`dev/EDP10` is the stable test platform for an original comma 3 installed in
the BYD Atto 3 test car. It uses DragonPilot's proven comma 3 support while
keeping project-specific changes small, reviewable, and reversible.

## Provenance

- Base tag: `base/dev-EDP10-dragonpilot-0.10.0`
- Base commit: `549577a29d580ad3fa968a0db6092c91269fc6e7`
- Source branch: DragonPilot `0.10.0-pre-build`
- Installable release reference: DragonPilot `0.10.0` at `3bc69705461099efff8335bda872edaf2528b157`
- DragonPilot release source record: openpilot 0.10.0 release commit
  `3a9999ce05f7d86387651c1e03990fa738902d18`
- Comparable official openpilot base: `v0.10.0` at
  `c085b8af19438956c15592828bd082803f43dfaf`

The `dragonpilot/` tree and Rick Lan's history must remain intact. EDP changes
belong in `nagaspilot/` or in narrowly scoped integration commits with attribution.

## Product Scope

- Original comma 3 with wide and narrow road cameras.
- Classic CAN and the proven BYD Atto 3 protocol.
- Operation with or without a driver camera; safety monitoring is retained.
- Factory longitudinal/AEB behavior by default.
- Openpilot longitudinal only as a separately validated trial mode.
- A small, consistent policy surface rather than extensive user tuning.

The branch does not adopt EOP's RK3588 HAL, stereo/radar assumptions, or broad
daemon replacements. Dashy may remain in inherited DragonPilot source, but EDP
product integration can disable or hide it without rewriting Rick Lan's code.

## Hardware Boundary

Openpilot `v0.10.0` is the comparison baseline because it is the final official
release for the original comma 3. This project does not target comma 3X or
comma four. EDP10 retains DragonPilot's Snapdragon 845/comma 3 implementation;
EOP10 independently retains ExoPilot's RK3588 implementation. Sharing a release
generation does not make their camera, compute, or hardware abstraction layers
interchangeable.

## Development Branches

| Branch | Base | Role |
| --- | --- | --- |
| `dev/EDP10` | DragonPilot 0.10.0 | Primary conservative comma 3/BYD line |
| `dev/EDP11` | DragonPilot 0.11.1 | Compatibility and forward-port reference |
| `dev/EOP10` | openpilot v0.10.0 + EOP | Experimental feature reference |
| `dev/NGP10` | openpilot v0.10.0 | Future clean openpilot-based line |

All branches remain development-only until vehicle evidence supports release.
