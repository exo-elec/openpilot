#!/usr/bin/env python3
"""Closed-loop toy simulation of the rule-based add-on layer, no vehicle and no MetaDrive needed.

Point-mass ego with a stand-in "policy" (hold set speed, hold lane centre, follow an object once it is in the lane like the stock lead logic) and scripted traffic, with
the add-ons in the loop: tracker-free ground-truth detections (optional noise), cut-in speed trim,
path selector, tighten-only arbiter, slewed consumers. Reports the worst gap/TTC with the layer OFF
and ON. It checks the control logic and its tuning direction, not the real car: ego dynamics are a
first-order lag on speed and a first-order lag on lateral offset.

  python3 -m nagaspilot.tools.sim_scenarios
"""
import math
import random
from dataclasses import dataclass

from nagaspilot.controls.ngp_arbiter import Proposal, arbitrate
from nagaspilot.controls.ngp_cutin_speed import CutInSpeed, Obj
from nagaspilot.controls.ngp_path_selector import PathSelector, PObj, build_profile
from nagaspilot.controls.ngp_pathd_consumer import PathAdjustFollower, speed_cap

DT = 0.05
SPEED_TAU_S = 1.2      # ego speed response to a lowered target
LAT_TAU_S = 0.8        # ego lateral response to an offset request
MAX_DECEL = 4.0        # m/s^2, comfortable-to-firm brake limit of the stand-in lead logic
LANE_HALF = 1.75       # lane half width, ego car 1.9 m wide
ROOM = 0.5             # room inside the lane each side the layer may use


@dataclass
class Actor:
  name: str
  x: float            # world forward from ego start (m)
  y: float            # world lateral, left positive (m)
  v: float            # world forward speed (m/s)
  vy: float = 0.0
  width: float = 1.8
  cut_start: float | None = None   # time the lateral move starts
  cut_vy: float = 0.0
  cut_stop_y: float | None = None  # stop moving laterally at this y


WIDTH = {'car': 1.8, 'truck': 2.5, 'motorcycle': 0.8, 'bicycle': 0.6, 'bus': 2.5}


def run(actors: list[Actor], set_speed: float = 25.0, layer: bool = True, seconds: float = 12.0,
        noise: float = 0.0, seed: int = 1) -> dict:
  rng = random.Random(seed)
  ex, ey, ev = 0.0, 0.0, set_speed       # ego world position, lateral offset (left +), speed
  sel = PathSelector()
  cutin = CutInSpeed()
  follower = PathAdjustFollower()
  target_off = 0.0
  min_gap = math.inf
  min_ttc = math.inf
  max_off = 0.0
  min_speed = ev
  hits = 0
  t = 0.0
  while t < seconds:
    for a in actors:
      if a.cut_start is not None and t >= a.cut_start:
        a.vy = a.cut_vy
      a.x += a.v * DT
      a.y += a.vy * DT
      if a.cut_stop_y is not None and ((a.vy > 0 and a.y >= a.cut_stop_y) or (a.vy < 0 and a.y <= a.cut_stop_y)):
        a.y, a.vy = a.cut_stop_y, 0.0
    cap = None
    if layer:
      objs, pobjs = [], []
      for i, a in enumerate(actors):
        rx, ry = a.x - ex, a.y - ey
        if abs(noise) > 0:
          rx *= 1 + rng.gauss(0, noise)
          ry += rng.gauss(0, noise * 2)
        rvx, rvy = a.v - ev, a.vy
        objs.append(Obj(i, rx, ry, rvx, rvy, max(0.05 * abs(rx), 0.3), 0.9, a.name))
        pobjs.append(PObj(i, a.name, rx, ry, rvx, rvy, 0.9))
      ci = cutin.update(ev, objs, DT)
      room_l = max(LANE_HALF - ey - 0.95 - 0.15, 0.0) if True else ROOM
      room_r = max(LANE_HALF + ey - 0.95 - 0.15, 0.0)
      s = sel.update(ev, pobjs, None, room_l, room_r)
      offs, caps = build_profile(s, ev, follower.offset)
      props = [Proposal('pathd', follower.update(s.offset_m, True, True, DT), speed_cap(ev, s.speed_factor, True)),
               Proposal('cutin', 0.0, ci.target_speed)]
      arb = arbitrate(props, room_l, room_r)
      target_off, cap = arb.offset_m, arb.speed_cap
    target_speed = min(set_speed, cap) if cap is not None else set_speed
    # stand-in for the stock lead logic: follow the nearest object that is already in our lane (headway 1.4 s + 4 m)
    for a in actors:
      dx, dy = a.x - ex, a.y - ey
      if dx > 0 and abs(dy) < 1.4:
        follow = a.v + 0.35 * (dx - (4.0 + 1.4 * ev))
        target_speed = min(target_speed, max(follow, 0.0))
    accel = (target_speed - ev) / SPEED_TAU_S
    ev += max(accel, -MAX_DECEL) * DT
    ey += (target_off - ey) * DT / LAT_TAU_S
    ex += ev * DT
    max_off = max(max_off, abs(ey))
    min_speed = min(min_speed, ev)
    for a in actors:
      dx, dy = a.x - ex, a.y - ey
      lat_gap = abs(dy) - (a.width + 1.9) / 2.0
      if lat_gap < 0.0 and abs(dx) < 4.5:       # bodies overlap laterally and longitudinally
        hits += 1
      if lat_gap < 0.5 and dx > 0:              # in or near our lane ahead
        min_gap = min(min_gap, dx)
        closing = ev - a.v
        if closing > 0.1:
          min_ttc = min(min_ttc, dx / closing)
    t += DT
  return {'layer': layer, 'min_gap_ahead_m': round(min_gap, 1) if math.isfinite(min_gap) else None,
          'min_ttc_s': round(min_ttc, 2) if math.isfinite(min_ttc) else None, 'max_offset_m': round(max_off, 2),
          'min_speed_mps': round(min_speed, 1), 'overlap_steps': hits}


def scenarios() -> dict:
  return {
    'car_cut_in_from_right': lambda: [Actor('car', 20.0, -3.5, 20.0, cut_start=1.0, cut_vy=1.4, cut_stop_y=0.0)],
    'bike_cut_in_from_left': lambda: [Actor('motorcycle', 12.0, 3.0, 22.0, width=0.8, cut_start=1.0, cut_vy=-1.2, cut_stop_y=0.0)],
    'truck_alongside_right': lambda: [Actor('truck', 0.0, -3.0, 25.0, width=2.5)],
    'bike_filtering_alongside': lambda: [Actor('motorcycle', -3.0, -2.0, 26.0, width=0.8)],
    'adjacent_lane_car_steady': lambda: [Actor('car', 15.0, 3.6, 25.0)],
    'car_moving_away': lambda: [Actor('car', 15.0, -2.2, 25.0, cut_start=0.0, cut_vy=-0.8)],
  }


def main() -> None:
  for name, make in scenarios().items():
    off = run(make(), layer=False)
    on = run(make(), layer=True)
    print(name)
    print('  off:', off)
    print('  on :', on)


if __name__ == "__main__":
  main()
