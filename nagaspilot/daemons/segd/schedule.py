"""
Which camera the card segments next: what the drive needs, not round-robin.

Pure policy (no cereal, no Params), the same shape as nagaspilot's controllers.
One card job per tick at most; a tick with nothing due runs no job.

Three steps: context -> wanted Hz per camera (`wanted_rates`); wants granted
in priority order from the tick budget (`grant`); earliest-deadline pick of
the most overdue granted camera (`Scheduler.next_camera`).
"""
from __future__ import annotations

from dataclasses import dataclass

CAMERAS = ('road', 'wide', 'tele', 'side_left', 'side_right', 'rear')

URBAN_SPEED_MPS = 12.0     # wide matters below this (junctions, city)
TELE_SPEED_MPS = 15.0      # far road matters above this
TURN_SPEED_MPS = 8.0       # a turn toward a side camera counts below this
TURN_STEER_DEG = 60.0


@dataclass(frozen=True)
class SegContext:
  """What the drive looks like right now (segd builds it from cereal)."""
  v_ego: float = 0.0
  reverse: bool = False
  steering_deg: float = 0.0            # + = left, as carState
  left_blinker: bool = False
  right_blinker: bool = False
  left_blindspot: bool = False
  right_blindspot: bool = False
  left_detection: bool = False         # a side_left camera detection is present
  right_detection: bool = False
  rear_detection: bool = False
  has_tele: bool = False


def wanted_rates(ctx: SegContext) -> list[tuple[str, float]]:
  """(camera, Hz) in priority order: earlier entries are granted first."""
  wants: list[tuple[str, float]] = []
  turning_left = ctx.v_ego < TURN_SPEED_MPS and ctx.steering_deg > TURN_STEER_DEG
  turning_right = ctx.v_ego < TURN_SPEED_MPS and ctx.steering_deg < -TURN_STEER_DEG
  side_hz = {
    'side_left': 6.0 if (ctx.left_blinker or turning_left) else 4.0 if (ctx.left_blindspot or ctx.left_detection) else 0.0,
    'side_right': 6.0 if (ctx.right_blinker or turning_right) else 4.0 if (ctx.right_blindspot or ctx.right_detection) else 0.0,
  }
  intent = ('side_left', 'side_right')

  if ctx.reverse:
    wants.append(('rear', 10.0))
    wants += [(c, side_hz[c]) for c in intent if side_hz[c] > 0.0]
    wants.append(('road', 2.0))
    return wants

  # Lane-change / turn intent first: a full manoeuvre never starves the road camera
  wants += [(c, side_hz[c]) for c in intent if side_hz[c] >= 6.0]
  wants.append(('road', 10.0))
  wants += [(c, side_hz[c]) for c in intent if 0.0 < side_hz[c] < 6.0]
  if ctx.rear_detection:
    wants.append(('rear', 4.0))
  wants.append(('wide', 4.0 if ctx.v_ego < URBAN_SPEED_MPS else 2.0))
  if ctx.has_tele and ctx.v_ego > TELE_SPEED_MPS:
    wants.append(('tele', 4.0))
  return wants


def grant(wants: list[tuple[str, float]], budget_hz: float) -> dict[str, float]:
  """Grant wants in priority order until the budget runs out."""
  granted: dict[str, float] = {}
  left = budget_hz
  for cam, hz in wants:
    give = min(hz, left)
    if give <= 0.0:
      break
    granted[cam] = give
    left -= give
  return granted


class Scheduler:
  """Credit per granted camera: exact long-run rates, one job per tick or none.

  Each tick a granted camera earns hz / rate_hz credit; the camera holding the
  most credit (at least 1, ties to the higher priority) runs and spends 1.
  Granted rates sum to at most rate_hz, so credit cannot pile up. A camera
  that has just become wanted starts with 1 credit, so it is served at once;
  one that is no longer wanted loses its credit.
  """
  MAX_CREDIT = 2.0

  def __init__(self, rate_hz: float = 20.0) -> None:
    self.rate_hz = rate_hz
    self._credit: dict[str, float] = {}

  def rates(self, ctx: SegContext) -> dict[str, float]:
    return grant(wanted_rates(ctx), self.rate_hz)

  def next_camera(self, tick: int, ctx: SegContext) -> str | None:
    """The camera to run at this tick, or None (nothing due: an idle tick)."""
    rates = self.rates(ctx)
    for cam in [c for c in self._credit if c not in rates]:
      del self._credit[cam]
    for cam, hz in rates.items():
      self._credit[cam] = 1.0 if cam not in self._credit else min(self._credit[cam] + hz / self.rate_hz, self.MAX_CREDIT)
    best, best_credit = None, 1.0 - 1e-9
    for cam in rates:  # priority order
      if self._credit[cam] > best_credit + 1e-9:
        best, best_credit = cam, self._credit[cam]
    if best is not None:
      self._credit[best] -= 1.0
    return best
