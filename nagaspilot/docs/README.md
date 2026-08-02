# NGP10 Documentation Index

## Recommended reading order

1. `PROJECT_CONCEPT.md` — product, hardware, and vehicle boundaries.
2. `NGP10_MINIMAL_EOP_PARITY.md` — what “surpass EDP10” means.
3. `NGP10_FEATURE_MATRIX.md` — one-page module/status/ownership table.
4. `NGP10_COMMA3_COMPLETE_PORT_PLAN.md` — complete port and promotion gates.
5. `NGP10_EOP_TRANSITION_PLAN.md` — current implementation status.
6. `NGP10_COMMA3_SECOND_PASS_AUDIT.md` — additional EDP10/EOP10 candidates.
7. `NGP10_EOP10_COMMA3_IMPLEMENTATION_PLAN.md` — original detailed source
   audit; retained for history and rationale.

## Source-code map

| Area | Location |
| --- | --- |
| Composition and manifest | `selfdrive/controls/lib/ngp_suite.py` |
| Control/safety proposals | `selfdrive/controls/lib/ngp_*.py` |
| Runtime diagnostics | `selfdrive/ngpshadowd.py` |
| Capability, BEV, overlays | `selfdrive/gridd/` |
| Single-camera detection contract | `selfdrive/monod/` |
| SOC path proposal | `selfdrive/pathd/` |
| Optional route curvature | `selfdrive/mapd/` |
| Adaptive telemetry | `selfdrive/adaptd/` |
| Trip diagnostics | `selfdrive/tripd/` |
| Cereal diagnostic schema | `cereal/custom.capnp` (`NGPState`) |

## Status vocabulary

- **Shadow**: runs or can run, publishes diagnostics, no command consumer.
- **Proposal**: pure result available, not connected to planning or control.
- **External**: owned by TC275, upstream OpenDBC, or Panda.
- **Excluded**: intentionally incompatible with comma 3 or the minimized scope.

No feature in this directory has control authority until the documented replay,
resource, gateway, Panda, stationary, and hardware-in-the-loop gates pass.
