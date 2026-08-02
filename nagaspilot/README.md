# NagasPilot NGP10

NGP10 is the comma 3-focused, minimized successor path derived from EOP10 and
kept feature-compatible with EDP10 where the hardware can support it.

Runtime features are integrated directly into the normal openpilot processes.
There is no parallel control daemon. Feature settings use the `ngp_` prefix,
while NGP-owned controller modules live under `nagaspilot/controls/` and enter
the upstream runtime through small integration hooks.

BrownPanda presents Tesla Model 3/Y-compatible party bus 0 and autopilot-party
bus 2. NGP10 treats it as one hardware-agnostic gateway interface and enables
the optional converted radar stream only when its required frames are present.

Start with [`docs/00_READ_ORDER.md`](docs/00_READ_ORDER.md).
