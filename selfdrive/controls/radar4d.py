#!/usr/bin/env python3
"""
4D short-range radar daemon — ExoPilot 01M (RK3588/openpilot)

Publishes Custom.Radar4D to cereal 'radar4d' socket at ~20Hz.
Each point: range (m), azimuth (deg), elevation (deg), Doppler vRel (m/s),
SNR/RCS (dB), and existence probability (0-100, from confirm hit-streak).

gridd.py reads 'radar4d' and fuses with stereoObjects:
  - Velocity annotation: sets CameraObject.vRel for close-range stereo objects
  - Confidence boost: high RCS + existence probability elevates CameraObject.confidence
  - Elevation gate: rejects overhead/ground-bounce clutter a 2-D-only pipeline can't see

NOT connected to radard.py. radard.py handles car OEM CAN radar ('radar3d') for ACC only.

Hardware driver: hal.drivers.radar.bgt60tr13c (BGT60TR13C chip). Lives in the
`exopilot` repo's `hal` package — shared with VisionPilot (02M/RK3576), not
duplicated here. Dev PC: `pip3 install -e ../exopilot/hal`. On-device: the
first-boot setup script (exopilot/scripts/install/setup_rk3588.sh) installs it.
"""

import math
import os
import time

import numpy as np

import cereal.messaging as messaging
from cereal import log
from openpilot.common.swaglog import cloudlog
from openpilot.common.transformations.orientation import rot_from_euler
from openpilot.selfdrive.controls.radar4d_tracker import TrackManager
from openpilot.selfdrive.controls.radar4d_pointcloud import (
    RadarPointcloudProcessor, detections_to_points,
    estimate_precipitation, detect_dropoff, classify_weather_severity,
    detect_vision_blocked,
    WiperMotionDetector, WindshieldContaminationDetector,
)
from openpilot.system.hardware.hw import Paths

try:
    from hal.drivers.radar import BGT60TR13C, BGT60Config, RadarPhyStatus, RadarIntrinsics
    from hal.drivers.radar import load_radar_intrinsics
    from hal.drivers.radar import dsp
    from hal.platform.rk3588_pins import GPIO as RK3588_GPIO, SPI as RK3588_SPI
    HAL_AVAILABLE = True
except ImportError:
    HAL_AVAILABLE = False
    RadarIntrinsics = None  # type: ignore
    load_radar_intrinsics = None  # type: ignore
    dsp = None  # type: ignore

FRAME_RATE_HZ = 20
MAX_RANGE_M = 15.0   # BGT60 range gate; detections beyond this are noise at this BW
IDLE_POLL_S = 30.0   # how often to re-check nothing when hal is unavailable
# Tracker dt clamp: the loop is paced by the sensor IRQ, so the real frame
# period jitters and stretches when read_fifo() times out.  Clamp the measured
# dt to a sane band around the nominal 50 ms before handing it to the tracker.
FRAME_DT_MIN_S = 0.02
FRAME_DT_MAX_S = 0.15
INTRINSICS_PATH = os.path.join(Paths.eop_data_root(), "calibration", "radar_intrinsics.json")

# Ego-velocity compensation robustness guards.
# CAN-based vEgo can be off due to wheel slip, tyre-size errors, or scaling;
# rely on the Kalman-fused speed when available, fall back to CAN, and only
# label returns as static when the speed estimate is trustworthy.
V_EGO_MIN_MPS = 0.5          # below this, radial-velocity noise dominates
V_EGO_MAX_STD_MPS = 0.5      # reject Kalman speed if 1-sigma uncertainty too high
STATIC_THRESH_MPS = 1.0      # generous to avoid calling stationary objects dynamic
V_EGO_MAX_MPS = 80.0         # sanity cap (~290 km/h) — treat as invalid above this

# Radar-Doppler ego velocity (hal GNC/RANSAC estimator): an ego-speed source
# fully independent of CAN and the Kalman filter — immune to wheel slip and
# tyre-size errors.  Used as the compensation fallback when neither vehicle
# source is trustworthy, and cross-checked against them when they are.
RADAR_EGO_MIN_POINTS = 3     # hal estimator minimum (3D fit needs >= 3 points)
V_EGO_XCHECK_MPS = 1.0       # warn when vehicle and radar speeds disagree
V_EGO_XCHECK_WARN_S = 5.0    # throttle cross-check warnings

# Crossing-yaw ghost filter (Autoware radar_crossing_objects_noise_filter).
# During ego turns, stationary clutter is swept across the FOV and the tracker
# can report fast tangential "objects" that do not exist.  A track whose motion
# is fast AND nearly perpendicular to its bearing is treated as such a ghost.
GHOST_MIN_SPEED_MPS = 1.5       # slower movers are plausible road users — keep
GHOST_CROSSING_COS_MAX = 0.342  # |cos(crossing_yaw)| < cos(70°) → tangential

# Environment inference (radar4d_pointcloud): precipitation score is EMA-
# smoothed so one noisy frame cannot flip the slippery-weather signal; a
# drop-off hazard must persist for a few frames before it is published.
PRECIP_EMA_GAIN = 0.2
DROPOFF_CONFIRM_FRAMES = 2
VISION_BLOCKED_CONFIRM_FRAMES = 5  # ~0.25 s of total far-field loss


def _is_crossing_ghost(track) -> bool:
    """True if a confirmed track's velocity is fast and mostly tangential.

    crossing_yaw = heading - bearing; genuine radial movers have
    |cos(crossing_yaw)| ~ 1, ego-turn ghosts have it near 0.
    """
    speed = math.hypot(track.vx, track.vy)
    if speed <= GHOST_MIN_SPEED_MPS:
        return False
    if math.hypot(track.x, track.y) < 1e-3:
        return False
    crossing_yaw = math.atan2(track.vy, track.vx) - math.atan2(track.y, track.x)
    return abs(math.cos(crossing_yaw)) < GHOST_CROSSING_COS_MAX


class Radar4DD:
    """
    BGT60TR13C 4D short-range radar daemon (Class-D pattern).

    Pacing is driven by BGT60TR13C.read_fifo()'s IRQ-blocking per-chirp loop
    (hardware chirp/frame timing), not a Ratekeeper — the two would fight if
    stacked, since the actual frame rate is set by sensor IRQ timing, not by
    a software sleep interval.
    """

    def __init__(self):
        self.pm = messaging.PubMaster(['radar4d'])
        self.sm = messaging.SubMaster(['liveCalibration', 'carState', 'liveLocationKalman'])
        self.tracker = TrackManager()
        # Pointcloud → clustered objects pipeline (Autoware-inspired).
        # Kept separate from TrackManager so clustering/shape math is unit-testable.
        self.pointcloud_processor = RadarPointcloudProcessor(
            eps_m=0.6,
            min_samples=2,
            min_points=2,
            enable_ground_filter=True,
        )
        self.bgt60: "BGT60TR13C | None" = None
        self.running = False
        self._calib_from_device: np.ndarray | None = None
        self._v_ego_mps: float = 0.0
        self._v_ego_reliable: bool = False
        self._last_radar_xcheck_warn_t: float = 0.0
        # Environment inference state (precipitation EMA + drop-off streak)
        self._precip_prob_ema: float = 0.0
        self._dropoff_streak: int = 0
        self._dropoff_dist_m: float = 0.0
        self._wiper_detector = WiperMotionDetector()
        self._wiper_on: bool = False
        self._contam_detector = WindshieldContaminationDetector()
        self._glass_contaminated: bool = False
        self._vision_blocked_streak: int = 0

        if not HAL_AVAILABLE:
            cloudlog.error(
                "radar4d: hal package not installed — cannot drive BGT60TR13C. "
                "Dev PC: pip3 install -e ../exopilot/hal. On-device: rerun "
                "exopilot/scripts/install/setup_rk3588.sh."
            )
            return

        spi = RK3588_SPI["RADAR4D"]
        irq = RK3588_GPIO["BGT60_IRQ"]
        rst = RK3588_GPIO["BGT60_RST"]
        if not (irq.get("confirmed") and rst.get("confirmed")):
            # Refuse to drive unconfirmed GPIO lines on real hardware — an
            # unverified pin number could glitch/reset an unrelated peripheral
            # sharing that GPIO bank. Matches VisionPilot's radar4d_node.py,
            # which hard-fails the same way unless explicitly run in mock mode.
            cloudlog.error(
                "radar4d: BGT60_IRQ/BGT60_RST GPIO pins are UNCONFIRMED placeholders "
                "(hal.platform.rk3588_pins, pending LubanCat5 schematic verification) "
                "— refusing to drive real hardware with them. Confirm against the "
                "LubanCat5 schematic and flip 'confirmed' to True once verified."
            )
            return

        # Use the 20 Hz preset: cuts SPI FIFO volume to ~275 kB/frame so the
        # radar can keep up with the 20 Hz camera pipeline on RK3588.
        self.config = BGT60Config.for_20hz()
        self.config.frame_rate_hz = FRAME_RATE_HZ
        self.config.mti_alpha = 0.5
        self.config.use_gpu = True  # use Mali OpenCL CFAR when available

        # Velocity-resolution tuning: raise Doppler FFT length from 48 → 64
        # chirps.  Range resolution is already at the 5.5 GHz hardware limit
        # (2.7 cm); more chirps is the only lever left for velocity.  Cost is
        # ~33% more SPI volume (~370 kB/frame), which high_speed_spi offsets.
        # NOTE: actual chirp spacing is set by the software read_fifo loop
        # (~2 ms/chirp at 25 MHz), not the RTU register — verify on hardware
        # before trusting vel_mps absolute scale.
        self.config.n_chirps = 64
        self.config.high_speed_spi = True

        self.config.intrinsics = self._load_intrinsics()
        self.bgt60 = BGT60TR13C(
            spi_bus=spi["bus"], spi_device=spi["device"],
            irq_chip='/dev/gpiochip0', irq_line=irq["num"],
            rst_chip='/dev/gpiochip0', rst_line=rst["num"],
            config=self.config,
        )

    def _update_calibration(self) -> None:
        """Read liveCalibration and cache the device->calibrated rotation."""
        if self.sm.updated["liveCalibration"]:
            cal = self.sm["liveCalibration"]
            if cal.calStatus == log.LiveCalibrationData.Status.calibrated:
                rpy = np.array(cal.rpyCalib, dtype=np.float64)
                # rpyCalib is device-from-calibrated Euler; inverse rotates device-frame
                # radar vectors into the calibrated road frame used by modeld/gridd.
                self._calib_from_device = rot_from_euler(rpy).T
            else:
                self._calib_from_device = None

    def _update_ego_velocity(self) -> None:
        """
        Select the best ego-speed estimate for static/dynamic compensation.

        Order of preference:
          1. liveLocationKalman.velocityCalibrated.value[0] (fused IMU/GPS/CAN)
             if valid and low std.
          2. carState.vEgo (filtered CAN) if finite and in sane range.

        Sets _v_ego_mps and _v_ego_reliable. Compensation is skipped when
        unreliable so we err on the side of calling everything dynamic rather
        than mis-labeling stationary clutter as moving.
        """
        v = 0.0
        reliable = False

        if self.sm.updated["liveLocationKalman"]:
            llk = self.sm["liveLocationKalman"]
            vel_cal = llk.velocityCalibrated
            if vel_cal.valid and len(vel_cal.value) >= 1:
                vx = float(vel_cal.value[0])
                std_ok = (len(vel_cal.std) >= 1 and float(vel_cal.std[0]) <= V_EGO_MAX_STD_MPS)
                if std_ok and abs(vx) <= V_EGO_MAX_MPS:
                    v = vx
                    reliable = abs(vx) >= V_EGO_MIN_MPS

        if not reliable and self.sm.updated["carState"]:
            cs = self.sm["carState"]
            vx = float(cs.vEgo)
            if np.isfinite(vx) and abs(vx) <= V_EGO_MAX_MPS:
                v = vx
                reliable = abs(vx) >= V_EGO_MIN_MPS

        self._v_ego_mps = v
        self._v_ego_reliable = reliable

    @staticmethod
    def _estimate_radar_ego_velocity(detections: list) -> float | None:
        """Ego vx (m/s) from the radar's own Doppler, via the shared HAL.

        Prefers the GNC estimator (robust when many dynamic targets are in
        view — urban traffic), falls back to classic RANSAC-LSQ on older HAL.
        getattr-guarded so a stale hal install without ego_velocity support
        simply disables this path.  Must run on UNcompensated detections
        (their Doppler still carries the ego motion).  Returns the
        longitudinal estimate, or None when unavailable/undecided.
        """
        if not HAL_AVAILABLE or len(detections) < RADAR_EGO_MIN_POINTS:
            return None
        try:
            from hal.drivers import radar as hal_radar
            fn = getattr(hal_radar, 'estimate_ego_velocity_gnc', None) \
                or getattr(hal_radar, 'estimate_ego_velocity', None)
            if fn is None:
                return None
            est = fn(detections)
            return None if est is None else float(est.vx_mps)
        except Exception as e:
            cloudlog.debug(f"radar4d: radar ego-velocity estimation failed: {e}")
            return None

    @staticmethod
    def _load_intrinsics() -> "RadarIntrinsics | None":
        """Load the factory intrinsic LUT owned by the exopilot HAL.

        The same 4-band range + 4x8 bilinear az/el grid is used by the
        DISP_RADAR4D bench wizard and the ExoPilot HAL; persistence lives in
        hal.drivers.radar.intrinsics so every layer shares one format.
        Missing or malformed files fall back to identity (no correction).
        """
        if not HAL_AVAILABLE:
            return None
        cal = load_radar_intrinsics(INTRINSICS_PATH)
        if cal is not None:
            cloudlog.info(f"radar4d: loaded intrinsic calibration from {INTRINSICS_PATH}")
        return cal

    @staticmethod
    def _apply_calibration(track, calib_from_device: np.ndarray | None) -> tuple[float, float]:
        """Return (azimuth_deg, elevation_deg) rotated into calibrated frame."""
        if calib_from_device is None:
            return track.azimuth_deg, track.elevation_deg

        az = np.radians(track.azimuth_deg)
        el = np.radians(track.elevation_deg)
        # Device frame: x=forward, y=left, z=up; matches radar az/el convention.
        v_device = np.array([
            np.cos(el) * np.cos(az),
            np.cos(el) * np.sin(az),
            np.sin(el),
        ])
        v_calib = calib_from_device @ v_device
        x, y, z = v_calib
        cal_az = np.degrees(np.arctan2(y, x))
        cal_el = np.degrees(np.arctan2(z, np.hypot(x, y)))
        return float(cal_az), float(cal_el)

    def _publish(self, tracks: list) -> None:
        self._update_calibration()
        msg = messaging.new_message('radar4d')
        points = msg.radar4d.init('points', len(tracks))
        objects = msg.radar4d.init('objects', len(tracks))
        for i, t in enumerate(tracks):
            az, el = self._apply_calibration(t, self._calib_from_device)
            points[i].trackId = t.track_id
            points[i].rangM = float(t.range_m)
            points[i].azimuth = az
            points[i].elevation = el
            points[i].vRel = float(t.vel_mps)
            points[i].snrDb = float(t.snr_db)
            points[i].existenceProb = float(t.existence_prob)
            points[i].isStatic = bool(t.is_static)
            points[i].dynProp = {"stationary": 0, "moving": 1, "stopped": 2}.get(t.dyn_prop, 1)
            points[i].aRel = float(t.aRel)

            meta = t.metadata
            objects[i].trackId = t.track_id
            objects[i].rangM = float(t.range_m)
            objects[i].azimuth = az
            objects[i].elevation = el
            objects[i].vRel = float(t.vel_mps)
            objects[i].aRel = float(t.aRel)
            objects[i].snrDb = float(t.snr_db)
            objects[i].existenceProb = float(t.existence_prob)
            objects[i].isStatic = bool(t.is_static)
            objects[i].dynProp = points[i].dynProp
            objects[i].lengthM = float(meta.get("length_m", 0.0))
            objects[i].widthM = float(meta.get("width_m", 0.0))
            objects[i].heightM = float(meta.get("height_m", 0.0))
            objects[i].yawRad = float(meta.get("yaw_rad", 0.0))
            objects[i].pointCount = int(meta.get("point_count", 1))
        msg.radar4d.precipProb = float(self._precip_prob_ema)
        msg.radar4d.wiperOn = bool(self._wiper_on)
        msg.radar4d.glassContaminated = bool(self._glass_contaminated)
        msg.radar4d.visionBlocked = bool(self._vision_blocked_streak >= VISION_BLOCKED_CONFIRM_FRAMES)
        msg.radar4d.weatherSeverity = classify_weather_severity(
            self._precip_prob_ema, self._wiper_on,
            self._wiper_detector.sweep_rate_hz,
            self._glass_contaminated, self._contam_detector.attenuation_db,
        )
        hazard = self._dropoff_streak >= DROPOFF_CONFIRM_FRAMES
        msg.radar4d.dropOffHazard = bool(hazard)
        msg.radar4d.dropOffDistM = float(self._dropoff_dist_m) if hazard else 0.0
        self.pm.send('radar4d', msg)

    def run(self):
        self.running = True

        if self.bgt60 is None:
            # EOPRadar4DEnabled=1 but hal isn't installed — already logged in
            # __init__. Idle rather than exit, so the manager doesn't
            # respawn-loop this process every restart interval.
            while self.running:
                time.sleep(IDLE_POLL_S)
            return

        with self.bgt60:
            self.bgt60.reset()
            chip_id = self.bgt60.read_chip_id()
            if not chip_id:
                cloudlog.error("radar4d: chip ID=0 — check SPI wiring and TXS0108E level shifter")
                self.running = False
                return
            cloudlog.info(f"radar4d: BGT60TR13C online chip_id=0x{chip_id:06X}")
            self.bgt60.configure()

            last_status = RadarPhyStatus.OK
            last_frame_t: float | None = None
            while self.running:
                self.sm.update(0)
                self._update_ego_velocity()

                raw = self.bgt60.read_fifo(timeout_s=0.5)
                detections = self.bgt60.process(raw)
                # Radar-Doppler ego velocity (on UNcompensated detections):
                # fallback compensation source when neither liveLocationKalman
                # nor carState is trustworthy, and a cross-check on them when
                # they are (catches wheel slip / tyre-size errors).
                v_radar = self._estimate_radar_ego_velocity(detections)
                if v_radar is not None:
                    if not self._v_ego_reliable and abs(v_radar) >= V_EGO_MIN_MPS:
                        self._v_ego_mps = v_radar
                        self._v_ego_reliable = True
                    elif self._v_ego_reliable and abs(v_radar - self._v_ego_mps) > V_EGO_XCHECK_MPS:
                        now = time.monotonic()
                        if now - self._last_radar_xcheck_warn_t >= V_EGO_XCHECK_WARN_S:
                            cloudlog.warning(
                                f"radar4d: ego-speed mismatch — vehicle {self._v_ego_mps:.2f} m/s "
                                f"vs radar-Doppler {v_radar:.2f} m/s (wheel slip or tyre-size error?)"
                            )
                            self._last_radar_xcheck_warn_t = now
                # Ego-velocity compensation: label returns from stationary clutter
                # (guardrails, parked cars) versus real dynamic obstacles.
                # Only run when the speed estimate is trustworthy; otherwise leave
                # is_static=False so we never mis-label a stationary object as moving.
                if self._v_ego_reliable and dsp is not None:
                    dsp.compensate_ego_velocity(
                        detections, self._v_ego_mps, static_thresh_mps=STATIC_THRESH_MPS
                    )
                # Pointcloud pipeline: ground filter → Euclidean cluster → shape estimate.
                # The tracker now tracks cluster centroids, preserving object size/yaw metadata.
                clusters = self.pointcloud_processor.process(detections)
                # Environment inference on the RAW returns (pre-ground-filter):
                # precipitation clutter → slippery-weather signal for the
                # longitudinal accel gate; below-road returns → drop-off guard.
                raw_points = detections_to_points(detections)
                precip = estimate_precipitation(raw_points)
                self._precip_prob_ema += PRECIP_EMA_GAIN * (precip - self._precip_prob_ema)
                # Wiper sweeps: driver-confirmed wet road (stronger signal than
                # clutter statistics — the wiper only runs when it is wet).
                self._wiper_on = self._wiper_detector.update(raw_points)
                # Water film / washer fluid / snow on the glass: near-static
                # hot zone + far-target attenuation (60 GHz is water-sensitive).
                self._glass_contaminated = self._contam_detector.update(raw_points)
                # Total vision loss: film blocks every return past the near
                # field — "cannot see the road at all", escalates to takeover.
                if detect_vision_blocked(self._glass_contaminated, self._contam_detector.attenuation_db):
                    self._vision_blocked_streak += 1
                else:
                    self._vision_blocked_streak = 0
                hazard, hazard_dist = detect_dropoff(raw_points)
                if hazard:
                    self._dropoff_streak += 1
                    self._dropoff_dist_m = hazard_dist
                else:
                    self._dropoff_streak = 0
                metadata = [
                    {
                        "length_m": c.length_m,
                        "width_m": c.width_m,
                        "height_m": c.height_m,
                        "yaw_rad": c.yaw_rad,
                        "point_count": len(c.points),
                    }
                    for c in clusters
                ]
                # Measured frame period for the tracker prediction step; None on
                # the first frame so the tracker uses its nominal dt.
                now = time.monotonic()
                dt_s = None
                if last_frame_t is not None:
                    dt_s = min(max(now - last_frame_t, FRAME_DT_MIN_S), FRAME_DT_MAX_S)
                last_frame_t = now
                tracks = [t for t in self.tracker.update(clusters, metadata, dt_s=dt_s)
                          if t.range_m <= MAX_RANGE_M and not _is_crossing_ghost(t)]
                self._publish(tracks)

                status = self.bgt60.get_status()
                if status != last_status:
                    cloudlog.warning(f"radar4d: BGT60TR13C status changed to {status.value}")
                    last_status = status

    def stop(self):
        self.running = False


def main():
    daemon = Radar4DD()
    try:
        daemon.run()
    except KeyboardInterrupt:
        daemon.stop()


if __name__ == '__main__':
    main()
