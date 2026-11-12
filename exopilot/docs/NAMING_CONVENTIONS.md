# Porting Naming Conventions

Release numbers belong to branch names, release documents, and compatibility
statements. They do not belong to implementation identifiers. This keeps code
stable when a release advances from 10 to a later version.

## No product prefix (owner decision 2026-11-12)

Names carry no `ngp_` / `eop_` / `NGP` / `EOP` prefix, and no replacement prefix or
suffix: a module, class, param key or capnp field says what it does (`dlon.py` / `DLON`,
`GreenLightAlert`, `dlonMode`). Param keys follow openpilot / sunnypilot CamelCase. One
feature has one name and one implementation: features a comma 3 can run are owned by
NGP10 (`exopilot/controls/`) and inherited by EOP10, 01M and 02M, which only add inputs
that need ExoPilot hardware. The full old -> new table is `NAMING_WITHOUT_PREFIX.md`.
`exopilot/tests/test_boundaries.py` fails on a prefixed file name or code name.

Release and line labels (`dev/NGP10`, `dev/EOP10`, `dev/EDP10`) stay in branch names and
documents, never in identifiers. The EDP branch keeps dragonpilot's `dp_` names.

Saved settings and logs stay readable across renames: `exopilot/param_migration.py`
moves saved values to the new key once at manager start (a value already under the new
name wins), and a renamed capnp field keeps its ordinal.

## Driver-facing lateral names

| Name | Meaning | Where it is used |
| --- | --- | --- |
| LCC | Lane Centering Control | Generic description of the driving function |
| ALCC | Always-on Lane Centering Control | NGP10, EOP10, and EDP10 driver-facing feature |
| LKAS | Lane Keeping Assist System | The vehicle's stock button, camera, fault, or CAN protocol only |

LKAS and LCC are related but are not interchangeable. LKAS commonly nudges a
vehicle away from lane boundaries, while LCC continuously targets the lane
center. NGP, EOP, and EDP code/UI therefore present the feature as ALCC. EDP's
internal `dp_lat_alka`, `STATUS_ALKA`, and cereal compatibility fields remain
stable for persisted settings and wire compatibility. Existing `LKAS`
identifiers remain unchanged only when they describe an OEM interface or an
upstream generic API.
