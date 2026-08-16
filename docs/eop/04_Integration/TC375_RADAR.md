# radar3d — long-range UART radar contract

*(Filename kept for history/link stability — this doc used to describe the
TC375 BrownPanda/Continental ARS4-B CAN radar contract. That design was
**never wired to real hardware**: `card.py`'s `RadarInterface` import was the
only consumer, and a second, more complete decoder
(`system/socketd/vehicle/tesla/continental_interface.py`) existed alongside
it and was never imported anywhere either. Both are removed. The vehicle's
only real forward-radar hardware is a long-range UART sensor; this doc now
describes that contract.)*

## Radar classification (current)

| Socket | Source | Range | Consumers | Purpose |
|--------|--------|-------|-----------|---------|
| `radar2d` | ESP32-S3 corner radar nodes, BLE tracked objects | 0-10m presence | `gridd.py` → `stereoObjects` | blind-spot / lane-change gating |
| `radar3d` | long-range UART radar (`selfdrive/controls/radar3d.py`) | 15-200m | `radard.py` → `radarState` (ACC), `gridd.py` → `stereoObjects` (adjacent-lane) | ACC lead tracking + forward merge/cut-in awareness |
| `radar4d` | BGT60TR13C (`radar4d.py`, SPI, camera-bar mounted) | 0-15m | `gridd.py` → `stereoObjects` | close-range maneuvering |

`radar3d` is the only one of the three that feeds two independent
consumers — see "Two consumers, one producer" below.

## Driver ownership — shared with the rest of the radar HAL

Low-level sensor porting lives in the `exopilot` repo's `hal` package
(`hal.drivers.radar.radar3d`), **not** in this repo — openpilot is a public
repository and `exopilot` is not, so hardware-specific driver code cannot
live here. This repo only owns the cereal producer daemon:

```
../exopilot/hal/hal/drivers/radar/radar3d.py   ← UART link + line parser (Radar3D, Radar3DConfig)
../exopilot/hal/hal/drivers/radar/bgt60tr13c.py ← shared RadarDetection dataclass (range_m, vel_mps,
                                                    azimuth_deg, elevation_deg, snr_db, is_static, track_id)
../exopilot/hal/hal/platform/rk3588_pins.py     ← UART["RADAR3D"] = /dev/ttyUSB1 @ 921600

selfdrive/controls/radar3d.py  ← cereal daemon (Radar3DD, process name "radar3d")
                                   publishes car.RadarData on the 'radar3d' socket
selfdrive/controls/radard.py   ← RadarD, upstream-openpilot-style camera+radar
                                   lead fusion (renamed from this repo's old
                                   radar3d.py — radar3d.py itself is now the
                                   sensor producer, not the fusion daemon)
```

Requires `hal` installed: on-device, `exopilot/scripts/install/setup_rk3588.sh`
does this during first-boot BSP setup; on a dev PC, `pip3 install -e ../exopilot/hal`.
`radar3d.py` degrades to an idle no-op (logged once, not a crash) if `hal`
isn't importable or the UART port fails to open — same convention
`radar4d.py` uses for its own hal-missing case.

## Two consumers, one producer

Unlike `radar2d`/`radar4d` (each feeding only `gridd.py`), `radar3d` feeds
**two independent consumers** of the same `car.RadarData` points:

1. **`radard.py`** (`RadarD`) — the original openpilot camera+radar lead
   fusion: matches `radar3d` tracks against `modelV2` vision leads, Kalman-
   filters lead acceleration, publishes `radarState` for controlsd/ACC.
   Unmodified upstream logic (only the file/module name changed — see
   below).
2. **`gridd.py`** (`_fuse_radar3d`) — folds `radar3d` points into
   `stereoObjects` for **forward adjacent-lane** awareness (merging traffic,
   cut-in), range > 12m (radar4d owns closer range). Deliberately skips
   ego-lane points ("ACC owns same-lane via `radarState`") so the two
   consumers complement rather than duplicate each other.

Both simply subscribe to the `radar3d` cereal socket — `radar3d.py` doesn't
need to know or care who's listening.

## Why `radar3d.py` and `radard.py` are two separate daemons/files

Before this change, the *only* thing that decoded car OEM radar CAN frames
was `card.py` (`system/socketd/vehicle/car/card.py`) — reasonable when the
radar rode the same Tesla-party-bus CAN stream `card.py` already parses for
carState. `radar3d.py` (the file now called `radard.py`) was purely a
*consumer* of whatever `card.py` published.

The long-range sensor here is **not on the CAN bus** — it's an independent
UART link. There's no structural reason to route it through `card.py`'s
100Hz safety-critical CAN loop, and doing so would couple an unrelated
serial link's timing into that loop. So `radar3d.py` is now a small,
standalone producer daemon (`Radar3DD`, Class-D pattern, matches
`Radar4DD`) that owns the UART link directly and publishes `radar3d` itself
— `card.py` no longer touches radar at all.

## Wire protocol (UART, 921600 8N1)

The sensor runs its own onboard range-Doppler/CFAR/AoA DSP and reports an
already-processed target list — no raw ADC/FFT/CFAR on the host side. One
text line per target per frame:

```
<id> P<snr_db> R<range_m> V<vel_mps> A<azimuth_deg> E<elevation_deg>
```

Example: `01 P 42.75 R 4.32 V 0.00 A 27.50 E 30`

`hal.drivers.radar.radar3d._parse_line()` scans the whole line for each
`<letter><optional colon/space><number>` tag independent of delimiter
(space- or comma-separated, both seen in vendor material) rather than
splitting on a fixed delimiter — robust to the vendor's documentation
itself being inconsistent about which one is used. Block/header lines
(`BK`/`AK`/`FT`), separator/comment lines (`---`, `#`), blank lines, and
lines missing any of the five required tags are skipped, not errored.

### Sign conventions (BENCH-VERIFY on real hardware)

| Field | Vendor convention | `RadarDetection` convention | Conversion |
|---|---|---|---|
| `V` (velocity) | away = +, closer = − | same (− = approaching) | no flip |
| `A` (azimuth) | +right / −left (inferred from the vendor's flight-controller adaptation doc's CAN-ID direction split) | +left / −right | negated: `azimuth_deg = -A` |
| `E` (elevation) | not stated by vendor | 0=boresight, +up | passed through as-is, **unverified** |

Both the azimuth negation and the elevation pass-through are flagged
`BENCH-VERIFY` in the driver source — confirm against a known-angle target
before trusting sign on real hardware, same discipline `bgt60_radar.md`
applies to BGT60's own AoA channel mapping.

### `car.RadarData.RadarPoint` field mapping (`selfdrive/controls/radar3d.py::detection_to_point`)

Formula matches `opendbc/car/gm/radar_interface.py` — the closest existing
in-repo precedent for "convert range+azimuth+range-rate into `RadarPoint`":

```python
dRel = range_m                                  # small-angle convention, not cos(az) projection
yRel = sin(radians(azimuth_deg)) * range_m       # left positive
vRel = vel_mps                                   # no flip, see table above
aRel = yvRel = NaN                               # not measured by this sensor (same as GM/Ford/Toyota)
measured = True                                  # onboard DSP only reports confirmed detections, no "estimated" mode
trackId = <vendor's own per-target ID>           # passed through directly; RadarD/gridd handle
                                                  # cross-frame continuity downstream, same as any
                                                  # other opendbc radar_interface
```

No capnp schema changes were needed — `car.RadarData.RadarPoint` already
covered everything this sensor provides.

### Liveness / fault handling

`radar3d.py` tracks per-target last-seen time; a track not refreshed within
250ms (~5 ticks at the daemon's 20Hz `Ratekeeper` cadence) is dropped —
coasts through a missed poll tick without flickering, never holds a ghost
target once the sensor has genuinely stopped reporting it. If the UART read
itself fails (link down), `radar3d` publishes `errors=['fault']` with all
points cleared — never stale points beside a fault, matching the pattern
the old (now-deleted) `continental_interface.py` used for its own CAN-link
liveness check.

POST: ~10s self-test + ~1 minute full boot per the vendor manual. The
driver returns `[]` during that window rather than raising, so a cold-boot
empty frame isn't mistaken for a fault.

## Verification

```bash
cereal_print radar3d       # long-range radar points at ~20Hz
cereal_print radarState    # ACC lead state, fed by radard.py from the above
```

No hardware needed for parser/conversion correctness:
`../exopilot/hal/tests/test_radar3d.py` (line parsing, buffering across
split reads) and `selfdrive/controls/tests/test_radar3d.py`
(detection→RadarPoint conversion, sign conventions).

## Open items

- [ ] Bench-verify azimuth sign (`-A`) against a known-angle target.
- [ ] Bench-verify elevation sign/convention — vendor manual doesn't state one.
- [ ] Confirm POST/boot timing (~10s self-test + ~1min full boot per the
      vendor manual) doesn't trip any downstream "sensor missing" heuristic
      on cold boot.
- [ ] Confirm vendor target-ID stability/reuse behavior across
      appear/disappear cycles on real hardware — `radard.py`'s Kalman
      tracks and `gridd.py`'s object association both assume a track ID
      isn't reused for a different physical target within a short window.
