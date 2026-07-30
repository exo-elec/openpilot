"""
Pointcloud processing for the BGT60TR13C 4D radar.

Takes raw/compensated radar detections, clusters them in Cartesian space,
estimates object shape/size/yaw, and produces Radar4DObject outputs for
camera-radar fusion in gridd.

The pipeline follows the Autoware perception pattern (ground filter →
Euclidean cluster → shape estimation, originally built for lidar), applied
here to a sparse radar pointcloud.  Design choices for a sparse short-range
radar:
  - DBSCAN-style Euclidean clustering (scipy cKDTree) instead of dense
    grid/voxel methods.  A BGT60TR13C frame only has a handful of CFAR hits,
    so a KD-tree is faster and more appropriate than a voxel grid.
  - Ground filtering is elevation-aware using the radar's own elevation
    estimate, not a full scan-ground-segmentation algorithm that expects a
    dense ground plane.
  - Shape estimation uses PCA for small clusters and an Autoware-style
    L-shape search when enough points are available, mirroring
    autoware_shape_estimation's bounding-box model.
  - All heavy math is plain NumPy/SciPy; an OpenCL/ACL path is reserved for
    future denser point clouds and is not a win for <50 points/frame.

This module is intentionally free of cereal/HAL imports so it can be unit
 tested with plain dataclasses.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Literal, Protocol

import numpy as np
from scipy.spatial import cKDTree

# Default Autoware-style Euclidean cluster params, scaled to a sparse radar.
# A dense lidar config uses min_samples=10 and eps=0.7 m; our CFAR hits are
# far sparser, so 2-3 points is already a meaningful cluster.
CLUSTER_EPS_M = 0.6          # neighbour search radius (m)
CLUSTER_MIN_SAMPLES = 2      # core point threshold
CLUSTER_MIN_POINTS = 2       # smallest cluster we publish

# Ground removal: points with |elevation| below this or z below this are
# treated as road surface / ground bounce and dropped before clustering.
GROUND_ELEVATION_DEG = -6.0  # below horizon
GROUND_Z_M = -0.5            # below ego (road surface)

# Shape estimation
LSHAPE_MIN_POINTS = 5        # need enough points for a stable L-shape search
LSHAPE_ANGLE_RES_RAD = math.radians(2.0)  # search granularity
LSHAPE_SEARCH_HALF_RAD = math.radians(45.0)

# Oversized-cluster split (Autoware euclidean_cluster splitOversizedClusters).
# At eps=0.6 m a guardrail and the vehicle next to it can merge into one long
# blob; any cluster whose principal-axis extent exceeds this is almost
# certainly two objects, so bisect it at the median along the PCA axis.
SPLIT_MAX_EXTENT_M = 8.0     # longer than any single car; short of a full bus
SPLIT_MAX_DEPTH = 2          # bound recursion so a wall does not shatter


def _det_to_cartesian(det) -> tuple[float, float, float]:
    """Spherical (range_m, azimuth_deg, elevation_deg) -> (x, y, z).

    Convention: x = forward, y = left, z = up.
    """
    r = float(det.range_m)
    az = math.radians(float(det.azimuth_deg))
    el = math.radians(float(det.elevation_deg))
    cos_el = math.cos(el)
    x = r * cos_el * math.cos(az)
    y = r * cos_el * math.sin(az)
    z = r * math.sin(el)
    return x, y, z


class _Detection(Protocol):
    """Duck-typed radar detection accepted by the pipeline."""
    range_m: float
    vel_mps: float
    azimuth_deg: float
    elevation_deg: float
    snr_db: float
    is_static: bool


@dataclass
class RadarPoint:
    """One radar detection in Cartesian ego frame."""
    x: float          # forward (m)
    y: float          # left (m)
    z: float          # up (m)
    range_m: float
    azimuth_deg: float
    elevation_deg: float
    vel_mps: float
    snr_db: float
    is_static: bool


@dataclass
class RadarCluster:
    """Cluster of radar points representing one object candidate."""
    cluster_id: int
    points: list[RadarPoint] = field(default_factory=list)
    # Centroid / track state (populated by shape estimator)
    cx: float = 0.0
    cy: float = 0.0
    cz: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    vz: float = 0.0
    ax: float = 0.0
    length_m: float = 0.0
    width_m: float = 0.0
    height_m: float = 0.0
    yaw_rad: float = 0.0
    snr_db: float = 0.0
    existence_prob: float = 0.0
    is_static: bool = False
    dyn_prop: Literal["stationary", "moving", "stopped"] = "stationary"

    @property
    def range_m(self) -> float:
        return math.hypot(self.cx, math.hypot(self.cy, self.cz))

    @property
    def azimuth_deg(self) -> float:
        return math.degrees(math.atan2(self.cy, self.cx))

    @property
    def elevation_deg(self) -> float:
        r = self.range_m
        return math.degrees(math.atan2(self.cz, r)) if r > 0.0 else 0.0

    @property
    def vRel(self) -> float:
        """Radial velocity of cluster centroid (negative = approaching)."""
        r = self.range_m
        if r < 1e-6:
            return -self.vx
        return (self.vx * self.cx + self.vy * self.cy + self.vz * self.cz) / r

    @property
    def vel_mps(self) -> float:
        """Radial velocity magnitude (alias for vRel, used by TrackManager)."""
        return self.vRel

    @property
    def aRel(self) -> float:
        """Longitudinal relative acceleration (negative = braking)."""
        return float(self.ax)


def detections_to_points(detections: list[_Detection]) -> list[RadarPoint]:
    """Convert a list of polar detections to Cartesian RadarPoints."""
    points: list[RadarPoint] = []
    for det in detections:
        x, y, z = _det_to_cartesian(det)
        points.append(RadarPoint(
            x=x, y=y, z=z,
            range_m=float(det.range_m),
            azimuth_deg=float(det.azimuth_deg),
            elevation_deg=float(det.elevation_deg),
            vel_mps=float(det.vel_mps),
            snr_db=float(det.snr_db),
            is_static=bool(det.is_static),
        ))
    return points


def filter_ground_points(
    points: list[RadarPoint],
    elev_deg: float = GROUND_ELEVATION_DEG,
    z_m: float = GROUND_Z_M,
) -> list[RadarPoint]:
    """Remove ground/road-surface returns before clustering.

    Uses both the radar's elevation estimate and the Cartesian z coordinate.
    This is intentionally lightweight for a sparse radar; a full scan-ground
    filter belongs in pointcloudd when fusing dense stereo point clouds.
    """
    return [p for p in points
            if p.elevation_deg > elev_deg and p.z > z_m]


# ---------------------------------------------------------------------------
# Environment inference from the raw return cloud.
#
# These run on the UNFILTERED points (before ground removal), because the
# evidence they look for is exactly what the clustering pipeline discards.
# ---------------------------------------------------------------------------

# Precipitation: rain/snow shows up as many weak, scattered returns that
# mostly fail to cluster — the wiper-on weather the driver already sees.
PRECIP_MIN_POINTS = 8           # need enough raw returns for a meaningful estimate
PRECIP_WEAK_SNR_DB = 12.0       # returns below this are precipitation-like
PRECIP_WEAK_FRACTION_FULL = 0.7 # weak-return fraction mapping to prob 1.0
PRECIP_SCATTER_BINS_FULL = 6    # occupied 10-deg azimuth bins mapping to full scatter


def estimate_precipitation(points: list[RadarPoint]) -> float:
    """Estimate rain/snow probability from the raw return cloud, 0-1.

    Precipitation returns are weak (low SNR) and spatially scattered across
    the whole FOV, unlike real-object returns which are stronger and compact.
    The score combines both: weak-return fraction x azimuth-scatter fraction.
    """
    if len(points) < PRECIP_MIN_POINTS:
        return 0.0
    weak = [p for p in points if p.snr_db < PRECIP_WEAK_SNR_DB]
    weak_frac = len(weak) / len(points)
    bins = {int(p.azimuth_deg // 10.0) for p in weak}
    scatter_frac = min(1.0, len(bins) / PRECIP_SCATTER_BINS_FULL)
    return min(1.0, (weak_frac / PRECIP_WEAK_FRACTION_FULL) * scatter_frac)


# Drop-off (negative obstacle): returns far BELOW the expected road plane in
# the forward corridor mean the road does not continue — cliff edge, ditch,
# collapsed shoulder.  A hard safety guard the camera surface detection can
# miss at night / in glare.
ROAD_MOUNT_HEIGHT_M = 0.5       # nominal radar mounting height above road
DROPOFF_DEPTH_MARGIN_M = 0.6    # how far below the road plane counts as evidence
DROPOFF_CORRIDOR_AZ_DEG = 20.0  # forward corridor (matches gridd blind-spot az)
DROPOFF_MIN_POINTS = 2          # evidence returns needed to call a hazard


def detect_dropoff(
    points: list[RadarPoint],
    mount_height_m: float = ROAD_MOUNT_HEIGHT_M,
    depth_margin_m: float = DROPOFF_DEPTH_MARGIN_M,
    corridor_az_deg: float = DROPOFF_CORRIDOR_AZ_DEG,
    min_points: int = DROPOFF_MIN_POINTS,
) -> tuple[bool, float]:
    """Detect a negative obstacle (drop-off) ahead from below-road returns.

    Expected road-surface returns sit near z = -mount_height.  Forward-path
    returns far below that plane are past a drop-off edge (the ground filter
    would discard them as "ground", which is why this runs on raw points).

    Returns:
        (hazard, distance_m) — distance is the median range of the evidence
        points, 0.0 when there is no hazard.
    """
    z_thresh = -(mount_height_m + depth_margin_m)
    evidence = [p.range_m for p in points
                if abs(p.azimuth_deg) <= corridor_az_deg and p.z < z_thresh]
    if len(evidence) < min_points:
        return False, 0.0
    return True, float(np.median(evidence))


# Wiper motion: the driver turns the wipers on exactly when it is wet, and
# the blade sweeps directly in front of the radar as a periodic, very close,
# fast-moving reflector — a far less ambiguous slippery-weather signal than
# precipitation clutter statistics.
WIPER_RANGE_MAX_M = 1.5       # blade arc stays this close to the radar
WIPER_MIN_MOVING = 2          # near-range moving returns counting as blade evidence
WIPER_WINDOW_FRAMES = 200     # ~10 s at 20 Hz (covers intermittent wipe)
WIPER_MIN_SWEEPS = 2          # periodic sweeps in the window to call wiper-on


class WiperMotionDetector:
    """Detect windshield-wiper sweeps from near-range moving returns.

    The blade produces bursts of close, non-static returns once per sweep.
    A single burst could be a passerby; PERIODIC bursts within the window are
    the wiper.  Runs on raw (unfiltered) points — the blade is above the
    ground filter but well inside the near field.
    """

    def __init__(self, window_frames: int = WIPER_WINDOW_FRAMES, fps: float = 20.0):
        self._bursts: deque = deque(maxlen=window_frames)
        self._fps = fps

    def _sweeps(self) -> int:
        return sum(1 for a, b in zip(self._bursts, list(self._bursts)[1:])
                   if b and not a)

    @property
    def sweep_rate_hz(self) -> float:
        """Sweeps per second in the current window — intermittent vs fast wipe
        is a direct rain-heaviness cue."""
        if len(self._bursts) < 2:
            return 0.0
        return self._sweeps() * self._fps / len(self._bursts)

    def update(self, points: list[RadarPoint]) -> bool:
        """Feed one frame of raw points; returns True when the wiper is on."""
        n_moving = sum(1 for p in points
                       if p.range_m <= WIPER_RANGE_MAX_M and not p.is_static)
        self._bursts.append(n_moving >= WIPER_MIN_MOVING)
        return self._sweeps() >= WIPER_MIN_SWEEPS


# Windshield contamination (rain drops / washer fluid / snow on the glass in
# front of the radar, radar-behind-glass mounting).  60 GHz is highly
# water-sensitive: the film adds a persistent near-STATIC hot zone (it moves
# with the car, unlike the wiper blade) and attenuates far targets.
CONTAM_RANGE_MAX_M = 1.0      # water/snow film on the glass = near-static hot zone
CONTAM_FAR_MIN_M = 3.0        # far returns used for the attenuation baseline
CONTAM_MIN_NEAR = 2           # near-static returns forming the hot zone
CONTAM_SNR_DROP_DB = 6.0      # far-target SNR drop vs clean baseline
CONTAM_BASELINE_GAIN = 0.02   # slow EMA so real weather changes beat the baseline
CONTAM_CONFIRM_FRAMES = 10    # ~0.5 s persistence required

# 3-step weather severity (0=clear is implicit): how heavy the rain / snow /
# mud-dirt is, so ADAS can step its risk margin down accordingly.
WEATHER_SEVERITY_NAMES = ("clear", "light", "moderate", "heavy")
WIPER_FAST_HZ = 0.6           # fast wipe = heavy rain driver cue
CONTAM_HEAVY_DB = 12.0        # deep attenuation = heavy film / mud / packed snow
PRECIP_MODERATE = 0.6         # clutter probability thresholds per level
PRECIP_LIGHT = 0.3


def classify_weather_severity(precip_prob: float, wiper_on: bool, wiper_rate_hz: float,
                              contaminated: bool, attenuation_db: float) -> int:
    """Combine all radar weather signals into a 0-3 severity level.

    0 clear    — nothing detected
    1 light    — drizzle/mist: some clutter or intermittent wipe
    2 moderate — steady rain/snow: fast wipe, heavy clutter, or glass film
    3 heavy    — downpour/mud/packed snow: deep attenuation, or fast wipe
                 with heavy clutter together
    """
    if (contaminated and attenuation_db >= CONTAM_HEAVY_DB) or \
       (wiper_on and wiper_rate_hz >= WIPER_FAST_HZ and precip_prob >= PRECIP_MODERATE):
        return 3
    if contaminated or (wiper_on and wiper_rate_hz >= WIPER_FAST_HZ) or precip_prob >= PRECIP_MODERATE:
        return 2
    if wiper_on or precip_prob >= PRECIP_LIGHT:
        return 1
    return 0


# Total vision loss: the contamination film blocks everything beyond
# CONTAM_FAR_MIN_M — the radar sees only the glass and nothing of the road.
# Distinct from severity 3 (heavy but still seeing): this is the "cannot see
# the road at all" case that must escalate to driver takeover, not just gating.
VISION_BLOCKED_DB = 50.0  # reachable only when far returns vanish (attenuation_db = 99.0)


def detect_vision_blocked(contaminated: bool, attenuation_db: float) -> bool:
    """True when the contamination film blocks all returns past the near field."""
    return contaminated and attenuation_db >= VISION_BLOCKED_DB


class WindshieldContaminationDetector:
    """Detect water film / washer fluid / snow covering the windshield.

    Two combined signatures, sustained over frames:
      1. hot zone: persistent near-range STATIC returns (the film rides with
         the car — a wiper blade or passerby is dynamic, so it can't fake this);
      2. attenuation: median far-target SNR drops below a slowly-adapted
         clean-weather baseline, or far returns vanish entirely.
    Needs a clean reference first: the baseline is only learned on frames
    without a hot zone.
    """

    def __init__(self):
        self._snr_baseline: float | None = None
        self._streak = 0
        self._attenuation_db = 0.0

    @property
    def attenuation_db(self) -> float:
        """Current far-target attenuation vs clean baseline (dB, >= 0).
        99.0 when far returns have vanished entirely — depth of contamination
        is the mud/dirt/heavy-snow severity cue."""
        return self._attenuation_db

    def update(self, points: list[RadarPoint]) -> bool:
        """Feed one frame of raw points; returns True when the glass is contaminated."""
        near_static = [p for p in points
                       if p.range_m <= CONTAM_RANGE_MAX_M and p.is_static]
        far_snr = [p.snr_db for p in points if p.range_m >= CONTAM_FAR_MIN_M]
        far_med = float(np.median(far_snr)) if far_snr else None

        hot_zone = len(near_static) >= CONTAM_MIN_NEAR
        if not hot_zone and far_med is not None:
            if self._snr_baseline is None:
                self._snr_baseline = far_med
            else:
                self._snr_baseline += CONTAM_BASELINE_GAIN * (far_med - self._snr_baseline)

        if self._snr_baseline is None or far_med is None:
            self._attenuation_db = 99.0 if (self._snr_baseline is not None and far_med is None) else 0.0
        else:
            self._attenuation_db = max(0.0, self._snr_baseline - far_med)

        attenuated = (self._snr_baseline is not None and
                      (far_med is None or far_med < self._snr_baseline - CONTAM_SNR_DROP_DB))
        if hot_zone and attenuated:
            self._streak += 1
        else:
            self._streak = 0
        return self._streak >= CONTAM_CONFIRM_FRAMES


def dbscan_cluster_indices(
    points: list[RadarPoint],
    eps_m: float = CLUSTER_EPS_M,
    min_samples: int = CLUSTER_MIN_SAMPLES,
) -> list[list[int]]:
    """DBSCAN clustering on Cartesian (x, y, z) using scipy cKDTree.

    Returns a list of clusters, each a list of point indices.  Noise points
    are omitted.  Time complexity is O(N log N) which is fine for a handful
    of radar hits per frame.
    """
    n = len(points)
    if n < min_samples:
        return []

    xyz = np.array([[p.x, p.y, p.z] for p in points], dtype=np.float32)
    tree = cKDTree(xyz)
    neighbors = tree.query_ball_point(xyz, r=eps_m)

    visited = [False] * n
    clusters: list[list[int]] = []

    for i in range(n):
        if visited[i]:
            continue
        visited[i] = True
        if len(neighbors[i]) < min_samples:
            continue

        # BFS/DFS expand cluster
        cluster = [i]
        queue = list(neighbors[i])
        for j in queue:
            if visited[j]:
                continue
            visited[j] = True
            cluster.append(j)
            if len(neighbors[j]) >= min_samples:
                queue.extend(neighbors[j])
        clusters.append(cluster)

    return clusters


def _pca_2d(xy: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Return (eigenvalues, eigenvectors, mean) for 2D points."""
    mean = xy.mean(axis=0)
    cov = np.cov(xy - mean, rowvar=False)
    # Handle single-point / collinear cases
    if cov.ndim < 2 or cov.shape != (2, 2):
        cov = np.eye(2) * 1e-6
    eigvals, eigvecs = np.linalg.eigh(cov)
    # Sort descending
    order = np.argsort(eigvals)[::-1]
    return eigvals[order], eigvecs[:, order], mean


def _lshape_search(xy: np.ndarray) -> tuple[float, float, float]:
    """Autoware-style L-shape bounding-box search (Zhang et al. IV2017).

    Returns (length, width, yaw_rad).  Searches over a small angle window and
    picks the orientation that maximizes the closeness criterion.
    """
    best_score = -np.inf
    best_theta = 0.0
    best_len, best_wid = 0.1, 0.1

    angles = np.arange(
        -LSHAPE_SEARCH_HALF_RAD,
        LSHAPE_SEARCH_HALF_RAD + LSHAPE_ANGLE_RES_RAD,
        LSHAPE_ANGLE_RES_RAD,
    )

    for theta in angles:
        c, s = math.cos(theta), math.sin(theta)
        e1 = np.array([c, s])
        e2 = np.array([-s, c])
        c1 = xy @ e1
        c2 = xy @ e2
        min1, max1 = c1.min(), c1.max()
        min2, max2 = c2.min(), c2.max()
        d_min = 0.1 * 0.1
        d_max = 0.4 * 0.4
        beta = 0.0
        for a, b in zip(c1, c2):
            d1 = min(max1 - a, a - min1)
            d2 = min(max2 - b, b - min2)
            d = max(min(d1, d2), d_min)
            if d <= d_max:
                beta += 1.0 / d
        length = max1 - min1
        width = max2 - min2
        # Prefer boxes that are longitudinally aligned (theta near 0)
        score = beta + 0.1 * abs(length - width)
        if score > best_score:
            best_score = score
            best_theta = theta
            best_len, best_wid = length, width

    # Ensure length >= width, flip yaw if needed
    if best_wid > best_len:
        best_len, best_wid = best_wid, best_len
        best_theta += math.pi / 2.0
    best_theta = math.atan2(math.sin(best_theta), math.cos(best_theta))
    return max(best_len, 0.1), max(best_wid, 0.1), best_theta


def estimate_cluster_shape(cluster: RadarCluster) -> None:
    """Estimate size/yaw/centroid/velocity of a radar cluster.

    Small clusters (<5 points) use PCA because L-shape search is unstable.
    Larger clusters use the Autoware L-shape criterion.
    """
    pts = cluster.points
    if not pts:
        return

    xyz = np.array([[p.x, p.y, p.z] for p in pts], dtype=np.float32)
    xy = xyz[:, :2]

    # Centroid
    cluster.cx, cluster.cy, cluster.cz = float(xyz[:, 0].mean()), float(xyz[:, 1].mean()), float(xyz[:, 2].mean())

    # Height
    cluster.height_m = max(0.1, float(xyz[:, 2].max() - xyz[:, 2].min()))

    # Velocity / acceleration from centroid-weighted Doppler
    vels = np.array([[p.vel_mps * math.cos(math.radians(p.azimuth_deg)) * math.cos(math.radians(p.elevation_deg)),
                      p.vel_mps * math.sin(math.radians(p.azimuth_deg)) * math.cos(math.radians(p.elevation_deg)),
                      p.vel_mps * math.sin(math.radians(p.elevation_deg))]
                     for p in pts], dtype=np.float32)
    weights = np.array([p.snr_db for p in pts], dtype=np.float32)
    weights = np.maximum(weights, 1.0)
    cluster.vx, cluster.vy, cluster.vz = (vels * weights[:, None]).sum(axis=0) / weights.sum()

    # SNR / static / existence summaries
    cluster.snr_db = float(np.max([p.snr_db for p in pts]))
    cluster.is_static = bool(np.mean([p.is_static for p in pts]) > 0.5)

    # Size & yaw
    n = len(pts)
    if n >= LSHAPE_MIN_POINTS:
        cluster.length_m, cluster.width_m, cluster.yaw_rad = _lshape_search(xy)
    else:
        eigvals, eigvecs, _ = _pca_2d(xy)
        major = eigvecs[:, 0]
        cluster.yaw_rad = math.atan2(major[1], major[0])
        # 2-sigma extents along principal axes
        std_major = math.sqrt(max(eigvals[0], 1e-6))
        std_minor = math.sqrt(max(eigvals[1], 1e-6))
        cluster.length_m = max(0.1, 4.0 * std_major)
        cluster.width_m = max(0.1, 4.0 * std_minor)


def _split_oversized(pts: list[RadarPoint], min_points: int, depth: int = 0) -> list[list[RadarPoint]]:
    """Bisect an oversized cluster at the median along its principal axis.

    Autoware euclidean_cluster does the same for dense lidar blobs that exceed
    the per-class max size; at our sparsity the trigger is the principal-axis
    extent, and recursion is depth-bounded so a long wall does not shatter
    into singletons.
    """
    if depth >= SPLIT_MAX_DEPTH or len(pts) < 2 * min_points:
        return [pts]
    xy = np.array([[p.x, p.y] for p in pts], dtype=np.float32)
    eigvals, eigvecs, mean = _pca_2d(xy)
    proj = (xy - mean) @ eigvecs[:, 0]
    if float(proj.max() - proj.min()) <= SPLIT_MAX_EXTENT_M:
        return [pts]
    median = float(np.median(proj))
    lo = [p for p, v in zip(pts, proj) if v <= median]
    hi = [p for p, v in zip(pts, proj) if v > median]
    if not lo or not hi:
        return [pts]
    return _split_oversized(lo, min_points, depth + 1) + _split_oversized(hi, min_points, depth + 1)


def cluster_points(
    points: list[RadarPoint],
    eps_m: float = CLUSTER_EPS_M,
    min_samples: int = CLUSTER_MIN_SAMPLES,
    min_points: int = CLUSTER_MIN_POINTS,
) -> list[RadarCluster]:
    """Cluster radar points and estimate shape for each cluster."""
    clusters: list[RadarCluster] = []
    indices = dbscan_cluster_indices(points, eps_m, min_samples)
    for idxs in indices:
        if len(idxs) < min_points:
            continue
        for sub in _split_oversized([points[i] for i in idxs], min_points):
            if len(sub) < min_points:
                continue
            cluster = RadarCluster(cluster_id=len(clusters), points=sub)
            estimate_cluster_shape(cluster)
            clusters.append(cluster)
    return clusters


class RadarPointcloudProcessor:
    """Autoware-pattern pointcloud → objects pipeline for sparse 4D radar."""

    def __init__(
        self,
        eps_m: float = CLUSTER_EPS_M,
        min_samples: int = CLUSTER_MIN_SAMPLES,
        min_points: int = CLUSTER_MIN_POINTS,
        ground_elev_deg: float = GROUND_ELEVATION_DEG,
        ground_z_m: float = GROUND_Z_M,
        enable_ground_filter: bool = True,
    ):
        self.eps_m = eps_m
        self.min_samples = min_samples
        self.min_points = min_points
        self.ground_elev_deg = ground_elev_deg
        self.ground_z_m = ground_z_m
        self.enable_ground_filter = enable_ground_filter

    def process(self, detections: list[_Detection]) -> list[RadarCluster]:
        """Run ground filter → cluster → shape estimate and return objects."""
        points = detections_to_points(detections)
        if self.enable_ground_filter:
            points = filter_ground_points(points, self.ground_elev_deg, self.ground_z_m)
        return cluster_points(points, self.eps_m, self.min_samples, self.min_points)
