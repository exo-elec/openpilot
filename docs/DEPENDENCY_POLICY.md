# EOP10 and NGP10 dependency policy

Both products derive from the same official openpilot v0.10.0 commit:
`c085b8af19438956c15592828bd082803f43dfaf`. Product commits build above that
exact baseline; the version label alone is not the authority.

Submodule rules shared with NGP10:

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

EOP10 uses commaai msgq directly because its pinned commit
`0e1ec5eb42404bfed9f5ad6ca06f3044488b3a15` exists upstream and carries no EOP
patch. EOP10's vehicle stack and safety integration are in-tree, so local
`opendbc_repo/` and `panda/` build/reference directories are not submodules or
runtime dependencies.

The shared modified OpenDBC authority is `exo-electronics/opendbc`. NGP10 uses
its `dev/NGP10` branch. If EOP10 later adopts that OpenDBC dependency, it must
use a pinned `dev/EOP10` commit in the same fork rather than another fork or a
local copy.
