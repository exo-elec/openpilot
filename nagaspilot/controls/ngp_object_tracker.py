"""Constant-velocity Kalman tracker for detector objects, with occlusion coasting and path prediction.

State per track is [x, y, vx, vy] in the car frame relative to ego (x forward, y LEFT,
the `yRel` convention). Measurements are ranged detections (x, y). An unmatched confirmed
track coasts on its prediction (marked `occluded`, covariance growing) and is dropped after
MAX_COAST_S. Pure numpy; no cereal, no Params.
"""
from dataclasses import dataclass, field

import numpy as np

ACCEL_SIGMA = 3.0        # m/s^2 process noise
GATE_CHI2 = 9.21         # 99 % for 2 dof
MIN_HITS = 3
MAX_COAST_S = 1.5
TENTATIVE_MAX_MISSES = 1
HORIZON_S = 3.0
STEP_S = 0.5


@dataclass
class Measurement:
  name: str
  x: float
  y: float
  sigma_x: float
  sigma_y: float


@dataclass
class Track:
  track_id: int
  name: str
  state: np.ndarray            # [x, y, vx, vy]
  cov: np.ndarray              # 4x4
  hits: int = 1
  misses: int = 0
  since_update: float = 0.0
  history: list = field(default_factory=list)

  @property
  def confirmed(self) -> bool:
    return self.hits >= MIN_HITS

  @property
  def occluded(self) -> bool:
    return self.confirmed and self.misses > 0

  @property
  def x(self): return float(self.state[0])
  @property
  def y(self): return float(self.state[1])
  @property
  def vx(self): return float(self.state[2])
  @property
  def vy(self): return float(self.state[3])
  @property
  def sigma_x(self): return float(np.sqrt(self.cov[0, 0]))
  @property
  def sigma_y(self): return float(np.sqrt(self.cov[1, 1]))


def measurement_sigma(x: float) -> tuple[float, float]:
  """Ranging error grows with distance: ~5 % along, ~2 % + 0.2 m across."""
  return max(0.3, 0.05 * x), 0.2 + 0.02 * x


class ObjectTracker:
  def __init__(self):
    self.tracks: list[Track] = []
    self._next_id = 1

  def update(self, meas: list[Measurement], dt: float) -> list[Track]:
    for t in self.tracks:
      self._predict(t, dt)

    unmatched = set(range(len(meas)))
    pairs = []
    for ti, t in enumerate(self.tracks):
      for mi, m in enumerate(meas):
        if m.name != t.name:
          continue
        d2 = self._mahalanobis2(t, m)
        if d2 <= GATE_CHI2:
          pairs.append((d2, ti, mi))
    used_t: set[int] = set()
    for d2, ti, mi in sorted(pairs):
      if ti in used_t or mi not in unmatched:
        continue
      used_t.add(ti)
      unmatched.discard(mi)
      self._correct(self.tracks[ti], meas[mi])

    for ti, t in enumerate(self.tracks):
      if ti not in used_t:
        t.misses += 1
        t.since_update += dt

    self.tracks = [t for t in self.tracks if self._alive(t)]
    for mi in sorted(unmatched):
      m = meas[mi]
      cov = np.diag([m.sigma_x ** 2, m.sigma_y ** 2, 25.0, 4.0])
      self.tracks.append(Track(self._next_id, m.name, np.array([m.x, m.y, 0.0, 0.0]), cov))
      self._next_id += 1
    return [t for t in self.tracks if t.confirmed]

  @staticmethod
  def _alive(t: Track) -> bool:
    if not t.confirmed:
      return t.misses <= TENTATIVE_MAX_MISSES
    return t.since_update <= MAX_COAST_S

  @staticmethod
  def _predict(t: Track, dt: float) -> None:
    F = np.eye(4)
    F[0, 2] = F[1, 3] = dt
    q = ACCEL_SIGMA ** 2
    Q = q * np.array([[dt**4 / 4, 0, dt**3 / 2, 0], [0, dt**4 / 4, 0, dt**3 / 2],
                      [dt**3 / 2, 0, dt**2, 0], [0, dt**3 / 2, 0, dt**2]])
    t.state = F @ t.state
    t.cov = F @ t.cov @ F.T + Q

  @staticmethod
  def _mahalanobis2(t: Track, m: Measurement) -> float:
    H = np.array([[1.0, 0, 0, 0], [0, 1.0, 0, 0]])
    R = np.diag([m.sigma_x ** 2, m.sigma_y ** 2])
    S = H @ t.cov @ H.T + R
    r = np.array([m.x, m.y]) - H @ t.state
    return float(r @ np.linalg.solve(S, r))

  @staticmethod
  def _correct(t: Track, m: Measurement) -> None:
    H = np.array([[1.0, 0, 0, 0], [0, 1.0, 0, 0]])
    R = np.diag([m.sigma_x ** 2, m.sigma_y ** 2])
    S = H @ t.cov @ H.T + R
    K = t.cov @ H.T @ np.linalg.inv(S)
    t.state = t.state + K @ (np.array([m.x, m.y]) - H @ t.state)
    t.cov = (np.eye(4) - K @ H) @ t.cov
    t.hits += 1
    t.misses = 0
    t.since_update = 0.0


def predict_path(t: Track, horizon: float = HORIZON_S, step: float = STEP_S) -> list[tuple[float, float, float]]:
  """Constant-velocity future positions [(t, x, y)] relative to ego now."""
  n = int(round(horizon / step))
  return [(i * step, t.x + t.vx * i * step, t.y + t.vy * i * step) for i in range(1, n + 1)]


def time_to_corridor(t: Track, half_width: float = 1.2, horizon: float = HORIZON_S, step: float = 0.1) -> float | None:
  """First time within the horizon the object is ahead (0 < x) and inside |y| < half_width, else None."""
  n = int(round(horizon / step))
  for i in range(n + 1):
    s = i * step
    x, y = t.x + t.vx * s, t.y + t.vy * s
    if x <= 0:
      return None if i == 0 else None
    if abs(y) < half_width:
      return s
  return None
