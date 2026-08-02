# TC375 BrownPanda radar contract

EOP10 receives the TC375 BrownPanda BYD-to-Tesla compatibility stream on
logical party bus 0. This is an EOP10 two-channel extension: upstream Tesla
radar interfaces normally listen on logical bus 1.

TC375 converts ten 64-byte BYD MVS4 CAN-FD object slots into the classical-CAN
Continental ARS4-B layout used by Tesla: status `0x401` followed by all forty
object A/B pairs `0x410..0x45F`. The final `0x45F` frame triggers publication.
This is not the industrial ARS408 object protocol.

Only the replay-supported BYD fields are measurements:

- track identity and confidence;
- longitudinal and lateral position;
- absolute target speed, converted to relative speed using fresh ego speed.

The overlapping BYD `ALEAD` field and unused `LAT_RELATED` field are not part
of the proven contract. TC375 transmits neutral Tesla `LongAccel` and
`LatSpeed`, and EOP10 ignores those wire values even if they are non-zero.
`radar3d` already estimates lead acceleration from absolute lead speed with its
Kalman filter; a second finite-difference estimate in `vehicled` would add noise
and duplicate that state estimator. No reliable lateral velocity can be
recovered from one unfiltered lateral-position difference, so it remains zero.

EOP10 requires a fresh, healthy `0x401`, a complete A/B pair, matching
`Index`/`Index2`, and `Tracked`, `Valid`, and `Meas`. The `0x45F` trigger must
have the full eight-byte classical-CAN payload. A missing or faulted status
suppresses every point, even if object frames still arrive. Loss of the entire
stream produces a rate-limited radar fault after 200 ms.

The source port reports offline CRC8/J1850 plus per-message DataID validation,
but does not ship the validator or the DataID table. TC375 therefore enforces
CAN ID, 64-byte DLC, trusted ECU-side origin, freshness, identity changes, and
physical plausibility, but it does not yet verify the two payload checksums.
Do not claim payload-integrity enforcement until the original validator and
DataIDs are recovered and reproduced against captures.

Before road use, replay known captures and complete HIL fault injection for
bad checksums, counters, truncated/missing slots, stale ego speed, track-ID
reuse, TX overflow, and loss/recovery of status and object frames.
