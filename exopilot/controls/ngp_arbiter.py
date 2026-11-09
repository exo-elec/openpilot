"""Tighten-only arbitration of rule-based add-on proposals around the driving policy.

Control style (chosen for pathd, see OBJECT_PROTECTION_STUDY.md section 15): the policy stays the
primary controller; add-ons (pathd, cut-in trim, SOC, RED, ...) are *proposers* that each return a
small bounded Proposal. One arbiter merges them under rules that can only make the car more
conservative:

  speed   : the minimum of all caps (never raises speed)
  lateral : each proposer asks for an offset (m, left positive) inside the lane room it was given.
            Offsets of the same sign do not add up: the largest magnitude wins. Opposite signs
            conflict, and a conflict means NO lateral offset (the policy path) plus the slower speed cap.
  bounds  : the result never exceeds the room on either side, nor MAX_OFFSET_M.

Because the arbiter only sees Proposals, it does not care whether a proposer is a rule, a sampler,
Hybrid A* or a learned model: new proposers plug in without touching controlsd/planner again.
Pure: no cereal, no Params.
"""
import math
from dataclasses import dataclass

MAX_OFFSET_M = 0.6


@dataclass(frozen=True)
class Proposal:
  source: str
  offset_m: float = 0.0               # left positive; 0 = no lateral request
  speed_cap: float | None = None      # m/s, None = no request


@dataclass(frozen=True)
class Arbitrated:
  offset_m: float
  speed_cap: float | None
  conflict: bool
  sources: tuple[str, ...]            # which proposers shaped the result


def arbitrate(proposals: list[Proposal], room_left_m: float, room_right_m: float) -> Arbitrated:
  caps = [p.speed_cap for p in proposals if p.speed_cap is not None and math.isfinite(p.speed_cap)]
  speed = min(caps) if caps else None
  left = [p for p in proposals if math.isfinite(p.offset_m) and p.offset_m > 0.0]
  right = [p for p in proposals if math.isfinite(p.offset_m) and p.offset_m < 0.0]
  if left and right:
    return Arbitrated(0.0, speed, True, tuple(p.source for p in left + right))
  hi = min(max(room_left_m, 0.0), MAX_OFFSET_M)
  lo = -min(max(room_right_m, 0.0), MAX_OFFSET_M)
  if left:
    win = max(left, key=lambda p: p.offset_m)
    return Arbitrated(min(win.offset_m, hi), speed, False, (win.source,))
  if right:
    win = min(right, key=lambda p: p.offset_m)
    return Arbitrated(max(win.offset_m, lo), speed, False, (win.source,))
  return Arbitrated(0.0, speed, False, tuple(p.source for p in proposals if p.speed_cap is not None))
