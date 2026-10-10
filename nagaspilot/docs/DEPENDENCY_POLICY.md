# EOP10 and NGP10 dependency policy

Both products derive from the same official openpilot v0.10.0 commit:
`c085b8af19438956c15592828bd082803f43dfaf`. Product commits build above that
exact baseline; the version label alone is not the authority.

Submodule rules shared with EOP10:

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

NGP10 uses official openpilot v0.10.0's OpenDBC API and public pin
`4b203ff5d1ad867de127de6b27382ba73e6e31a7`. It has no BrownPanda radar patch.
Other official dependency gitlinks remain unchanged. EOP10 retains its
hardware-compatible OpenDBC generation; sharing portable policy does not require
identical dependency ABIs. Pins must remain publicly reproducible.
