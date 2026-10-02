#!/usr/bin/env python3
"""Integration smoke test for CARLA simulation components.

Run this on the workstation after starting CARLA to verify:
1. SimulatedSideD connects and publishes sideDetections
2. TrafficLightPublisher connects and publishes stereoObjects
3. ScenarioSpawner can spawn actors
4. WeatherController can apply presets

Usage:
    # Terminal 1: start CARLA
    ./CarlaUE4.sh -quality-level=Low -RenderOffScreen -nosound

    # Terminal 2: run smoke test
    cd ~/openpilot && python tools/sim/tests/test_workstation_integration.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))


def check_carla_import():
  try:
    import carla
    print(f"[OK] carla Python API imported (version: {carla.__file__})")
    return True
  except ImportError as e:
    print(f"[FAIL] Cannot import carla: {e}")
    return False


def check_carla_connection(host="localhost", port=2000, timeout=10):
  try:
    import carla
    client = carla.Client(host, port)
    client.set_timeout(timeout)
    world = client.get_world()
    print(f"[OK] Connected to CARLA server at {host}:{port}")
    print(f"       World: {world.get_map().name}")
    return world
  except Exception as e:
    print(f"[FAIL] Cannot connect to CARLA: {e}")
    return None


def check_simulated_sided(world):
  try:
    from openpilot.tools.sim.lib.simulated_sided import SimulatedSideD
    print("[OK] SimulatedSideD imported")

    # Need an ego vehicle to test
    blueprint_library = world.get_blueprint_library()
    vehicle_bp = blueprint_library.filter('vehicle.tesla.model3')[0]
    spawn_points = world.get_map().get_spawn_points()
    vehicle = world.spawn_actor(vehicle_bp, spawn_points[0])
    print(f"[OK] Spawned ego vehicle (id={vehicle.id})")

    sd = SimulatedSideD()
    sd.update(world, vehicle)
    print("[OK] SimulatedSideD.update() executed without error")

    vehicle.destroy()
    return True
  except Exception as e:
    print(f"[FAIL] SimulatedSideD test failed: {e}")
    return False


def check_traffic_light_publisher(world):
  try:
    from openpilot.tools.sim.lib.traffic_light_publisher import TrafficLightPublisher
    print("[OK] TrafficLightPublisher imported")

    blueprint_library = world.get_blueprint_library()
    vehicle_bp = blueprint_library.filter('vehicle.tesla.model3')[0]
    spawn_points = world.get_map().get_spawn_points()
    vehicle = world.spawn_actor(vehicle_bp, spawn_points[0])

    tlp = TrafficLightPublisher()
    tlp.update(world, vehicle)
    print("[OK] TrafficLightPublisher.update() executed without error")

    vehicle.destroy()
    return True
  except Exception as e:
    print(f"[FAIL] TrafficLightPublisher test failed: {e}")
    return False


def check_weather_controller(world):
  try:
    from openpilot.tools.sim.lib.weather_controller import WEATHER_PRESETS
    import carla
    print("[OK] WeatherController imported")

    # Test applying each preset
    for _name, preset in WEATHER_PRESETS.items():
      world.set_weather(carla.WeatherParameters(
        cloudiness=preset.cloudiness,
        precipitation=preset.precipitation,
        precipitation_deposits=preset.precipitation_deposits,
        wind_intensity=preset.wind_intensity,
        sun_azimuth_angle=preset.sun_azimuth_angle,
        sun_altitude_angle=preset.sun_altitude_angle,
        fog_density=preset.fog_density,
        fog_distance=preset.fog_distance,
        wetness=preset.wetness,
      ))
      # Tick to apply
      world.tick()
    print(f"[OK] Applied all {len(WEATHER_PRESETS)} weather presets")
    return True
  except Exception as e:
    print(f"[FAIL] WeatherController test failed: {e}")
    return False


def check_scenario_spawner(world):
  try:
    from openpilot.tools.sim.lib.scenario_spawner import ScenarioSpawner, SCENARIOS
    print("[OK] ScenarioSpawner imported")
    print(f"[INFO] Available scenarios: {list(SCENARIOS.keys())}")

    blueprint_library = world.get_blueprint_library()
    vehicle_bp = blueprint_library.filter('vehicle.tesla.model3')[0]
    spawn_points = world.get_map().get_spawn_points()
    vehicle = world.spawn_actor(vehicle_bp, spawn_points[0])

    # Test each scenario
    for scenario_name in SCENARIOS:
      spawner = ScenarioSpawner.__new__(ScenarioSpawner)
      spawner.scenario_name = scenario_name
      spawner.active = True
      spawner._spawned = False
      spawner._actors = []
      spawner.spawn(world, blueprint_library, vehicle)
      print(f"[OK] Spawned scenario '{scenario_name}' with {len(spawner._actors)} actors")
      spawner.cleanup()
      world.tick()

    vehicle.destroy()
    return True
  except Exception as e:
    print(f"[FAIL] ScenarioSpawner test failed: {e}")
    return False


def main():
  print("=" * 60)
  print("CARLA Workstation Integration Smoke Test")
  print("=" * 60)

  passed = 0
  failed = 0

  if not check_carla_import():
    sys.exit(1)

  world = check_carla_connection()
  if world is None:
    sys.exit(1)

  tests = [
    ("SimulatedSideD", check_simulated_sided),
    ("TrafficLightPublisher", check_traffic_light_publisher),
    ("WeatherController", check_weather_controller),
    ("ScenarioSpawner", check_scenario_spawner),
  ]

  for name, test_fn in tests:
    print()
    print(f"--- Testing {name} ---")
    if test_fn(world):
      passed += 1
    else:
      failed += 1

  print()
  print("=" * 60)
  print(f"Results: {passed} passed, {failed} failed")
  print("=" * 60)

  if failed > 0:
    sys.exit(1)
  print("All integration checks passed!")


if __name__ == "__main__":
  main()
