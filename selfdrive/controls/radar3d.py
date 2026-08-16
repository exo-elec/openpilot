#!/usr/bin/env python3
"""
radar3d — long-range UART radar producer — ExoPilot (RK3588/openpilot)

Publishes car.RadarData to cereal 'radar3d' at 20Hz. Replaces the old,
never-wired Continental ARS4-B/BrownPanda CAN radar path that used to be
produced from card.py — this vehicle has no forward OEM radar, only a 2D
blind-spot corner radar (radar2d, untouched by this daemon).

Consumers (unchanged, schema-compatible — this is a drop-in producer swap):
  selfdrive/controls/radard.py (RadarD)     -> radarState -> controlsd/ACC
  selfdrive/gridd/gridd.py (_fuse_radar3d)  -> stereoObjects (adjacent-lane,
    dRel > 12m, ego-lane skipped since ACC already owns that via radarState)

Hardware driver: hal.drivers.radar.radar3d (Radar3D class). Lives in the
`exopilot` repo's `hal` package, shared with the rest of the radar HAL
(BGT60TR13C, etc) — low-level sensor porting cannot live in this repo since
openpilot is a public repository and `exopilot` is not. Dev PC:
`pip3 install -e ../exopilot/hal`. On-device: the first-boot setup script
(exopilot/scripts/install/setup_rk3588.sh) installs it. This daemon only
owns the cereal producer loop and the RadarPoint conversion below; if `hal`
isn't importable it degrades to an idle no-op (logged once, not a crash),
same convention radar4d.py uses.

Sensor: 77GHz FMCW, 4T4R, 650MHz BW, up to 120m range, +/-45deg FOV. Does its
own onboard CFAR/AoA DSP and reports an already-processed target list over
UART (921600 8N1) -- no local FFT/CFAR needed on this side, just parsing.
See docs/eop/04_Integration/TC375_RADAR.md for the wire contract, sign
conventions, and bench-verify open items.
"""

import math
import time

import cereal.messaging as messaging
from openpilot.common.realtime import DT_MDL, Priority, Ratekeeper, config_realtime_process
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.swaglog import cloudlog

try:
    from hal.drivers.radar import Radar3D, Radar3DConfig
    from hal.platform.rk3588_pins import UART as RK3588_UART
    HAL_AVAILABLE = True
except ImportError:
    HAL_AVAILABLE = False

FRAME_RATE_HZ = 20
IDLE_POLL_S = 30.0   # how often to re-check nothing when hal/port is unavailable
UART_TIMEOUT_S = 0.05  # short so read_detections() doesn't stall the 20Hz cadence
# A track not refreshed within this many seconds is dropped -- coasts through
# a missed poll tick or two without flickering, but never holds a ghost target
# once the sensor has genuinely stopped reporting it.
STALE_TIMEOUT_S = 0.25


def detection_to_point(det) -> dict:
    """Convert a radar3d RadarDetection into car.RadarData.RadarPoint fields.

    Formula matches opendbc/car/gm/radar_interface.py -- the closest existing
    precedent in this codebase for "convert range+azimuth+range-rate into
    RadarPoint": dRel is the raw range (small-angle convention, not a full
    cos(az) projection), yRel = sin(azimuth) * range with left positive.
    det.azimuth_deg is already +left/-right (hal.drivers.radar.radar3d
    negates the vendor's raw +right/-left angle) so no further sign flip is
    needed here.

    aRel/yvRel are left NaN (not measured by this sensor, capnp comment:
    "valid if not NaN" -- same as GM/Ford/Toyota's own radar_interfaces).
    measured is always True: this sensor's onboard DSP only reports
    confirmed detections, it has no "estimated track" mode.
    """
    return {
        'trackId': det.track_id if det.track_id is not None else 0,
        'dRel': float(det.range_m),
        'yRel': float(math.sin(math.radians(det.azimuth_deg)) * det.range_m),
        'vRel': float(det.vel_mps),
        'aRel': float('nan'),
        'yvRel': float('nan'),
        'measured': True,
    }


class Radar3DD:
    """
    radar3d long-range radar producer daemon (Class-D pattern, matches Radar4DD).

    Paced by a Ratekeeper at FRAME_RATE_HZ, not by the sensor's own framing --
    unlike BGT60's IRQ-blocking read_fifo(), Radar3D.read_detections() is a
    short-timeout non-blocking-ish poll, so a software ratekeeper is the
    right pacing source here.
    """

    def __init__(self):
        self.pm = messaging.PubMaster(['radar3d'])
        self.radar: Radar3D | None = None
        self.running = False
        # trackId -> RadarPoint field values (plus _last_seen monotonic time)
        self._pts: dict[int, dict] = {}
        self._link_ok = False

        if not HAL_AVAILABLE:
            cloudlog.error(
                "radar3d: hal package not installed -- cannot drive the sensor. " +
                "Dev PC: pip3 install -e ../exopilot/hal. On-device: rerun " +
                "exopilot/scripts/install/setup_rk3588.sh."
            )
            return

        uart = RK3588_UART["RADAR3D"]
        config = Radar3DConfig(port=uart["device"], baud=uart["baud"], timeout_s=UART_TIMEOUT_S)
        self.radar = Radar3D(config)
        try:
            self.radar.open()
            self._link_ok = True
            cloudlog.info(f"radar3d: opened {uart['device']} @ {uart['baud']} baud")
        except Exception as e:
            cloudlog.error(f"radar3d: failed to open {uart['device']}: {e}")
            self.radar = None

    def _publish(self) -> None:
        msg = messaging.new_message('radar3d')
        rr = msg.radar3d
        rr.errors = [] if self._link_ok else ['fault']
        if self._link_ok:
            points = rr.init('points', len(self._pts))
            for i, fields in enumerate(self._pts.values()):
                points[i].trackId = fields['trackId']
                points[i].dRel = fields['dRel']
                points[i].yRel = fields['yRel']
                points[i].vRel = fields['vRel']
                points[i].aRel = fields['aRel']
                points[i].yvRel = fields['yvRel']
                points[i].measured = fields['measured']
        msg.valid = self._link_ok
        self.pm.send('radar3d', msg)

    def run(self):
        self.running = True

        if self.radar is None:
            # EOP wiring incomplete (hal missing or port failed to open) --
            # already logged in __init__. Idle rather than exit, so the
            # manager doesn't respawn-loop this process every restart interval.
            while self.running:
                time.sleep(IDLE_POLL_S)
            return

        rk = Ratekeeper(FRAME_RATE_HZ, print_delay_threshold=None)
        while self.running:
            now = time.monotonic()
            try:
                dets = self.radar.read_detections()
                self._link_ok = True
            except Exception as e:
                if self._link_ok:  # log once on the transition, not every tick
                    cloudlog.warning(f"radar3d: UART read failed: {e}")
                self._link_ok = False
                dets = []

            if self._link_ok:
                for det in dets:
                    if det.track_id is None:
                        continue
                    fields = detection_to_point(det)
                    fields['_last_seen'] = now
                    self._pts[det.track_id] = fields
                # drop stale tracks (not refreshed within STALE_TIMEOUT_S)
                for tid in [t for t, f in self._pts.items() if now - f['_last_seen'] > STALE_TIMEOUT_S]:
                    del self._pts[tid]
            else:
                self._pts.clear()  # never publish stale points beside a fault

            self._publish()
            rk.keep_time()

    def stop(self):
        self.running = False
        if self.radar is not None:
            self.radar.close()


def main():
    set_daemon_affinity("radar3d")
    config_realtime_process(DT_MDL, Priority.CTRL_LOW)
    daemon = Radar3DD()
    try:
        daemon.run()
    except KeyboardInterrupt:
        daemon.stop()


if __name__ == '__main__':
    main()
