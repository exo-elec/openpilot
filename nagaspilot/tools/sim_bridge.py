#!/usr/bin/env python3
"""Ground-truth `monoDetections` for any simulator (MetaDrive, CARLA, a replayed scenario).

The simulator knows every actor's world pose. This module turns a list of actors plus the ego pose into the
`monoDetections` the real pipeline would publish, optionally with noise and dropouts, so pathd / the cut-in trim
/ DPP can be driven in a simulator without a camera or the detector:

  detections = ground_truth_detections(ego, actors, noise=0.03)        # pure, tested
  publish(pm, detections, frame_id, t_s)                               # cereal PubMaster('monoDetections')

Boxes are projected with the road camera's pinhole model so the NGP10 ranging code can ALSO be exercised end to
end in simulation (`range_from_box` below must return the true distance, which is a unit test).
Frames: world and ego x forward, y LEFT. Speeds in m/s. Pure apart from `publish`.
"""
import math
import random
from dataclasses import dataclass

from nagaspilot.controls.ngp_ranging import RoadCamera

IMG_W, IMG_H = 1928, 1208
CLASS_HEIGHT_M = {'car': 1.5, 'truck': 2.5, 'bus': 2.8, 'motorcycle': 1.2, 'bicycle': 1.0, 'person': 1.7}
CLASS_WIDTH_M = {'car': 1.8, 'truck': 2.5, 'bus': 2.5, 'motorcycle': 0.8, 'bicycle': 0.6, 'person': 0.6}


@dataclass(frozen=True)
class Pose:
  x: float
  y: float
  vx: float
  vy: float = 0.0
  yaw: float = 0.0


@dataclass(frozen=True)
class Actor:
  actor_id: int
  name: str
  pose: Pose


@dataclass(frozen=True)
class Detection:
  track_id: int
  name: str
  confidence: float
  x: float          # forward, m
  y: float          # left, m
  vx: float         # relative velocity
  vy: float
  sigma_x: float
  u: float          # normalised box centre x
  v: float          # normalised box centre y
  w: float
  h: float


def project_box(name: str, x: float, y: float, cam: RoadCamera = RoadCamera(), horizon_v: float | None = None,
                vp_u: float | None = None) -> tuple[float, float, float, float] | None:
  """Normalised (u, v, w, h) of an upright box standing on the road at (x forward, y left), camera looking straight ahead."""
  if x <= 1.0:
    return None
  vp_u = cam.cx if vp_u is None else vp_u
  horizon_v = cam.cy if horizon_v is None else horizon_v
  hpx = CLASS_HEIGHT_M.get(name, 1.5) * cam.focal / x
  wpx = CLASS_WIDTH_M.get(name, 1.8) * cam.focal / x
  bottom = horizon_v + cam.height_m * cam.focal / x
  cx = vp_u - y * cam.focal / x
  if not (0 <= cx <= IMG_W) or bottom - hpx > IMG_H or bottom < 0:
    return None
  return cx / IMG_W, (bottom - hpx / 2) / IMG_H, wpx / IMG_W, hpx / IMG_H


def range_from_box(u: float, v: float, w: float, h: float, cam: RoadCamera = RoadCamera()) -> tuple[float, float]:
  """Inverse of project_box (flat ground): (x, y) from the box bottom."""
  bottom = (v + h / 2) * IMG_H
  x = cam.height_m * cam.focal / (bottom - cam.cy)
  y = -(u * IMG_W - cam.cx) * x / cam.focal
  return x, y


def ground_truth_detections(ego: Pose, actors: list[Actor], noise: float = 0.0, dropout: float = 0.0,
                            rng: random.Random | None = None, max_range: float = 120.0, fov_half_deg: float = 25.0,
                            cam: RoadCamera = RoadCamera()) -> list[Detection]:
  """What the detector + tracker would publish: relative position/velocity, a box, a range sigma. `noise` is a relative range error."""
  rng = rng or random.Random(0)
  out = []
  for a in actors:
    dx, dy = a.pose.x - ego.x, a.pose.y - ego.y
    if not (1.0 < dx < max_range) or abs(math.degrees(math.atan2(dy, dx))) > fov_half_deg:
      continue
    if dropout > 0 and rng.random() < dropout:
      continue
    ex = 1.0 + (rng.gauss(0.0, noise) if noise > 0 else 0.0)
    x, y = dx * ex, dy + (rng.gauss(0.0, 2 * noise) if noise > 0 else 0.0)
    box = project_box(a.name, x, y, cam)
    if box is None:
      continue
    out.append(Detection(a.actor_id, a.name, 0.9, x, y, a.pose.vx - ego.vx, a.pose.vy - ego.vy, max(0.05 * x, 0.3), *box))
  return out


def publish(pm, detections: list[Detection], frame_id: int, t_s: float, exec_time_s: float = 0.0) -> None:
  from cereal import messaging
  msg = messaging.new_message('monoDetections', valid=True)
  md = msg.monoDetections
  md.frameId = frame_id
  md.timestamp = t_s
  md.numTracks = len(detections)
  md.modelExecutionTime = exec_time_s
  items = md.init('detections', len(detections))
  for i, d in enumerate(detections):
    it = items[i]
    it.trackId, it.className, it.confidence, it.cameraSource = d.track_id, d.name, d.confidence, 'road'
    it.x, it.y, it.vx, it.vy, it.sigmaX = d.x, d.y, d.vx, d.vy, d.sigma_x
    it.u, it.v, it.w, it.h = d.u, d.v, d.w, d.h
    it.distance = math.hypot(d.x, d.y)
  pm.send('monoDetections', msg)
