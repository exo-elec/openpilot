# Code boundaries

Enforced by `nagaspilot/tests/test_boundaries.py`.

| Layer | Location | Rule |
| --- | --- | --- |
| Upstream runtime | `selfdrive/`, `system/`, `cereal/` | Integration hooks only. A hook reads params, passes plain values/callables into `nagaspilot`, and applies the result. |
| Pure policy | `nagaspilot/controls/` (`ngp_*`, later `eop_*`) | No `selfdrive`/`system` imports, no `Params`, no `cereal.messaging`. Inputs are arguments; outputs are suggestions. |
| Real daemons (EOP branches only) | `nagaspilot/daemons/<name>d/` | New daemon implementations live here and register through `system/manager/process_config.py`. Existing `selfdrive/` daemon imports are compatibility shims while each daemon is migrated. |
| Board layer (RK3588 / ExoPilot 01M, 02M) | `system/hardware/<board>/` | Never imported from `nagaspilot`. Hardware support is decided by the branch, not by runtime flags. |
| UI | `selfdrive/ui/qt/offroad/ngp_{panel,controls}.*` | Reviewed exception; keep upstream edits to a registration line. |
| Tests | `nagaspilot/tests/` | All NGP/EOP tests live here. |

Hook-passed values: `NGPDLON(get_bool=Params().get_bool)`,
`NGPLeadHandoff(radar_to_camera=RADAR_TO_CAMERA)`.

## Hardware rule

Each branch carries only code its hardware can run and feed.

- `dev/NGP10` (comma 3): road and wide-road cameras, driver camera, IMU, GPS, CAN and
  BrownPanda radar. No stereo, side/rear cameras, accelerator-backed detectors or BEV grids.
- EOP branches (ExoPilot 01M, 02M; RK3588 and its sensors): `eop_` modules for stereo,
  gridd, pathd, radar3d, AEB and FCW live here, and are never merged into NGP. Daemon
  relocation is incremental; see `CODE_BOUNDARY_TASKS.md` for migrated daemons and remaining work.
- Shared code must work from comma 3 inputs alone. If it needs a sensor comma 3 lacks, it is EOP code.
