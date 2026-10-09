"""
DrivableFusion: segd's per-camera drivable BEV maps fused into one drivable
layer on BEV_GRID (rows forward, cols left positive; CLAUDE.md "Frame
conventions").

After Autoware's probabilistic_occupancy_grid_map: each camera's single-frame
map is fused once, when it arrives, in log-odds weighted by that camera's
reliability (synchronized_grid_map_fusion, fusion_method log-odds); between
updates the map decays toward unknown (OccupancyGridMapLOBFUpdater, tau
0.75 s). Sources here run at different, changing rates (segd feeds the card
by demand, schedule.py), so nothing waits for anyone: a camera that is off
simply fades. That fade is honest -- unknown, never stale road.

The grid is in the vehicle frame, so it is moved back by the distance the car
travelled since the last step (livePose forward velocity, signed, so reverse
works). Rotation is not modelled: over the 0.75 s memory a curve smears a
faded cell sideways by well under a lane.
"""
from __future__ import annotations

import numpy as np

from openpilot.nagaspilot.daemons.gridd.fusion_costmap import COST_LANE, COST_ROAD, COST_UNCERTAIN, COST_UNKNOWN
from openpilot.nagaspilot.daemons.gridd.lazy_bev import BEV_GRID, BEVGridSpec

# segd drivableBev cell values
UNKNOWN, OTHER, DRIVABLE, LANE = 0, 1, 2, 3

TAU_S = 0.75             # decay to unknown (Autoware LOBF tau)
P_DRIVABLE_OBS = 0.85    # a camera calling a cell drivable / not drivable
LOGIT_MAX = 6.0          # |log-odds| clip (p 0.9975)
RELIABILITY = {          # input_ogm_reliabilities: how far each camera is trusted
  'road': 1.0, 'wide': 0.8, 'tele': 0.8, 'side_left': 0.6, 'side_right': 0.6, 'rear': 0.6,
}
DEGRADED_AFTER_S = 1.0   # no camera map for this long -> segmentation degraded

P_ROAD = 0.75            # fused probability drivable at/above -> confident road
P_UNCERTAIN = 0.55       # ... at/above -> uncertain road; below -> unknown
LANE_SCORE_MIN = 0.5

_LOGIT_OBS = float(np.log(P_DRIVABLE_OBS / (1.0 - P_DRIVABLE_OBS)))


class DrivableFusion:
  def __init__(self, spec: BEVGridSpec = BEV_GRID) -> None:
    self.spec = spec
    shape = (spec.rows, spec.cols)
    self.logodds = np.zeros(shape, dtype=np.float32)   # of "drivable"; 0 = unknown
    self.lane = np.zeros(shape, dtype=np.float32)      # decayed lane-marking evidence
    self._t: float | None = None
    self._last_map_t: float | None = None
    self._shift_frac = 0.0                             # ego travel not yet a whole cell

  def step(self, now: float, v_forward: float = 0.0) -> None:
    """Age the map to `now`: decay toward unknown, move with the car."""
    if self._t is not None:
      dt = max(0.0, now - self._t)
      decay = float(np.exp(-dt / TAU_S))
      self.logodds *= decay
      self.lane *= decay
      self._move(v_forward * dt)
    self._t = now

  def _move(self, travelled_m: float) -> None:
    """Ego moved `travelled_m` forward: content moves back that many rows."""
    self._shift_frac += travelled_m / self.spec.resolution
    cells = int(self._shift_frac)   # toward zero: keeps the fraction's sign
    if cells == 0:
      return
    self._shift_frac -= cells
    for layer in (self.logodds, self.lane):
      if abs(cells) >= layer.shape[0]:
        layer.fill(0.0)
      elif cells > 0:
        layer[:-cells] = layer[cells:]
        layer[-cells:] = 0.0
      else:
        layer[-cells:] = layer[:cells]
        layer[:-cells] = 0.0

  def update(self, camera: str, cells: np.ndarray, now: float) -> None:
    """Fuse one camera's drivableBev map (rows x cols uint8) once, on arrival."""
    if cells.shape != self.logodds.shape:
      return
    w = RELIABILITY.get(camera, 0.5)
    drivable = (cells == DRIVABLE) | (cells == LANE)
    self.logodds[drivable] += w * _LOGIT_OBS
    self.logodds[cells == OTHER] -= w * _LOGIT_OBS
    np.clip(self.logodds, -LOGIT_MAX, LOGIT_MAX, out=self.logodds)
    self.lane[cells == LANE] += w
    self.lane[drivable & (cells != LANE)] *= 0.5     # road seen without a marking
    np.clip(self.lane, 0.0, 2.0, out=self.lane)
    self._last_map_t = now

  def has_evidence(self, now: float) -> bool:
    """A camera map arrived recently: else segmentation is degraded (never a fault)."""
    return self._last_map_t is not None and now - self._last_map_t <= DEGRADED_AFTER_S

  def probability(self) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-self.logodds))

  def cost(self) -> np.ndarray:
    """Drivable-layer cost for FusionCostmap.apply_drivable (uint16; capped at
    COST_UNKNOWN, so segmentation alone never reaches pathd's obstacle
    thresholds). Not-road stays COST_UNKNOWN: it only fails to lower the cost."""
    p = self.probability()
    cost = np.full(p.shape, COST_UNKNOWN, dtype=np.uint16)
    cost[p >= P_UNCERTAIN] = COST_UNCERTAIN
    road = p >= P_ROAD
    cost[road] = COST_ROAD
    cost[road & (self.lane >= LANE_SCORE_MIN)] = COST_LANE
    return cost
