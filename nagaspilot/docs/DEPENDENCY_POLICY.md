# EOP10 and NGP10 dependency policy

Both products derive from the same official openpilot v0.10.0 commit:
`c085b8af19438956c15592828bd082803f43dfaf`. Product commits build above that
exact baseline; the version label alone is not the authority.

Submodule rules shared with EOP10:

1. Use the commaai repository directly when no product patch is required.
2. Pin every dependency to an exact public commit. A branch field is only an
   update hint and never replaces the gitlink.
3. When either product must modify a dependency, create one
   `exo-electronics/<dependency>` fork and use product branches such as
   `dev/EOP10` and `dev/NGP10` in that shared fork.
4. Never pin a local-only or unreachable commit. A clean recursive clone must
   reproduce the source tree without developer-machine directories.
5. Different product pins are allowed when runtime/API requirements differ,
   but the reason and upstream/fork authority must be documented.

NGP10 keeps the official v0.10.0 Panda, msgq, rednose, teleoprtc, and tinygrad
gitlinks unchanged. BrownPanda radar requires an OpenDBC change, so NGP10 pins
public commit `6f7e8e2ace18cd55aa6e974fa3349e68477901c5` from
`exo-electronics/opendbc:dev/NGP10`.

If EOP10 adopts OpenDBC as a dependency, it must use a pinned `dev/EOP10`
commit in the same exo fork. EOP10 must not depend on NGP10's API generation or
on an unpublished local OpenDBC directory merely to share the repository.
