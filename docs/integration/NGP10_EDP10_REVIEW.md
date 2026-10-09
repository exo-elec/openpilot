# NGP10 improvements from the independent EDP10 comparison

NGP10 remains the baseline. EDP10 is an independent reference, not a Git parent.
NGP-origin settings retain `ngp_` names; EOP-origin shared policy uses `EOP` names.

Implemented:

- Match official openpilot v0.10's OpenDBC API and pin (4b203ff5). Preserve the
  former BrownPanda Continental radar consumer in a portable NGP adapter, with
  complete-set, pair-index, bus, freshness and fault checks. EOP retains its own
  hardware-compatible OpenDBC pin.
- Require fresh (under 500 ms), matching live Panda controls authorization for
  ALCC suggestions. This does not add firmware authority for independent lateral
  engagement: unsupported safety configurations remain unavailable.
- Expose existing policy switches and bounded choices in the NGP native settings
  panel. Add optional HUD hiding above a speed, manual brightness, engagement-only
  chime muting, custom shutdown timing, recording delay and installed-vehicle
  selection. Defaults preserve stock behavior; safety alerts remain audible.
- Offer an optional loopback-only read-only status viewer on port 9091. It exposes
  only four trip counters and onroad status, not configuration or vehicle commands.
- Handle typed Params booleans and migrate legacy EOP policy switches before
  manager initialization writes defaults. Explicit canonical choices win.

EDP's ineffective coasting/brake-suppression hook is intentionally excluded.
This change does not import BYD/Chery controllers, alternate safety firmware,
remote Dashy video streaming, monitoring-disable modes or force-offroad modes.
Vehicle selection is restricted to installed OpenDBC interfaces; it does not add
vehicle support. BrownPanda firmware and on-device operation require separate
hardware validation.

Validation: 86 focused policy/parser/HTTP/migration tests passed using the actual
pinned OpenDBC and NGP schemas. All 230 installed interfaces imported; BrownPanda
adapter construction passed. Changed native UI translation units passed GCC
syntax checking with Qt5 and generated Cap'n Proto headers (existing deprecation
warnings). These checks do not constitute a full device build or road test.
