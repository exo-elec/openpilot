# EOP10 and NGP10 dependency policy

Both products derive from the same official openpilot v0.10.0 commit:
`c085b8af19438956c15592828bd082803f43dfaf`. Product commits build above that
exact baseline; the version label alone is not the authority.

Submodule rules shared with NGP10:

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

EOP10 uses commaai msgq directly because its pinned commit
`0e1ec5eb42404bfed9f5ad6ca06f3044488b3a15` exists upstream and carries no EOP
patch. `opendbc_repo/` is an explicit submodule because the Tesla CAN adapter
and safety envelope are shared with the EXO vehicle stack; `panda/` remains a
build/reference directory and is not an OpenPilot runtime dependency.

The shared modified OpenDBC authority is `exo-electronics/opendbc`. EOP10 and
NGP10 pin the same EXO commit, `49d48498` (the full SHA is recorded by the
parent gitlink).
It is descended from the official `v0.2.1` release and includes the Tesla
BrownPanda radar/safety work required by both products.

The official `v0.2.1` tag is a compatibility reference, not the selected
runtime pin: it declares Python 3.9+ but does not contain the BrownPanda safety
tree. Rebasing to that tag would remove required safety APIs. The shared fork
also carries a small Python 3.10 compatibility layer for Ubuntu 22.04 and ROS
2 Humble. Do not replace this pin with upstream `v0.2.1`.

Tinygrad is pinned to the official `v0.13.0` tag and is not floated on
`master`. Msgq and rednose intentionally follow their commaai `master` pins;
the remaining `third_party` entries are exact gitlinks with tag/branch hints
in `.gitmodules` and should not be mass-upgraded without RK3588 ABI validation.
