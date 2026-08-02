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

NGP10 keeps the official v0.10.0 Panda, msgq, rednose, teleoprtc, and tinygrad
gitlinks unchanged. BrownPanda radar requires an OpenDBC change, so NGP10 pins
public commit `62c915ce4b9ca5d0ce561f7d59b7fff5dac6b5c1` from
[`exo-electronics/opendbc:master`](https://github.com/exo-electronics/opendbc/tree/master).
The branch name documents the update line; the gitlink is the reproducible
authority. There is no OpenDBC `dev/NGP10` branch.

If EOP10 adopts OpenDBC as a dependency, it must pin the same fork master commit
when API-compatible. EOP10 must not depend on NGP10's API generation or on an
unpublished local OpenDBC directory merely to share the repository.
