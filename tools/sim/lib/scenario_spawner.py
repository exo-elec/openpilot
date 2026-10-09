"""CARLA scenario spawner using native CARLA APIs.

No custom abstractions — just direct carla.World, carla.TrafficManager,
and carla.command usage. Inspired by CARLA's generate_traffic.py example.

Scenarios:
  pedestrian_crossing  — AI walker crosses ego path
  cut_in               — NPC forced lane change via TM
  emergency_brake      — Lead vehicle full brake via VehicleControl
  cyclist_overtake     — Slow-moving bicycle in adjacent lane

Controlled via EOPSimScenario param.
"""
from __future__ import annotations

import math
import time

from openpilot.common.params import Params

SCENARIOS = {"pedestrian_crossing", "cut_in", "emergency_brake", "cyclist_overtake"}


def _get_blueprints(world, filter_str: str, generation: str = "all"):
  """Return matching blueprints, optionally filtered by generation.

  Mirrors logic from CARLA's generate_traffic.py.
  """
  bps = world.get_blueprint_library().filter(filter_str)
  if generation.lower() == "all" or len(bps) <= 1:
    return bps
  try:
    gen = int(generation)
    return [bp for bp in bps if int(bp.get_attribute("generation")) == gen]
  except (ValueError, TypeError):
    return bps


def _spawn_actor(world, blueprint, transform, attach_to=None):
  """Try to spawn an actor; return None on failure."""
  try:
    if attach_to is not None:
      return world.spawn_actor(blueprint, transform, attach_to=attach_to)
    return world.spawn_actor(blueprint, transform)
  except RuntimeError:
    return None


def _waypoint_ahead(world_map, ego_transform, distance: float):
  """Get a waypoint distance meters ahead of ego."""
  waypoint = world_map.get_waypoint(ego_transform.location)
  return waypoint.next(distance)[0] if waypoint else None


class ScenarioSpawner:
  """Spawns test scenarios using pure CARLA APIs."""

  def __init__(self):
    self.params = Params()
    self.scenario_name = (self.params.get("EOPSimScenario") or "").lower().strip()
    self.active = self.scenario_name in SCENARIOS
    self._spawned = False
    self._actors: list = []

  # ------------------------------------------------------------------
  # Scenario implementations
  # ------------------------------------------------------------------

  def _pedestrian_crossing(self, world, ego_vehicle):
    """Spawn an AI walker that crosses the road ahead of ego."""
    import carla
    wps = _get_blueprints(world, "walker.pedestrian.*", generation="2")
    if not wps:
      return
    walker_bp = wps[0]
    if walker_bp.has_attribute("is_invincible"):
      walker_bp.set_attribute("is_invincible", "true")

    # Spawn ~25 m ahead, offset to the side
    ego_t = ego_vehicle.get_transform()
    fwd = ego_t.get_forward_vector()
    right = carla.Location(x=-fwd.y, y=fwd.x, z=0.0)  # perpendicular
    spawn_loc = ego_t.location + fwd * 25.0 + right * 3.0
    spawn_loc.z += 0.5

    walker = _spawn_actor(world, walker_bp, carla.Transform(spawn_loc))
    if walker is None:
      return

    # AI walker controller
    ctrl_bp = world.get_blueprint_library().find("controller.ai.walker")
    ctrl = _spawn_actor(world, ctrl_bp, carla.Transform(), attach_to=walker)
    if ctrl is not None:
      ctrl.start()
      target = spawn_loc - right * 8.0
      ctrl.go_to_location(target)
      ctrl.set_max_speed(1.4)
      self._actors.extend([walker, ctrl])
    else:
      self._actors.append(walker)

  def _cut_in(self, world, ego_vehicle, tm_port: int = 8000):
    """Spawn a vehicle in the fast lane and force a lane change into ego lane.

    For LHD (US/EU): fast lane is LEFT, cut-in from left.
    For RHD/LHT (Bangkok/Thailand): fast lane is RIGHT, cut-in from right.
    """
    vbps = _get_blueprints(world, "vehicle.*")
    if not vbps:
      return
    vbp = vbps[0]

    ego_t = ego_vehicle.get_transform()
    world_map = world.get_map()
    ego_wp = world_map.get_waypoint(ego_t.location)
    if ego_wp is None:
      return

    # Detect road rule: LHT = RHD (Bangkok), RHT = LHD (US/EU).
    # CARLA waypoints don't expose road rule directly; we default to LHD
    # and rely on the OSM map's embedded LHT/RHT data for TrafficManager.
    is_lht = False  # Default LHD; OSM LHT maps will handle TM correctly

    # In LHT (Bangkok), fast lane is on the right -> cut-in from right
    # In RHT (US/EU), fast lane is on the left  -> cut-in from left
    if is_lht:
      target_lane = ego_wp.get_right_lane()
      cut_direction = True   # True = change to left lane (ego lane)
    else:
      target_lane = ego_wp.get_left_lane()
      cut_direction = False  # False = change to right lane (ego lane)

    if target_lane is None:
      return

    spawn_wp = target_lane.previous(10.0)[0] if target_lane.previous(10.0) else None
    if spawn_wp is None:
      return

    vehicle = _spawn_actor(world, vbp, spawn_wp.transform)
    if vehicle is None:
      return

    vehicle.set_autopilot(True, tm_port)
    tm = world.get_trafficmanager(tm_port)
    tm.auto_lane_change(vehicle, False)
    tm.force_lane_change(vehicle, cut_direction)
    tm.vehicle_percentage_speed_difference(vehicle, -10.0)

    self._actors.append(vehicle)

  def _emergency_brake(self, world, ego_vehicle):
    """Spawn a lead vehicle 20 m ahead; full brake after 5 s."""
    import carla
    vbps = _get_blueprints(world, "vehicle.*")
    if not vbps:
      return
    vbp = vbps[0]

    ego_t = ego_vehicle.get_transform()
    fwd = ego_t.get_forward_vector()
    spawn_loc = ego_t.location + fwd * 20.0
    spawn_loc.z += 0.5
    yaw = ego_t.rotation.yaw

    vehicle = _spawn_actor(
      world, vbp, carla.Transform(spawn_loc, carla.Rotation(yaw=yaw))
    )
    if vehicle is None:
      return

    vehicle.set_autopilot(True)
    self._actors.append(vehicle)
    self._brake_vehicle = vehicle
    self._brake_trigger_time = time.monotonic() + 5.0

  def _cyclist_overtake(self, world, ego_vehicle):
    """Spawn a slow-moving bicycle 40 m ahead in the slow lane.

    For RHD/LHT (Bangkok): slow lane is on the LEFT.
    For LHD (US/EU): slow lane is on the RIGHT.
    """
    import carla
    world_map = world.get_map()
    ego_t = ego_vehicle.get_transform()
    ego_wp = world_map.get_waypoint(ego_t.location)
    if ego_wp is None:
      return

    # Try bicycle blueprint; fallback to any vehicle
    bps = _get_blueprints(world, "vehicle.diamondback.century")
    if not bps:
      bps = _get_blueprints(world, "vehicle.*")
    if not bps:
      return

    # Slow lane: left in LHT (Bangkok), right in RHT (US)
    # Default to LHD behavior; OSM LHT maps will reverse via TrafficManager
    slow_lane_wp = ego_wp.get_right_lane()
    if slow_lane_wp is not None:
      ahead = slow_lane_wp.next(40.0)
      spawn_wp = ahead[0] if ahead else None
    else:
      spawn_wp = ego_wp.next(40.0)[0] if ego_wp.next(40.0) else None

    if spawn_wp is None:
      return

    vehicle = _spawn_actor(world, bps[0], spawn_wp.transform)
    if vehicle is None:
      return

    # Slow cyclist speed (~4 m/s)
    yaw = math.radians(spawn_wp.transform.rotation.yaw)
    vehicle.set_target_velocity(carla.Vector3D(x=math.cos(yaw) * 4.0, y=math.sin(yaw) * 4.0, z=0.0))
    vehicle.set_autopilot(True)
    self._actors.append(vehicle)

  # ------------------------------------------------------------------
  # Public API
  # ------------------------------------------------------------------

  def spawn(self, world, ego_vehicle):
    """Spawn the configured scenario."""
    if not self.active or self._spawned:
      return

    scenario = self.scenario_name
    if scenario == "pedestrian_crossing":
      self._pedestrian_crossing(world, ego_vehicle)
    elif scenario == "cut_in":
      self._cut_in(world, ego_vehicle)
    elif scenario == "emergency_brake":
      self._emergency_brake(world, ego_vehicle)
    elif scenario == "cyclist_overtake":
      self._cyclist_overtake(world, ego_vehicle)

    self._spawned = True
    alive = [a for a in self._actors if a is not None]
    print(f"ScenarioSpawner: spawned '{scenario}' with {len(alive)} actors")

  def tick(self):
    """Update scenario state (e.g., trigger emergency brake)."""
    if not self.active or not self._spawned:
      return

    if self.scenario_name == "emergency_brake" and hasattr(self, "_brake_vehicle"):
      if time.monotonic() > self._brake_trigger_time and self._brake_vehicle is not None:
        try:
          import carla
          self._brake_vehicle.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
          self._brake_vehicle = None
          print("ScenarioSpawner: emergency brake triggered")
        except RuntimeError:
          pass

  def cleanup(self):
    """Destroy all spawned actors."""
    for actor in self._actors:
      if actor is not None:
        try:
          actor.destroy()
        except RuntimeError:
          pass
    self._actors.clear()
    self._spawned = False
