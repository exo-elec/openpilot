# Porting Naming Conventions

Release numbers belong to branch names, release documents, and compatibility
statements. They do not belong to implementation identifiers. This keeps code
stable when a release advances from 10 to a later version.

| Line | Branch/release label | Implementation prefix | Example |
| --- | --- | --- | --- |
| dragonpilot / EDP | `dev/EDP10` | `dp_` | `dp_lat_alka` |
| NagasPilot | `dev/NGP10` | `ngp_` | `ngp_speed_policy.py` |
| EnhancedOpenPilot | `dev/EOP10` | `eop_` | `eop_settings_backup.py` |

Python classes use the corresponding stable uppercase product prefix where a
prefix is needed, for example `NGPCapabilities` and `NGPDLAT`, never
`NGP10Capabilities` or `NGP10DLAT`. NGP-owned policy modules live in
`nagaspilot/controls/` and retain names such as `ngp_tja.py`. Upstream runtime
files contain only integration hooks. Cereal fields use normal lower-camel
names such as `ngpDlonMode`.

The EDP branch intentionally retains dragonpilot's established `dp_` names.
Renaming those persisted parameters to `edp_` would break existing device
settings and is not a porting-style improvement.

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
