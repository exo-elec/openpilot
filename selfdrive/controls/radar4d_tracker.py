"""
Track management for radar4d — Cartesian alpha-beta-gamma tracker with
confirm/drop hysteresis and ARS-style dynamic property classification.

Designed for dense, chaotic traffic (India / Vietnam / Southeast Asia):
  - Cartesian state and gating prevent the angular distortion of polar
    nearest-neighbour association, so a scooter 2 m ahead and 1 m left is
    gated correctly regardless of range.
  - Alpha-beta-gamma smoothing estimates longitudinal/lateral acceleration,
    giving downstream longitudinal control a smoother vRel / aRel signal
    for comfortable braking and acceleration.
  - Per-class-aware gating and existence probability keep ID switches low
    when many small objects (scooters, pedestrians, bicycles) are close.

The public interface is unchanged: TrackManager.update(detections) returns
confirmed Track objects. Track still exposes range_m / azimuth_deg /
elevation_deg / vel_mps for radar4d._publish(), but now also carries
Cartesian position/velocity/acceleration and aRel / lateral acceleration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

CLUSTER_EPS_M = 0.5    # clustering radius in range-projected space (tune on hardware)
CLUSTER_MIN = 2         # minimum peak count to form a cluster

MAX_ASSOC_M = 2.0       # default Cartesian match radius in x/y (m)
CONFIRM_HITS = 3        # consecutive matched frames before a track is published
DROP_MISSES = 3         # consecutive missed frames before a tentative/confirmed track is dropped

EXISTENCE_SPAWN = 40.0
EXISTENCE_HIT_STEP = 25.0
EXISTENCE_MISS_STEP = 20.0
EXISTENCE_MAX = 100.0

DYN_PROP_SPEED_THRESHOLD_MPS = 0.5  # radial-velocity magnitude

# Alpha-beta-gamma gains for 20 Hz update.  These are tuned for a roughly
# constant-acceleration target with moderate process noise; visionpilot's
# tracker uses a very similar set.  Gamma is small so acceleration is mostly
# smoothed, not driven by single-frame noise.
ALPHA = 0.45
BETA = 0.20
GAMMA = 0.03

# Per-class Cartesian association gates (meters in x/y plane).  Wider for
# large vehicles, tighter for pedestrians / scooters so they do not steal
# each other's IDs in dense traffic.  Borrowed from Autoware's data-association
# matrix, scaled to our sparse short-range BGT60TR13C radar.
GATES_M = {
    "default": 2.0,
    "vehicle": 3.0,      # car / truck / bus — large, slower manoeuvring
    "motorcycle": 1.2,   # scooter / motorcycle / bicycle
    "person": 0.8,       # pedestrian
}

# Temporal shape filtering (Autoware shape_estimation UnstableShapeFilter +
# vehicle_tracker normalizeYaw).  Per-frame L-shape/PCA fits on 2-5 point
# clusters are flickery, so the track smooths length/width/height/yaw with a
# dual-rate EMA — a small gain when the fit jumps (likely a bad frame), a
# larger one when it agrees — clamped to plausible vehicle bounds.  Yaw is
# first unwrapped across the 180-degree L-shape ambiguity so a flipped fit
# never pulls the average.
SHAPE_EMA_GAIN_STABLE = 0.5
SHAPE_EMA_GAIN_JUMP = 0.2
SHAPE_JUMP_REL_THRESH = 0.5    # relative change above this counts as a jump
SHAPE_YAW_EMA_GAIN = 0.3
SHAPE_MIN_DIM_M = 0.1
SHAPE_MAX_LENGTH_M = 12.0
SHAPE_MAX_WIDTH_M = 3.5
SHAPE_MAX_HEIGHT_M = 3.5


def _wrap_pi(a: float) -> float:
    return math.atan2(math.sin(a), math.cos(a))


def _clamp_dim(v: float, max_m: float) -> float:
    return min(max(v, SHAPE_MIN_DIM_M), max_m)


def _merge_shape_metadata(old: dict, new: dict) -> dict:
    """Blend a track's stored cluster shape with the newest fit.

    First observation adopts the (clamped) measurement directly; afterwards
    dimensions run through the dual-rate EMA and yaw through the
    pi-ambiguity-normalized EMA.  point_count and any other keys always
    pass through from the newest fit.
    """
    merged = dict(new)
    dims = (("length_m", SHAPE_MAX_LENGTH_M),
            ("width_m", SHAPE_MAX_WIDTH_M),
            ("height_m", SHAPE_MAX_HEIGHT_M))
    if not old:
        for key, max_m in dims:
            merged[key] = _clamp_dim(float(new.get(key, SHAPE_MIN_DIM_M)), max_m)
        return merged
    for key, max_m in dims:
        v_new = _clamp_dim(float(new.get(key, SHAPE_MIN_DIM_M)), max_m)
        v_old = float(old.get(key, 0.0))
        if v_old <= 0.0:
            merged[key] = v_new
            continue
        rel = abs(v_new - v_old) / max(v_old, 1e-3)
        gain = SHAPE_EMA_GAIN_JUMP if rel > SHAPE_JUMP_REL_THRESH else SHAPE_EMA_GAIN_STABLE
        merged[key] = v_old + gain * (v_new - v_old)
    yaw_old = float(old.get("yaw_rad", 0.0))
    yaw_new = float(new.get("yaw_rad", 0.0))
    d = _wrap_pi(yaw_new - yaw_old)
    if abs(d) > math.pi / 2.0:
        # 180-degree-flipped fit: flip it back before averaging (normalizeYaw).
        yaw_new = _wrap_pi(yaw_new + math.pi)
        d = _wrap_pi(yaw_new - yaw_old)
    merged["yaw_rad"] = _wrap_pi(yaw_old + SHAPE_YAW_EMA_GAIN * d)
    return merged


def _det_to_cartesian(det) -> tuple[float, float, float]:
    """Spherical (range_m, azimuth_deg, elevation_deg) -> (x, y, z).

    Convention matches gridd: x = forward, y = left, z = up.
    """
    r = float(det.range_m)
    az = math.radians(float(det.azimuth_deg))
    el = math.radians(float(det.elevation_deg))
    cos_el = math.cos(el)
    x = r * cos_el * math.cos(az)
    y = r * cos_el * math.sin(az)
    z = r * math.sin(el)
    return x, y, z


def _cartesian_to_spherical(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Return (range_m, azimuth_deg, elevation_deg)."""
    r = math.hypot(x, math.hypot(y, z))
    az = math.degrees(math.atan2(y, x))
    el = math.degrees(math.atan2(z, math.hypot(x, y))) if r > 0 else 0.0
    return r, az, el


def _radial_velocity(x: float, y: float, z: float,
                     vx: float, vy: float, vz: float) -> float:
    """Filtered radial (Doppler-direction) velocity from a Cartesian state.

    Sign convention matches raw Doppler: negative = approaching.  Returns 0.0
    for a degenerate near-zero range where the radial direction is undefined.
    """
    r = math.sqrt(x * x + y * y + z * z)
    if r < 1e-3:
        return 0.0
    return (x * vx + y * vy + z * vz) / r


def _class_gate_m(det) -> float:
    """Best-guess association gate from detection size / RCS / SNR.

    We do not have a semantic class at the radar stage, so use SNR as a
    weak size proxy: high-SNR returns are more likely vehicles, low-SNR
    returns are more likely pedestrians / scooters.
    """
    snr = getattr(det, "snr_db", 20.0)
    if snr >= 25.0:
        return GATES_M["vehicle"]
    if snr >= 18.0:
        return GATES_M["motorcycle"]
    return GATES_M["person"]


def cluster(detections: list) -> list:
    """
    Greedy SNR-weighted clustering in polar (range_m, azimuth-projected)
    space. Returns SNR-weighted centroids as RadarDetection-shaped objects
    (keeps full polar representation + elevation, no Cartesian collapse).
    """
    if not detections:
        return []

    groups, used = [], [False] * len(detections)

    for i, seed in enumerate(detections):
        if used[i]:
            continue
        group = [seed]
        used[i] = True
        for j in range(i + 1, len(detections)):
            if used[j]:
                continue
            dr = detections[j].range_m - seed.range_m
            da_m = math.radians(abs(detections[j].azimuth_deg - seed.azimuth_deg)) * seed.range_m
            if math.hypot(dr, da_m) < CLUSTER_EPS_M:
                group.append(detections[j])
                used[j] = True
        groups.append(group)

    centroids = []
    for group in groups:
        if len(group) < CLUSTER_MIN:
            continue
        w = sum(d.snr_db for d in group)
        if w <= 0:
            w = len(group)
        cls = type(group[0])
        static_weight = sum(d.is_static * d.snr_db for d in group) / w
        centroids.append(cls(
            range_m=sum(d.range_m * d.snr_db for d in group) / w,
            azimuth_deg=sum(d.azimuth_deg * d.snr_db for d in group) / w,
            elevation_deg=sum(d.elevation_deg * d.snr_db for d in group) / w,
            vel_mps=sum(d.vel_mps * d.snr_db for d in group) / w,
            snr_db=max(d.snr_db for d in group),   # peak SNR = best RCS proxy
            is_static=static_weight > 0.5,
        ))
    return centroids


@dataclass
class Track:
    track_id: int
    # Cartesian state, convention: x=forward, y=left, z=up
    x: float
    y: float
    z: float
    vx: float
    vy: float
    vz: float
    ax: float
    ay: float
    az: float
    # Polar view of the state (for publishing).  Under KalmanTrackManager these
    # are filter outputs (range/az/el from the Cartesian state, vel_mps the
    # smoothed radial velocity); under ABGTrackManager vel_mps is raw Doppler.
    range_m: float
    azimuth_deg: float
    elevation_deg: float
    vel_mps: float
    snr_db: float
    # Lifecycle
    hit_streak: int = 1
    miss_streak: int = 0
    state: Literal["tentative", "confirmed"] = "tentative"
    existence_prob: float = field(default=EXISTENCE_SPAWN)
    is_static: bool = False
    _has_moved: bool = field(default=False, repr=False)
    # Optional lidar-style object metadata (length/width/height/yaw/pointCount).
    # Populated when TrackManager is tracking RadarCluster objects instead of
    # raw CFAR centroids.  Preserved across frames for the matched track.
    metadata: dict = field(default_factory=dict)

    @property
    def dyn_prop(self) -> Literal["moving", "stopped", "stationary"]:
        """ARS-style dynamic property from radial velocity magnitude."""
        speed = abs(self.vel_mps)
        if speed >= DYN_PROP_SPEED_THRESHOLD_MPS:
            self._has_moved = True
            return "moving"
        if self._has_moved:
            return "stopped"
        return "stationary"

    @property
    def aRel(self) -> float:
        """Longitudinal relative acceleration (m/s^2), negative = braking."""
        return float(self.ax)

    @property
    def lateral_speed_mps(self) -> float:
        """Lateral speed magnitude (m/s)."""
        return float(abs(self.vy))


class ABGTrackManager:
    """
    Cartesian alpha-beta-gamma tracker with confirm/drop hysteresis.

    Association is done in the x/y plane (forward/left) so gate sizes are
    physically meaningful for dense traffic.  Elevation is still tracked but
    not used for association, matching the real uncertainty of the BGT60's
    elevation estimate.

    Existence probability is an independent accumulator (spawn -> hit/miss
    stepping), so short dropouts decay confidence gradually instead of
    resetting it to zero.
    """

    def __init__(self, dt_s: float = 0.05, alpha: float = ALPHA,
                 beta: float = BETA, gamma: float = GAMMA):
        self._tracks: dict[int, Track] = {}
        self._next_id = 1
        self._max_id = (1 << 63) - 1
        self.dt_s = dt_s
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma

    def _alloc_id(self) -> int:
        tid = self._next_id
        self._next_id = (self._next_id % self._max_id) + 1
        return tid

    def _predict(self, track: Track, dt_s: float | None = None) -> None:
        """Alpha-beta-gamma prediction step (constant-acceleration model)."""
        dt = self.dt_s if dt_s is None else dt_s
        track.x += track.vx * dt + 0.5 * track.ax * dt * dt
        track.y += track.vy * dt + 0.5 * track.ay * dt * dt
        track.z += track.vz * dt + 0.5 * track.az * dt * dt
        track.vx += track.ax * dt
        track.vy += track.ay * dt
        track.vz += track.az * dt

    def _update_track(self, track: Track, det, metadata: dict | None = None,
                      dt_s: float | None = None) -> None:
        """Alpha-beta-gamma correction step fused with Doppler velocity."""
        dt = self.dt_s if dt_s is None else dt_s
        x_m, y_m, z_m = _det_to_cartesian(det)

        rx = x_m - track.x
        ry = y_m - track.y
        rz = z_m - track.z

        track.x += self.alpha * rx
        track.y += self.alpha * ry
        track.z += self.alpha * rz

        if dt > 1e-6:
            track.vx += (self.beta / dt) * rx
            track.vy += (self.beta / dt) * ry
            track.vz += (self.beta / dt) * rz
            track.ax += (self.gamma / (dt * dt)) * rx
            track.ay += (self.gamma / (dt * dt)) * ry
            track.az += (self.gamma / (dt * dt)) * rz

        # Fuse Doppler radial velocity into Cartesian velocity.  For a forward
        # obstacle this is the dominant measurement of relative speed, and using
        # it directly avoids the lag of inferring velocity from position alone.
        vel_mps = float(det.vel_mps)
        az = math.radians(float(det.azimuth_deg))
        el = math.radians(float(det.elevation_deg))
        cos_el = math.cos(el)
        vx_meas = vel_mps * cos_el * math.cos(az)
        vy_meas = vel_mps * cos_el * math.sin(az)
        vz_meas = vel_mps * math.sin(el)
        # Doppler smoothing gain: smaller than alpha so position update dominates.
        vel_gain = 0.25
        track.vx += vel_gain * (vx_meas - track.vx)
        track.vy += vel_gain * (vy_meas - track.vy)
        track.vz += vel_gain * (vz_meas - track.vz)

        # Update polar measurement from smoothed Cartesian state for publishing.
        track.range_m, track.azimuth_deg, track.elevation_deg = _cartesian_to_spherical(
            track.x, track.y, track.z)
        track.vel_mps = vel_mps
        track.snr_db = float(det.snr_db)
        track.is_static = bool(det.is_static)
        if metadata is not None:
            track.metadata = metadata

        track.hit_streak += 1
        track.miss_streak = 0
        track.existence_prob = min(EXISTENCE_MAX, track.existence_prob + EXISTENCE_HIT_STEP)
        if track.state == "tentative" and track.hit_streak >= CONFIRM_HITS:
            track.state = "confirmed"

    def update(self, centroids: list, metadata: list[dict] | None = None,
               dt_s: float | None = None) -> list[Track]:
        """Update tracks with new detections.

        Args:
            centroids: detection-shaped objects (range_m, azimuth_deg, ...)
            metadata: optional parallel list of dicts attached to each track.
                      Useful for lidar-style cluster shape (length/width/etc).
            dt_s: optional measured frame period (see KalmanTrackManager).
        """
        remaining = list(centroids)
        remaining_meta = list(metadata) if metadata is not None else [None] * len(centroids)
        matched_ids: set[int] = set()

        # Predict all existing tracks forward.
        for track in self._tracks.values():
            self._predict(track, dt_s)

        # Greedy Cartesian nearest-neighbour association with per-detection gate.
        # For a single short-range radar this is fast; global nearest-neighbour
        # (Hungarian) is overkill at this sparsity.
        for tid, track in self._tracks.items():
            if not remaining:
                break
            best_i, best_d = None, float('inf')
            for i, det in enumerate(remaining):
                x_m, y_m, _ = _det_to_cartesian(det)
                d = math.hypot(track.x - x_m, track.y - y_m)
                if d < best_d:
                    best_d, best_i = d, i
            if best_i is not None:
                gate = _class_gate_m(remaining[best_i])
                if best_d <= gate:
                    det = remaining.pop(best_i)
                    meta = remaining_meta.pop(best_i)
                    self._update_track(track, det, meta, dt_s)
                    matched_ids.add(tid)

        # Unmatched tracks: increment miss streak and decay existence.
        for tid, track in self._tracks.items():
            if tid in matched_ids:
                continue
            track.miss_streak += 1
            track.hit_streak = 0
            track.existence_prob = max(0.0, track.existence_prob - EXISTENCE_MISS_STEP)

        # Drop tracks that have missed too many consecutive frames.
        for tid in [t for t, tr in self._tracks.items() if tr.miss_streak >= DROP_MISSES]:
            del self._tracks[tid]

        # Spawn new tentative tracks for unmatched detections.
        for det, meta in zip(remaining, remaining_meta, strict=False):
            tid = self._alloc_id()
            x_m, y_m, z_m = _det_to_cartesian(det)
            self._tracks[tid] = Track(
                track_id=tid,
                x=x_m, y=y_m, z=z_m,
                vx=0.0, vy=0.0, vz=0.0,
                ax=0.0, ay=0.0, az=0.0,
                range_m=float(det.range_m),
                azimuth_deg=float(det.azimuth_deg),
                elevation_deg=float(det.elevation_deg),
                vel_mps=float(det.vel_mps),
                snr_db=float(det.snr_db),
                is_static=bool(det.is_static),
                metadata=meta if meta is not None else {},
            )

        return [t for t in self._tracks.values() if t.state == "confirmed"]


# ---------------------------------------------------------------------------
# Extended Kalman Filter tracker (default)
# ---------------------------------------------------------------------------
#
# Why Kalman over alpha-beta-gamma:
#   - Adaptive gains: the filter trusts high-SNR / close-range measurements
#     more and coasts through occlusion when uncertainty is high.
#   - Covariance estimate: downstream fusion (gridd) can gate by uncertainty,
#     not just by a fixed radius.
#   - Better transient response: acceleration steps are tracked with less lag
#     while single-frame noise is smoothed optimally.
#
# Occlusion handling:
#   - Confirmed tracks coast for up to EKF_COAST_MAX_S without a detection.
#   - During coast, only the prediction step runs, so uncertainty grows.
#   - Re-acquisition uses a covariance-scaled gate instead of the fixed class
#     gate, so a track that has been occluded for a few frames is easier to
#     re-associate when it reappears.
# ---------------------------------------------------------------------------

EKF_COAST_MAX_S = 1.0          # hard wall-clock limit a confirmed track may coast (Autoware: 1.0 s)
EKF_GATE_SCALE = 2.0           # additional gate multiplier for coasted tracks
EKF_Z_SMOOTH_GAIN = 0.3        # complementary gain for elevation/z (noisiest AoA channel)

# Bayesian existence probability (Autoware multi_object_tracker/tracker_base):
#   hit:  p' = p*TPR / (p*TPR + (1-p)*FPR)   with TPR=0.8, FPR=0.2
#   miss: exponential decay with 0.5 s half-life
# A well-confirmed track converges toward 99.9 and fades smoothly during
# occlusion instead of stepping; downstream (gridd confidence boost) sees a
# continuous confidence signal.  Stored on the 0-100 capnp scale (the update
# itself runs normalized 0-1); the expire threshold is 0.015 x 100.
EXISTENCE_TPR = 0.8            # true-positive rate (Autoware default)
EXISTENCE_FPR = 0.2            # false-positive rate (Autoware default)
EXISTENCE_HALF_LIFE_S = 0.5    # miss decay half-life
EXISTENCE_EXPIRE = 1.5         # drop a confirmed track below this (0.015 x 100)
_EXISTENCE_BAYES_MIN = 10.0    # clamp band for the Bayes hit update
_EXISTENCE_BAYES_MAX = 99.9


class KalmanTrackManager:
    """
    Constant-acceleration EKF tracker with confirm/drop hysteresis and
    occlusion coasting.

    State: [x, y, vx, vy, ax, ay] in Cartesian ego frame (x=forward, y=left).
    Measurements: [x, y] from cluster centroid plus radial Doppler vRel.

    The public interface matches ABGTrackManager: update(centroids, metadata)
    returns confirmed Track objects.  Internally each track carries a 6-D
    state vector and 6x6 covariance.
    """

    def __init__(self, dt_s: float = 0.05):
        self.dt_s = dt_s
        self._tracks: dict[int, Track] = {}
        self._states: dict[int, np.ndarray] = {}
        self._covs: dict[int, np.ndarray] = {}
        self._next_id = 1
        self._max_id = (1 << 63) - 1

    # ---- ID management -------------------------------------------------
    def _alloc_id(self) -> int:
        tid = self._next_id
        self._next_id = (self._next_id % self._max_id) + 1
        return tid

    # ---- EKF matrices --------------------------------------------------
    def _F(self, dt: float) -> np.ndarray:
        """State transition matrix for constant acceleration."""
        dt2 = 0.5 * dt * dt
        return np.array([
            [1, 0, dt, 0, dt2, 0],
            [0, 1, 0, dt, 0, dt2],
            [0, 0, 1, 0, dt, 0],
            [0, 0, 0, 1, 0, dt],
            [0, 0, 0, 0, 1, 0],
            [0, 0, 0, 0, 0, 1],
        ], dtype=np.float64)

    def _Q(self, dt: float, snr_db: float) -> np.ndarray:
        """Process noise covariance.

        High-SNR tracks get lower process noise (we trust the dynamics);
        low-SNR tracks get higher noise so the filter can adapt quickly.
        Tuned to avoid acceleration overshoot on step changes: a single
        Doppler jump should not drive |ax| > 30 m/s^2.
        """
        q_acc = max(2.0 - 0.05 * snr_db, 0.3)  # m/s^2 process noise
        dt2 = dt * dt
        dt3 = dt2 * dt
        dt4 = dt3 * dt
        q = q_acc * q_acc
        Q = np.zeros((6, 6))
        Q[0, 0] = 0.25 * dt4 * q
        Q[1, 1] = 0.25 * dt4 * q
        Q[0, 2] = Q[2, 0] = 0.5 * dt3 * q
        Q[1, 3] = Q[3, 1] = 0.5 * dt3 * q
        Q[0, 4] = Q[4, 0] = 0.5 * dt2 * q
        Q[1, 5] = Q[5, 1] = 0.5 * dt2 * q
        Q[2, 2] = dt2 * q
        Q[3, 3] = dt2 * q
        Q[2, 4] = Q[4, 2] = dt * q
        Q[3, 5] = Q[5, 3] = dt * q
        Q[4, 4] = q
        Q[5, 5] = q
        return Q

    def _R(self, range_m: float, snr_db: float) -> np.ndarray:
        """Measurement noise covariance.

        Position noise grows with range and worsens at low SNR; Doppler noise
        is mostly SNR-dependent.
        """
        pos_std = 0.08 + 0.015 * range_m + max(0.0, (20.0 - snr_db)) * 0.04
        doppler_std = 0.25 + max(0.0, (20.0 - snr_db)) * 0.08
        return np.diag([pos_std**2, pos_std**2, doppler_std**2])

    # ---- EKF steps -----------------------------------------------------
    def _predict(self, tid: int, track: Track, dt_s: float | None = None) -> None:
        dt = self.dt_s if dt_s is None else dt_s
        state = self._states[tid]
        cov = self._covs[tid]
        F = self._F(dt)
        Q = self._Q(dt, track.snr_db)
        state = F @ state
        cov = F @ cov @ F.T + Q
        self._states[tid] = state
        self._covs[tid] = cov

        # Update Track data from state for publishing / association
        track.x, track.y = float(state[0]), float(state[1])
        track.vx, track.vy = float(state[2]), float(state[3])
        track.ax, track.ay = float(state[4]), float(state[5])
        track.range_m, track.azimuth_deg, track.elevation_deg = _cartesian_to_spherical(
            track.x, track.y, track.z)
        # Publish the filtered radial velocity (not the stale raw Doppler) so a
        # coasting track still reports a physically consistent vRel.
        track.vel_mps = _radial_velocity(track.x, track.y, track.z,
                                         track.vx, track.vy, track.vz)

    def _update_track(self, tid: int, track: Track, det, metadata: dict | None = None) -> None:
        state = self._states[tid]
        cov = self._covs[tid]

        x_m, y_m, z_m = _det_to_cartesian(det)
        r = math.hypot(x_m, y_m)
        if r < 1e-3:
            r = 1e-3

        # Measurement vector: [x, y, v_radial]
        z = np.array([x_m, y_m, float(det.vel_mps)])

        # Predicted measurement h(state)
        h = np.array([
            state[0],
            state[1],
            (state[0] * state[2] + state[1] * state[3]) / r,
        ])

        # Jacobian H
        H = np.zeros((3, 6))
        H[0, 0] = 1.0
        H[1, 1] = 1.0
        r3 = r * r * r
        H[2, 0] = (state[2] * state[1]**2 - state[3] * state[0] * state[1]) / r3
        H[2, 1] = (state[3] * state[0]**2 - state[2] * state[0] * state[1]) / r3
        H[2, 2] = state[0] / r
        H[2, 3] = state[1] / r

        # Measurement update
        R = self._R(r, float(det.snr_db))
        y = z - h
        S = H @ cov @ H.T + R
        K = cov @ H.T @ np.linalg.inv(S)
        state = state + K @ y
        cov = (np.eye(6) - K @ H) @ cov
        # Ensure symmetry (numerical safety)
        cov = 0.5 * (cov + cov.T)

        self._states[tid] = state
        self._covs[tid] = cov

        # Update Track fields
        track.x, track.y = float(state[0]), float(state[1])
        track.vx, track.vy = float(state[2]), float(state[3])
        track.ax, track.ay = float(state[4]), float(state[5])
        # Elevation/z is the noisiest AoA channel on the BGT60TR13C and is not
        # part of the EKF state — smooth it with a complementary filter so the
        # overhead-sign / ground-bounce gates downstream see a stable value.
        track.z += EKF_Z_SMOOTH_GAIN * (z_m - track.z)
        track.range_m, track.azimuth_deg, track.elevation_deg = _cartesian_to_spherical(
            track.x, track.y, track.z)
        # Publish the EKF-smoothed radial velocity instead of the raw Doppler:
        # same sign convention, but without single-frame Doppler quantization
        # noise.  Raw det.vel_mps is still the measurement feeding the filter.
        track.vel_mps = _radial_velocity(track.x, track.y, track.z,
                                         track.vx, track.vy, track.vz)
        track.snr_db = float(det.snr_db)
        track.is_static = bool(det.is_static)
        if metadata is not None:
            # Temporally filter the cluster shape instead of wholesale
            # replacing it: per-frame fits on sparse clusters are flickery.
            track.metadata = _merge_shape_metadata(track.metadata, metadata)

        track.hit_streak += 1
        track.miss_streak = 0
        # Bayesian existence update (Autoware tracker_base): a hit raises
        # confidence toward 99.9 with diminishing steps, never a flat +N.
        # Computed on the normalized 0-1 scale, stored on the 0-100 capnp scale.
        p = track.existence_prob / EXISTENCE_MAX
        p = p * EXISTENCE_TPR / (p * EXISTENCE_TPR + (1.0 - p) * EXISTENCE_FPR)
        track.existence_prob = min(_EXISTENCE_BAYES_MAX, max(_EXISTENCE_BAYES_MIN, p * EXISTENCE_MAX))
        if track.state == "tentative" and track.hit_streak >= CONFIRM_HITS:
            track.state = "confirmed"

    # ---- Association gate ----------------------------------------------
    def _association_gate(self, tid: int, det) -> float:
        """Adaptive gate: base class gate scaled by track uncertainty.

        When a track has been coasting (occluded), its covariance grows, so
        the gate widens.  This makes re-acquisition after occlusion easier
        without stealing IDs from nearby confirmed tracks.
        """
        base = _class_gate_m(det)
        if tid not in self._covs:
            return base
        cov = self._covs[tid]
        # 2-sigma position uncertainty in the x/y plane
        sigma = math.sqrt(max(cov[0, 0], cov[1, 1]))
        # Base gate + covariance-scaled term, capped to avoid runaway
        return min(base + EKF_GATE_SCALE * sigma, 5.0)

    # ---- Public interface ----------------------------------------------
    def update(self, centroids: list, metadata: list[dict] | None = None,
               dt_s: float | None = None) -> list[Track]:
        """Update tracks with new detections.

        Args:
            centroids: detection-shaped objects (range_m, azimuth_deg, ...)
            metadata: optional parallel list of dicts attached to each track.
            dt_s: optional measured frame period.  The daemon is paced by the
                  sensor IRQ loop, not a Ratekeeper, so the real interval
                  jitters and stretches on dropped frames; passing it keeps
                  the constant-acceleration prediction physically correct.
                  Defaults to the nominal constructor value.
        """
        remaining = list(centroids)
        remaining_meta = list(metadata) if metadata is not None else [None] * len(centroids)
        matched_ids: set[int] = set()

        # Predict all existing tracks forward.
        for tid, track in self._tracks.items():
            self._predict(tid, track, dt_s)

        # Greedy nearest-neighbour association with adaptive gate.
        for tid, track in self._tracks.items():
            if not remaining:
                break
            best_i, best_d = None, float('inf')
            for i, det in enumerate(remaining):
                x_m, y_m, _ = _det_to_cartesian(det)
                d = math.hypot(track.x - x_m, track.y - y_m)
                if d < best_d:
                    best_d, best_i = d, i
            if best_i is not None:
                gate = self._association_gate(tid, remaining[best_i])
                if best_d <= gate:
                    det = remaining.pop(best_i)
                    meta = remaining_meta.pop(best_i)
                    self._update_track(tid, track, det, meta)
                    matched_ids.add(tid)

        dt_eff = self.dt_s if dt_s is None else dt_s

        # Unmatched tracks: increment miss streak and decay existence with a
        # fixed half-life (Autoware tracker_base), so long frames from a sensor
        # hiccup decay confidence faster than short ones.
        miss_decay = 0.5 ** (dt_eff / EXISTENCE_HALF_LIFE_S)
        for tid, track in self._tracks.items():
            if tid in matched_ids:
                continue
            track.miss_streak += 1
            track.hit_streak = 0
            track.existence_prob *= miss_decay

        # Drop tracks: tentative tracks die fast; confirmed tracks coast
        # through occlusion up to a hard wall-clock limit, or expire early if
        # the Bayesian existence probability has collapsed.
        to_drop = []
        for tid, tr in self._tracks.items():
            if tr.state == "tentative":
                if tr.miss_streak >= DROP_MISSES:
                    to_drop.append(tid)
            elif tr.miss_streak * dt_eff > EKF_COAST_MAX_S or tr.existence_prob < EXISTENCE_EXPIRE:
                to_drop.append(tid)
        for tid in to_drop:
            del self._tracks[tid]
            del self._states[tid]
            del self._covs[tid]

        # Spawn new tentative tracks for unmatched detections.
        for det, meta in zip(remaining, remaining_meta, strict=False):
            tid = self._alloc_id()
            x_m, y_m, z_m = _det_to_cartesian(det)
            self._tracks[tid] = Track(
                track_id=tid,
                x=x_m, y=y_m, z=z_m,
                vx=0.0, vy=0.0, vz=0.0,
                ax=0.0, ay=0.0, az=0.0,
                range_m=float(det.range_m),
                azimuth_deg=float(det.azimuth_deg),
                elevation_deg=float(det.elevation_deg),
                vel_mps=float(det.vel_mps),
                snr_db=float(det.snr_db),
                is_static=bool(det.is_static),
                metadata=meta if meta is not None else {},
            )
            # Initialise EKF state from measurement.  Doppler gives a good
            # initial radial velocity; transverse velocity starts at zero.
            az = math.radians(float(det.azimuth_deg))
            el = math.radians(float(det.elevation_deg))
            cos_el = math.cos(el)
            vx0 = det.vel_mps * cos_el * math.cos(az)
            vy0 = det.vel_mps * cos_el * math.sin(az)
            self._states[tid] = np.array([x_m, y_m, vx0, vy0, 0.0, 0.0], dtype=np.float64)
            pos_var = 0.04
            vel_var = 0.8
            acc_var = 1.0
            self._covs[tid] = np.diag([pos_var, pos_var, vel_var, vel_var, acc_var, acc_var])

        return [t for t in self._tracks.values() if t.state == "confirmed"]


# Default tracker: Kalman with occlusion handling.
TrackManager = KalmanTrackManager
