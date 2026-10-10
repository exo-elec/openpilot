# NGP10 baseline refresh and descendant validation

The independent EDP10 review is implemented on NGP10. EOP10 is rebased on that
baseline; 01M is rebased on EOP10 and 02M on 01M. EDP10 is unchanged.

86 portable Python files under controls, runtime and mapd are inherited byte for
byte. EOP keeps its own native Tesla-party vehicle daemon and hardware adapters; it has no external OpenDBC dependency. NGP's
new native settings and device hooks belong to the NGP runtime; inheriting helper
source does not claim that the separate EOP device adapters implement every NGP
convenience. Existing EOP feature switches and hardware-specific behavior remain.
The 01M/02M UI source remains identical; only build/display profiles differ.

The BrownPanda synthetic radar adapter has been removed from the baseline and
all descendants. UART radar3d and corner radar2d retain their separate producers. EOP manager also migrates saved
origin aliases before defaults and its pathd accepts typed scale-switch booleans.

Validation: NGP 86 focused tests and native Qt translation-unit syntax checks;
EOP 221 shared-policy/settings/adaptive-UI tests. Shared-source lineage checks
pass. This is host validation, not a full device build or hardware/road test.
