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

NOT connected to radard.py. radard.py handles the long-range UART radar
('radar3d') for ACC only.

Sensor: ~/radar/ESP32_RADAR — 4 corner-mounted nodes (FL/FR/RL/RR, ESP32-S3 +
BGT60TR13C), each streaming its own raw CFAR point cloud over WiFi/UDP
(port 47000). Each node does its own onboard CFAR/AoA; this daemon receives,
transforms corner-local detections into vehicle frame (using the same
corner-pose registry radar2d's BLE pipeline already shares —
radar4d_geometry.load_corner_poses()), then feeds the existing DBSCAN
cluster -> Kalman tracker pipeline below exactly as before — that pipeline
never cared which physical sensor produced a detection.

Hardware driver: hal.drivers.radar.radar4d (RadarCornerReceiver). Lives in
the `exopilot` repo's `hal` package — low-level sensor porting can't live in
this public repo. Dev PC: `pip3 install -e ../exopilot/hal`. On-device: the
first-boot setup script (exopilot/scripts/install/setup_rk3588.sh) installs it.

Previously drove a single BGT60TR13C directly over SPI (camera-bar mounted).
That path (hal.drivers.radar.bgt60tr13c, rk3588_pins SPI/GPIO entries,
radar4d_calibrate.py's factory intrinsics wizard, and the environment-
inference block below — precipitation/wiper/windshield-contamination) was
BGT60-specific: the wizard drove BGT60 directly, and environment inference
assumed a windshield/camera-bar-mounted sensor with glass behind it, which
is physically meaningless for bumper-mounted corner nodes. Removed along
with the SPI driver; the capnp fields those computed stay published as
neutral defaults (no schema change) rather than being deleted.
"""

import math
import time

import numpy as np

import cereal.messaging as messaging
from cereal import log
from openpilot.common.swaglog import cloudlog
from openpilot.common.transformations.orientation import rot_from_euler
from openpilot.selfdrive.controls.radar4d_geometry import (
    corner_local_to_vehicle_frame, load_corner_poses)
from openpilot.selfdrive.controls.radar4d_tracker import TrackManager
from openpilot.selfdrive.controls.radar4d_pointcloud import RadarPointcloudProcessor

try:
    from hal.drivers.radar import RadarCornerReceiver, dsp
    from hal.drivers.radar.radar4d import CORNER_UNKNOWN
    HAL_AVAILABLE = True
except ImportError:
    HAL_AVAILABLE = False
    dsp = None  # type: ignore
    CORNER_UNKNOWN = 0xFF

FRAME_RATE_HZ = 20
MAX_RANGE_M = 15.0   # close-range/gridd role; not extended toward radar3d's
                      # 15-200m ACC territory despite corner nodes' greater
                      # raw hardware range — deliberate, revisit only if asked
IDLE_POLL_S = 30.0   # how often to re-check nothing when hal is unavailable
UDP_TIMEOUT_S = 0.05 # short poll so recv_all() doesn't stall the tracker's dt pacing
# Tracker dt clamp: the loop is paced by the UDP receiver's short-timeout
# poll, not a hardware IRQ, but frame arrival still jitters (WiFi, 4
# independent senders). Clamp the measured dt to a sane band around the
# nominal 50 ms before handing it to the tracker.
FRAME_DT_MIN_S = 0.02
FRAME_DT_MAX_S = 0.15

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

# Corner mounting poses in vehicle frame (x_m forward, y_m left, yaw_deg CCW).
# Keys match ESP32_RADAR radar_corner_id_t: 0=FL, 1=FR, 2=RL, 3=RR. FALLBACK
# ONLY — __init__ prefers the shared registry (radar4d_geometry.load_corner_poses(),
# same one gridd.py's radar2d fusion reads) and falls back to this table only
# when that registry is absent or incomplete. PLACEHOLDER values pending
# extrinsic calibration — same honesty convention as gridd.py's own
# _R2D_CORNER_POSE fallback (these two tables are deliberately identical:
# same 4 physical bracket locations, whichever transport reads the sensor).
_PLACEHOLDER_CORNER_POSE = {
    0: ( 1.8,  0.8,   45.0),   # front-left
    1: ( 1.8, -0.8,  -45.0),   # front-right
    2: (-1.8,  0.8,  135.0),   # rear-left
    3: (-1.8, -0.8, -135.0),   # rear-right
}


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
    ESP32_RADAR corner-node 4D short-range radar daemon (Class-D pattern).

    Pacing is driven by RadarCornerReceiver.recv_all()'s short-timeout poll,
    not a Ratekeeper — matches the previous BGT60 daemon's shape (pacing
    tied to the sensor read call, not a separate sleep interval), just with
    a software poll timeout instead of a hardware IRQ block.
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
        self.receiver: RadarCornerReceiver | None = None
        self.running = False
        self._calib_from_device: np.ndarray | None = None
        self._v_ego_mps: float = 0.0
        self._v_ego_reliable: bool = False
        self._last_radar_xcheck_warn_t: float = 0.0
        # Environment inference capnp fields (precipProb/wiperOn/etc.) are no
        # longer computed for bumper-mounted corner nodes — always published
        # at these neutral defaults. See module docstring.
        self._precip_prob_ema: float = 0.0
        self._dropoff_streak: int = 0
        self._dropoff_dist_m: float = 0.0
        self._wiper_on: bool = False
        self._glass_contaminated: bool = False
        self._vision_blocked_streak: int = 0

        if not HAL_AVAILABLE:
            cloudlog.error(
                "radar4d: hal package not installed — cannot drive the corner-node " +
                "UDP receiver. Dev PC: pip3 install -e ../exopilot/hal. On-device: " +
                "rerun exopilot/scripts/install/setup_rk3588.sh."
            )
            return

        # Corner-pose registry: shared with radar2d's BLE pipeline
        # (gridd.py) — same physical bracket locations, same corner-id
        # numbering. All-or-nothing: falls back to a class-level placeholder
        # table only if the shared registry is entirely absent/malformed.
        self._corner_pose = load_corner_poses() or _PLACEHOLDER_CORNER_POSE
        if self._corner_pose is _PLACEHOLDER_CORNER_POSE:
            cloudlog.warning(
                "radar4d: shared corner-pose registry unavailable — using " +
                "placeholder mounting poses pending extrinsic calibration."
            )

        self.receiver = RadarCornerReceiver(timeout_s=UDP_TIMEOUT_S)
        try:
            self.receiver.open()
        except Exception as e:
            cloudlog.error(f"radar4d: failed to open UDP receiver: {e}")
            self.receiver = None

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
        # Environment inference (precip/wiper/contamination/dropoff) assumed
        # a windshield/camera-bar-mounted sensor with glass behind it —
        # physically meaningless for bumper-mounted corner nodes. Not
        # computed; published at these neutral defaults for schema
        # stability (no capnp change). See module docstring.
        msg.radar4d.precipProb = float(self._precip_prob_ema)
        msg.radar4d.wiperOn = bool(self._wiper_on)
        msg.radar4d.glassContaminated = bool(self._glass_contaminated)
        msg.radar4d.visionBlocked = bool(self._vision_blocked_streak)
        msg.radar4d.weatherSeverity = 0  # 0=clear
        msg.radar4d.dropOffHazard = bool(self._dropoff_streak)
        msg.radar4d.dropOffDistM = float(self._dropoff_dist_m)
        self.pm.send('radar4d', msg)

    def run(self):
        self.running = True

        if self.receiver is None:
            # EOPRadar4DEnabled=1 but hal isn't installed / UDP port failed
            # to open — already logged in __init__. Idle rather than exit,
            # so the manager doesn't respawn-loop this process every restart
            # interval.
            while self.running:
                time.sleep(IDLE_POLL_S)
            return

        last_frame_t: float | None = None
        while self.running:
            self.sm.update(0)
            self._update_ego_velocity()

            # Receive this tick's corner frames and transform each detection
            # from its node's local sensor frame into vehicle frame, merging
            # all corners into one flat list — downstream (clustering,
            # tracking, publish) never cared which physical sensor a
            # detection came from.
            detections: list = []
            for frame in self.receiver.recv_all():
                if frame.corner_id == CORNER_UNKNOWN or frame.corner_id not in self._corner_pose:
                    continue  # unresolved corner strap — cannot place without a pose
                pose = self._corner_pose[frame.corner_id]
                for det in frame.detections:
                    d_rel, y_rel = corner_local_to_vehicle_frame(det.range_m, det.azimuth_deg, pose)
                    det.range_m = math.hypot(d_rel, y_rel)
                    det.azimuth_deg = math.degrees(math.atan2(y_rel, d_rel))
                    detections.append(det)

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
                            f"radar4d: ego-speed mismatch — vehicle {self._v_ego_mps:.2f} m/s " +
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

    def stop(self):
        self.running = False
        if self.receiver is not None:
            self.receiver.close()


def main():
    daemon = Radar4DD()
    try:
        daemon.run()
    except KeyboardInterrupt:
        daemon.stop()


if __name__ == '__main__':
    main()
