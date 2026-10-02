# Node 6 — EDP10 net-new: BYD car port + `dp_tja` + planner hooks

## BYD Atto 3 car port (`opendbc_repo/opendbc/car/byd/*`) — ✅ pass, unusually rigorous

Reviewed `carstate.py`, `carcontroller.py`, `interface.py`, `values.py`,
`bydcan.py` (ported from `shemps/byd-atto3-openpilot-port`).

- `ret.dashcamOnly = True` + `SafetyModel.noOutput` in `interface.py`: this
  port **cannot** send CAN commands to a real car today — it's dashcam-only
  by explicit design, matching the project's existing pattern for
  unvalidated car ports (per memory: comparable to the Tesla port's
  `dashcamOnly` status). This substantially lowers real-world risk even
  though Node 2's safety review of `byd.h` still applies to whenever it's
  flipped live.
  - `carcontroller.py`'s class docstring is explicit about exactly what's
    missing before this could go live: `byd.h`'s `BYD_TX_MSGS` whitelist
    doesn't include `0x32E` (longitudinal) yet, so even setting
    `openpilotLongitudinalControl` would fail closed at panda's generic
    TX check, not silently transmit. This is the right kind of
    self-documented gap.
- `get_can_parsers` reusing `DBC[CP.carFingerprint][Bus.pt]` for both the
  `Bus.pt` and `Bus.cam` parsers looked suspicious at first read but matches
  the exact precedent already used by upstream Mazda's `carstate.py` (single
  DBC file, multiple physical buses) — not a bug.
- Cross-checked signal decode against the DBC and against `byd.h`'s RX hook:
  `gasPressed` threshold (10 raw), `cruiseState.enabled` (`acc_state in (3,5)`)
  match `byd.h`'s `pcm_cruise_check` exactly.
- `CarControllerParams.ANGLE_LIMITS`/`ZONE_MAX_ANGLE_*`/`ZONE_MAX_RATE_*`
  are explicitly documented as 80% of `byd.h`'s panda-side backstop
  ("app layer tighter, panda layer looser" — consistent two-layer defense
  in depth), with the physics reasoning spelled out inline. Consistent with
  Node 2's findings.
- Every uncited/unverified number in the port (steering angle max, slip
  factor, 0x316/0x32E behavior) is flagged as such in comments rather than
  presented as validated. No fabricated precision found.

**Verdict: no bugs. This is the most carefully self-documented code seen in
this audit so far** — every non-obvious constant cites its source and every
known gap is called out rather than papered over.

## `dp_tja.py` / `TrafficJamAssist` — 🟡 finding: no user-facing toggle, unlike every sibling feature

`self.tja = TrafficJamAssist(self.dt)` is unconditionally instantiated and
`tja_result = self.tja.update(...)` runs every planner cycle with **no
`dp_flags` gate** — contrast with `ACM`/`ACM_DOWNHILL`/`AEM`/`BRSC` in the
same file, which are all gated behind a `params.get_bool(...)` flag checked
in `plannerd.py` before being OR'd into `dp_flags`. `dp_panel.cc`'s
`add_longitudinal_toggles()` has entries for AEM and the newly-added BRSC,
but none for TJA — it doesn't appear in the settings UI at all.

Checked whether this is EDP10-specific: **no** — `NGP10`'s `params_keys.h`
diff (Node 5) adds 16 new `ngp_*` keys covering ALCC, LCA, road-edge
detection, BRSC, and 9 separate `ngp_lon_dlon_*` sub-toggles, but **no**
`ngp_lon_tja`/`ngp_lat_tja` key either. The gap is consistent across both
branches that have a TJA implementation, which weakens the "accidental
omission" reading and strengthens "deliberate always-on behavior" — TJA's
own docstring says it "can only reduce positive acceleration and positive
jerk," i.e. it's fail-safe in direction (never makes the car more
aggressive), which is a defensible reason to treat it like FCW/AEB rather
than a comfort toggle.

**Still worth a decision**, because right now there is genuinely no way for
a user to know this feature exists or to turn it off if its behavior near a
lead car (holding accel back for 1.5s after any track-ID change or distance
jump, capping accel to 0.55x for a full second of "stable" tracking even in
clean conditions) feels wrong to them — and `TJA.md` doesn't describe this
implementation at all (see Node 4). If the always-on decision is
intentional, it should be stated in `TJA.md`; if not, it needs the same
toggle treatment as its siblings.

## `longitudinal_planner.py` / `plannerd.py` wiring — ✅ pass

- BRSC is fed unconditionally (`self.brsc.update(...)` runs regardless of
  `brsc_enabled`) so its internal EMA/hold state stays warm, but only
  *applied* to `v_cruise`/`output_a_target` when `brsc_enabled` — correct
  separation of "always compute" vs "conditionally apply," and matches the
  pattern the BRSC feature doc describes.
- `BRSC_MIN_V_EGO`/`BRSC_MIN_SPEED_MS` floor prevents BRSC from crawling the
  car to a stop on a long rough stretch — sane bound.
- Accel-scale composition order (ACM/AEM's own adjustments → TJA →
  BRSC → jerk-limited by `tja_result.jerk_scale`) is a `min()` chain, so
  each layer can only tighten, never loosen, the previous layer's bound —
  correct composition for independent safety/comfort caps.
- `plannerd.py`'s `SubMaster` adds `'accelerometer'` with
  `ignore_alive=['accelerometer']` — correct, since a car without the
  accelerometer service (or one where it drops out) shouldn't stall the
  planner's `poll='modelV2'` loop; `sm.valid.get('accelerometer', False)`
  gate in the planner is checked before reading `az`, so a missing service
  degrades to BRSC simply never activating rather than a crash.

---

**Node status: done.** 1 clean pass (BYD car port, exceptionally
well-documented), 1 clean pass (planner wiring), 1 finding requiring a
decision (TJA has no toggle, consistent across both branches that implement
it — likely intentional but undocumented as such).
