# Device-falling detection — restored

**Not a Kommu/KA2 fix** — found while searching bukapilot's history for
KA2-specific material, but this is stock upstream comma.ai openpilot history
(commit `490ee5268`, "add fall filter and less FP on posenet", 2020-08-03,
authored by comma's own team). Any openpilot fork carries this in its git
log; it is not IP from the reference RK3588 production fork. Documented separately from `RKNN_PROVENANCE.md`
to keep that doc's KA2 attribution accurate.

## What was found

`selfdrive/selfdrived/events.py` already defines `EventName.deviceFalling`
with a comment: *"When the localizer detects an acceleration of more than
40 m/s^2 (~4G) we alert the driver the device might have fallen from the
windshield."* But nothing in the codebase ever triggered it — no
`self.events.add(EventName.deviceFalling)` anywhere, and `locationd.py` had
no accelerometer-magnitude fall detector. This fork's rewrite of `locationd`
kept the *other* half of the same 2020 commit (the `posenetOK` false-positive
threshold, `new_mean/old_mean > 4.0 and new_mean > 7.0`, present and correct
in the current `get_msg()`) but dropped the fall-detection half, leaving the
alert as dead scaffolding.

## What was restored

- `cereal/log.capnp`: added `LivePose.deviceStable @8 :Bool = true;`
  (verified the schema still compiles with `capnp compile -o- cereal/log.capnp`)
- `selfdrive/locationd/locationd.py`: `Localizer.device_fell` set on every
  accelerometer sample in `handle_log()`, published as `livePose.deviceStable`
  in `get_msg()`
- `selfdrive/selfdrived/selfdrived.py`: triggers `EventName.deviceFalling`
  when `not livePose.deviceStable`, mirroring the existing `posenetInvalid`
  check right next to it
- `common/mock/generators.py`: mock `livePose` message sets `deviceStable = True`

## Adapted, not copied verbatim

The original 2020 code checked a single axis
(`abs(sensor_reading.acceleration.v[0] - 10) > 40`, assuming a specific
mount/axis convention). This fork's `locationd.py` already reorders/negates
axes differently (`meas = np.array([-v[2], -v[1], -v[0]])`) for its own
Kalman filter convention, so replicating the single-axis check verbatim would
silently pick the wrong axis. Used vector magnitude instead —
`abs(np.linalg.norm(meas) - EARTH_G) > FALL_ACCEL_THRESHOLD` (`EARTH_G =
9.81`, `FALL_ACCEL_THRESHOLD = 40.0`, same numeric threshold as upstream,
which comma validated as producing "no false positives in 20k minutes of
driving") — orientation-independent, so it doesn't depend on getting the axis
convention right, at the cost of being slightly less sensitive to
directional falls than a well-tuned single-axis check would be.

## Verified

- `capnp compile -o- cereal/log.capnp` — schema compiles.
- `python3 -m py_compile` on all three edited `.py` files — clean.
- Grepped for any other code constructing/enumerating `LivePose` fields that
  might need the new field too — none found; the three files above are the
  complete change.
- **Not verified**: real accelerometer data. The threshold is upstream's own
  validated number, not re-derived here, but this fork's sensor pipeline
  (units, coordinate convention, any pre-filtering) was never checked against
  it beyond static code reading. Worth a note for whoever validates on real
  hardware — this isn't RK3588-specific, so it doesn't belong on the
  `RK3588_HARDWARE_VALIDATION_CHECKLIST.md`, but it does need a real drop
  test or equivalent before trusting the alert in the field.
