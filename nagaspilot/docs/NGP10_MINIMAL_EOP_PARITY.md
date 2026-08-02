# Minimal EOP10 parity

NGP10 carries EOP10 behavior when it can be implemented with comma 3 inputs and
the normal openpilot runtime. It excludes duplicate localization/parameter
learners, vehicle-specific services that the gateway replaces, and unvalidated
actuation paths.

Current parity includes direct DLON/coasting, TJA, shared speed-zone comfort,
ALCC, lane-change assistance, road-edge gating, and speed-dependent steering
safety. NGP uses upstream `paramsd` rather than cloning EOP adaptation logic.
