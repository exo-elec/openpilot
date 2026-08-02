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
`NGP10Capabilities` or `NGP10DLAT`. Daemon module and process names retain the
prefix separator, for example `ngp_shadowd`; cereal fields follow their normal
lower-camel convention, such as `ngpState`.

The EDP branch intentionally retains dragonpilot's established `dp_` names.
Renaming those persisted parameters to `edp_` would break existing device
settings and is not a porting-style improvement.
