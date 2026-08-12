# Process lifecycle hardening ported from Kommu KA2

**Source**: bukapilot commit `82e0d70ab` ("KA2: harden vision on/off
transitions and posenet startup"), authored by Kommu (2026-06-16, real KA2
production work — not inherited comma history, unlike
`DEVICE_FALLING_DETECTION.md`). Fixes "intermittent camerad zombies and
posenetInvalid during boot and rapid on/off cycles." Not RK3588-specific —
this is generic `system/manager` process-supervision logic — so kept
separate from `RKNN_PROVENANCE.md`.

KA2's fix touched 6 files: `locationd.py`, `selfdrived.py`, `camerad/main.cc`,
`hardwared.py`, `manager.py`, `process.py`. Ported what was safely portable;
explicitly did not port the rest.

## Ported

### `manager.py`: split onroad/offroad params publish timing

Was: a single `if started != started_prev: write_onroad_params(started,
params)`, for both transitions, at the same point in the loop (confirmed
EOP10 had this exact pattern — same bug shape as what KA2 found). Now:
`IsOnroad` publishes before `ensure_running` starts processes;
`IsOffroad` is deferred until *after* `ensure_running` has told processes to
stop — otherwise downstream consumers can observe `IsOffroad` while
`modeld`/camera producers are still tearing down.

### `process.py`: longer stop timeout for camera producer processes

Was: `join_process(self.proc, 5)` — a flat 5s kill-timeout for every managed
process, including the ones holding real camera device handles. Now: camera
producers get 15s (matching KA2's `CAMERAD_STOP_TIMEOUT_S`). This fork splits
comma's single `camerad` into `v4l2d` (MIPI CSI) + `uvcd` (USB) — both
covered via `CAMERA_PRODUCERS = frozenset({"v4l2d", "uvcd"})`.

## Explicitly not ported

- **Vision shutdown ordering** (KA2's `VISION_SHUTDOWN_ORDER` — stop
  consumers before the camera producer, with blocking joins in that order).
  KA2's version lists a flat chain (`encoderd, modeld, dmonitoringmodeld,
  controlsd, camerad`) because comma's stock process topology is a simple
  producer→consumer pipeline. This fork's is not: `modeld`, `gridd`,
  `stereod`, `monod`, `sided` all consume from `v4l2d`/`uvcd` via VisionIPC
  subscriptions, but `process_config.py`'s `should_run` predicates don't
  declare that dependency (they're all gated on `ignition_on`, not
  `camera_on`) — so the actual producer/consumer graph needed to order
  shutdown correctly isn't visible from static reading of the manager code.
  Guessing an order here risks reintroducing the same class of race in a
  different shape. Needs someone who knows the real VisionIPC topology, or a
  hardware session that can reproduce the zombie/race symptom to validate an
  ordering against.
- **`hardwared.py`: deferred CPU power-save on offroad.** EOP10's
  `hardwared.py` has no power-save mechanism at all (`should_pwrsave`,
  `set_power_save`, `screenBrightnessPercent`-driven CPU offlining — none of
  it exists here, not even in unfixed form). This isn't "missing KA2's fix,"
  it's a bigger, separate question of whether/how RK3588 CPU power
  management should work at the app level for this board at all — out of
  scope for a bug-port.
- **`camerad/main.cc`: wait for CPU6 before setting camerad affinity.**
  KA2-hardware-specific (assumes a particular RK3588 board's core layout and
  a single `camerad` process). This fork's camera producers are `v4l2d.cc`/
  `uvcd` — different processes, different affinity code, if any. Not
  investigated.
### `locationd.py`/`selfdrived.py` pieces — reviewed and partially ported

The rest of the same commit, reviewed in a follow-up pass:

**Ported** (generic algorithmic robustness, no hardware dependency):
- `locationd.py`: `posenet_stds` history now resets on a >2s gap in
  `cameraOdometry` messages (`CAMODO_GAP_RESET_S`) — a vision restart or
  dropped-frame gap otherwise leaves stale history that reads as a false
  `std_spike` on the next sample. `std_spike` itself is now gated on
  `camodo_count >= POSENET_STD_WARMUP_SAMPLES` (40 samples) so it can't fire
  before the rolling history has actually filled with real post-reset data.
- `selfdrived.py`: added a 30s grace period (`POSENET_ONROAD_GRACE_S`) after
  each onroad transition during which `posenetInvalid`/
  `locationdTemporaryError` are suppressed — locationd needs time to
  stabilize after boot or an offroad→onroad transition. Also gated the whole
  `livePose`-derived event block on `sm.alive['livePose'] and
  sm.valid['livePose']`, so it isn't evaluated against a stale/default
  message. `deviceFalling` (see `DEVICE_FALLING_DETECTION.md`) is
  deliberately **not** covered by the grace period — it's a real
  physical-event detector, not warmup noise, so suppressing it during
  startup would hide an actual fall.

**Not ported** (KA2-hardware-conditional in KA2's *own* code — their team
gated these behind `if KA2:`, which is itself a signal they're not safe to
apply unconditionally):
- The `init_timeout` change (6s → 30s for KA2, 15s for `TESTING_CLOSET`) and
  the `can_initialize` relaxation (allowing init to complete on timeout +
  cameras-ready + pose-ready, without `all_valid`). This alters
  safety-relevant startup gating — when the system considers itself
  initialized — based on KA2's own empirically-observed boot timing. This
  fork's boot-to-camera-ready latency on ExoPilot 01M is unknown (never run
  on hardware), so there's no evidence the same threshold applies, and
  loosening `can_initialize`'s conditions without that evidence is exactly
  the kind of change that shouldn't be guessed at.
- The `REPLAY and not KA2` camera-ignore branch — KA2-specific replay
  tooling behavior, not applicable here.

## Verified

`python3 -m py_compile` on all four edited files (`manager.py`, `process.py`,
`locationd.py`, `selfdrived.py`) — clean. Not runtime-tested: this is
process-supervision and locationd code, exercised continuously by every
onroad/offroad transition, and the race conditions it fixes are exactly the
kind that only show up under real repeated on/off
cycling on hardware — add to whatever hardware validation session covers
`RK3588_HARDWARE_VALIDATION_CHECKLIST.md`, even though it isn't itself
RK3588-specific.
