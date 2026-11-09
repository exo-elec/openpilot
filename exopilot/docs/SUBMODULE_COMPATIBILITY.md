# EDP10 submodule compatibility audit

EDP10 cannot be converted directly to the NGP10 submodule layout yet.

| Branch | OpenDBC/Panda layout | Compatibility result |
| --- | --- | --- |
| EOP10 | External ignored trees in this checkout | Environment-dependent |
| EDP10 | Full tracked vendor trees | Current EDP ABI and BYD source are self-contained |
| NGP10 | `opendbc_repo` and `panda` git submodules | Newer upstream ABI |

The OpenDBC trees are not interchangeable at the current pins. EDP10's
`CarInterfaceBase.get_params()` carries a `dp_params` argument and its lateral
torque callback uses `LatControlInputs`; NGP10's submodule uses the upstream
signature. EDP10 also contains the BYD car and safety implementation, which is
not present in NGP10's OpenDBC submodule.

Replacing EDP10's tracked tree with the NGP10 gitlinks would therefore remove
BYD support and fail car-interface imports. A safe migration requires first
publishing an EDP-compatible OpenDBC fork/submodule that preserves the BYD
files and `dp_params` ABI, then switching the root repository to that pinned
commit. Until that compatibility fork exists, keep EDP10's vendor tree.
