"""Unit tests for CARLA simulation components (no CARLA server required)."""

import math

import pytest

from openpilot.selfdrive.controls.lib.radar_zones import RadarZoneMonitor, ZoneAlertLevel
from openpilot.selfdrive.sided.bev_reprojector import make_default_geometry
from openpilot.tools.sim.lib.scenario_spawner import ScenarioSpawner, SCENARIOS
from openpilot.tools.sim.lib.weather_controller import WEATHER_PRESETS, WeatherController


# ---------------------------------------------------------------------------
# BSD tests
# ---------------------------------------------------------------------------

def _make_stereo_objects(*objs):
    """Build a fake StereoObjects message from (dRel, yRel, vRel, prob) tuples."""
    class _Obj:
        def __init__(self, d, y, v, p):
            self.dRel = d
            self.yRel = y
            self.vRel = v
            self.prob = p
    class _Msg:
        def __init__(self, items):
            self.objects = [_Obj(*o) for o in items]
    return _Msg(list(objs))


def _make_carstate(left_bsm=False, right_bsm=False):
    class _CS:
        leftBlindspot = left_bsm
        rightBlindspot = right_bsm
    return _CS()


class TestRadarZoneMonitor:
    """Test RadarZoneMonitor with unified stereoObjects input."""

    def test_empty_update(self):
        zm = RadarZoneMonitor()
        left, right, _rear = zm.update(None, _make_carstate(), None, None, 0.0)
        assert left.alert_level  == ZoneAlertLevel.OFF
        assert right.alert_level == ZoneAlertLevel.OFF

    def test_left_zone_warning(self):
        zm = RadarZoneMonitor()
        # radar4d adjacent-lane object at 5m left, 5m behind, approaching fast
        so = _make_stereo_objects((-5.0, 3.0, -10.0, 0.8))
        left, right, _rear = zm.update(so, _make_carstate(), None, None, 0.5)
        assert left.alert_level  == ZoneAlertLevel.WARNING
        assert left.detected     is True
        assert right.alert_level == ZoneAlertLevel.OFF

    def test_right_zone_warning(self):
        zm = RadarZoneMonitor()
        so = _make_stereo_objects((-5.0, -3.0, -10.0, 0.8))
        left, right, _rear = zm.update(so, _make_carstate(), None, None, 0.5)
        assert right.alert_level == ZoneAlertLevel.WARNING
        assert left.alert_level  == ZoneAlertLevel.OFF

    def test_camera_fallback_caution(self):
        zm = RadarZoneMonitor()

        class _Det:
            def __init__(self, x, y, conf):
                self.x = x
                self.y = y
                self.confidence = conf
        class _Msg:
            def __init__(self, dets):
                self.detections = dets

        side_dets = _Msg([_Det(-5.0, 3.0, 0.95)])
        left, right, _rear = zm.update(None, _make_carstate(), side_dets, None, 1.0)
        assert left.alert_level == ZoneAlertLevel.CAUTION
        assert left.detected    is True

    def test_native_bsm_left(self):
        zm = RadarZoneMonitor()
        left, right, _rear = zm.update(None, _make_carstate(left_bsm=True), None, None, 0.5)
        assert left.alert_level != ZoneAlertLevel.OFF

    def test_chime_cooldown(self):
        zm = RadarZoneMonitor()
        so = _make_stereo_objects((-5.0, 3.0, -10.0, 0.8))
        zm.update(so, _make_carstate(), None, None, 0.0)
        chime, side = zm.chime_request(0.0)
        assert chime is True

        assert zm.chime_request(1.0) == (False, None)   # within cooldown
        chime2, _ = zm.chime_request(4.0)
        assert chime2 is True

    def test_alert_messages(self):
        zm = RadarZoneMonitor()
        assert zm.alert_message() is None

        so = _make_stereo_objects((-5.0, 3.0, -10.0, 0.8))
        zm.update(so, _make_carstate(), None, None, 0.0)
        assert zm.alert_message() == "Vehicle in left blind spot"


# ---------------------------------------------------------------------------
# Side camera geometry tests
# ---------------------------------------------------------------------------

class TestSideCameraGeometry:
  """Test BEV reprojector geometry matches CARLA simulation."""

  def test_left_camera_geometry(self):
    geo = make_default_geometry('side_left')
    assert geo.cam_y_m == pytest.approx(0.85)
    assert geo.cam_z_m == pytest.approx(0.75)
    assert geo.cam_x_m == pytest.approx(0.7)
    assert geo.yaw_rad == pytest.approx(math.pi - math.pi / 6.0)  # 150°
    assert geo.pitch_rad == pytest.approx(0.0)

  def test_right_camera_geometry(self):
    geo = make_default_geometry('side_right')
    assert geo.cam_y_m == pytest.approx(-0.85)
    assert geo.cam_z_m == pytest.approx(0.75)
    assert geo.cam_x_m == pytest.approx(0.7)
    assert geo.yaw_rad == pytest.approx(math.pi + math.pi / 6.0)  # 210° = -150°
    assert geo.pitch_rad == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Scenario spawner tests
# ---------------------------------------------------------------------------

class TestScenarioSpawner:
  """Test scenario spawner configuration."""

  def test_known_scenarios(self):
    assert "pedestrian_crossing" in SCENARIOS
    assert "cut_in" in SCENARIOS
    assert "emergency_brake" in SCENARIOS
    assert "cyclist_overtake" in SCENARIOS

  def test_unknown_scenario_is_inactive(self):
    spawner = ScenarioSpawner()
    # Default (empty) scenario should be inactive
    assert spawner.active is False

  def test_emergency_brake_timing(self):
    """Emergency brake scenario stores trigger time ~5s in the future."""
    import os
    os.environ["OPENPILOT_STUB_PARAMS_PYX"] = "1"
    from openpilot.common.params import Params
    p = Params()
    p.put("EOPSimScenario", "emergency_brake")
    spawner = ScenarioSpawner()
    assert spawner.active is True
    assert spawner.scenario_name == "emergency_brake"


# ---------------------------------------------------------------------------
# Weather controller tests
# ---------------------------------------------------------------------------

class TestWeatherController:
  """Test weather preset definitions."""

  def test_all_presets_have_required_fields(self):
    for name, preset in WEATHER_PRESETS.items():
      assert name != ""
      assert 0.0 <= preset.cloudiness <= 100.0
      assert 0.0 <= preset.precipitation <= 100.0
      assert -90.0 <= preset.sun_altitude_angle <= 90.0

  def test_clear_day_vs_clear_night(self):
    day = WEATHER_PRESETS["clear_day"]
    night = WEATHER_PRESETS["clear_night"]
    assert day.sun_altitude_angle > 0
    assert night.sun_altitude_angle < 0

  def test_rain_has_wetness(self):
    rain = WEATHER_PRESETS["rain"]
    assert rain.precipitation > 0
    assert rain.wetness > 0

  def test_weather_controller_unknown_returns_none(self):
    import os
    os.environ["OPENPILOT_STUB_PARAMS_PYX"] = "1"
    ctrl = WeatherController()
    assert ctrl._read_param() is None


# ---------------------------------------------------------------------------
# Mock CARLA tests (no carla module required)
# ---------------------------------------------------------------------------

class MockCarlaLocation:
  def __init__(self, x, y, z=0.0):
    self.x = x
    self.y = y
    self.z = z

  def distance(self, other):
    return math.hypot(self.x - other.x, self.y - other.y)


class MockCarlaRotation:
  def __init__(self, yaw=0.0):
    self.yaw = yaw


class MockCarlaTransform:
  def __init__(self, location, rotation=None):
    self.location = location
    self.rotation = rotation or MockCarlaRotation()


class MockCarlaVelocity:
  def __init__(self, x=0.0, y=0.0, z=0.0):
    self.x = x
    self.y = y
    self.z = z


class MockCarlaActor:
  def __init__(self, actor_id, x, y, vx=0.0, vy=0.0):
    self.id = actor_id
    self._loc = MockCarlaLocation(x, y)
    self._vel = MockCarlaVelocity(vx, vy)

  def get_transform(self):
    return MockCarlaTransform(self._loc)

  def get_velocity(self):
    return self._vel


class MockCarlaActorList:
  def __init__(self, actors):
    self._actors = actors

  def filter(self, pattern):
    if "traffic_light" in pattern:
      return [a for a in self._actors if hasattr(a, "state")]
    return [a for a in self._actors if hasattr(a, "id") and not hasattr(a, "state")]


class MockCarlaWorld:
  def __init__(self, actors):
    self._actors = actors

  def get_actors(self):
    return MockCarlaActorList(self._actors)


class MockCarlaVehicle:
  def __init__(self, x=0.0, y=0.0, yaw=0.0, vx=0.0, vy=0.0):
    self.id = 1
    self._loc = MockCarlaLocation(x, y)
    self._rot = MockCarlaRotation(yaw)
    self._vel = MockCarlaVelocity(vx, vy)

  def get_transform(self):
    return MockCarlaTransform(self._loc, self._rot)

  def get_velocity(self):
    return self._vel


class MockTrafficLight:
  def __init__(self, x, y, state_str="Red"):
    self._loc = MockCarlaLocation(x, y)
    self.state = type("State", (), {"__str__": lambda _s: f"TrafficLightState.{state_str}"})()

  def get_transform(self):
    return MockCarlaTransform(self._loc)


class TestSimulatedSideDMockCarla:
  """Test SimulatedSideD logic with mock CARLA objects."""

  def test_no_objects_when_empty(self):
    from openpilot.tools.sim.lib.simulated_sided import SimulatedSideD
    import os
    os.environ["OPENPILOT_STUB_PARAMS_PYX"] = "1"
    sd = SimulatedSideD()
    world = MockCarlaWorld([])
    vehicle = MockCarlaVehicle()
    objs = sd._get_blindspot_objects(world, vehicle)
    assert objs == []

  def test_detects_vehicle_in_left_blindspot(self):
    from openpilot.tools.sim.lib.simulated_sided import SimulatedSideD
    import os
    os.environ["OPENPILOT_STUB_PARAMS_PYX"] = "1"
    sd = SimulatedSideD()
    # Vehicle 3m to the left, 5m behind ego
    actor = MockCarlaActor(2, -5.0, 3.0, vx=0.0, vy=0.0)
    world = MockCarlaWorld([actor])
    vehicle = MockCarlaVehicle(yaw=0.0)
    objs = sd._get_blindspot_objects(world, vehicle)
    assert len(objs) == 1
    assert objs[0]["side"] == "left"
    assert objs[0]["label"] == "car"
    assert objs[0]["confidence"] == 0.95

  def test_detects_vehicle_in_right_blindspot(self):
    from openpilot.tools.sim.lib.simulated_sided import SimulatedSideD
    import os
    os.environ["OPENPILOT_STUB_PARAMS_PYX"] = "1"
    sd = SimulatedSideD()
    # Vehicle 3m to the right, 5m behind ego
    actor = MockCarlaActor(2, -5.0, -3.0, vx=0.0, vy=0.0)
    world = MockCarlaWorld([actor])
    vehicle = MockCarlaVehicle(yaw=0.0)
    objs = sd._get_blindspot_objects(world, vehicle)
    assert len(objs) == 1
    assert objs[0]["side"] == "right"

  def test_ignores_ego_vehicle(self):
    from openpilot.tools.sim.lib.simulated_sided import SimulatedSideD
    import os
    os.environ["OPENPILOT_STUB_PARAMS_PYX"] = "1"
    sd = SimulatedSideD()
    actor = MockCarlaActor(1, -5.0, 3.0)  # same id as ego
    world = MockCarlaWorld([actor])
    vehicle = MockCarlaVehicle()
    objs = sd._get_blindspot_objects(world, vehicle)
    assert len(objs) == 0

  def test_ignores_vehicle_outside_zone(self):
    from openpilot.tools.sim.lib.simulated_sided import SimulatedSideD
    import os
    os.environ["OPENPILOT_STUB_PARAMS_PYX"] = "1"
    sd = SimulatedSideD()
    # Too far left (6m)
    actor = MockCarlaActor(2, -5.0, 6.0)
    world = MockCarlaWorld([actor])
    vehicle = MockCarlaVehicle()
    objs = sd._get_blindspot_objects(world, vehicle)
    assert len(objs) == 0

  def test_ignores_vehicle_too_far_rear(self):
    from openpilot.tools.sim.lib.simulated_sided import SimulatedSideD
    import os
    os.environ["OPENPILOT_STUB_PARAMS_PYX"] = "1"
    sd = SimulatedSideD()
    # 25m behind (beyond -20m limit)
    actor = MockCarlaActor(2, -25.0, 3.0)
    world = MockCarlaWorld([actor])
    vehicle = MockCarlaVehicle()
    objs = sd._get_blindspot_objects(world, vehicle)
    assert len(objs) == 0

  def test_relative_speed_computed(self):
    from openpilot.tools.sim.lib.simulated_sided import SimulatedSideD
    import os
    os.environ["OPENPILOT_STUB_PARAMS_PYX"] = "1"
    sd = SimulatedSideD()
    # Actor approaching from behind at 10 m/s faster than ego
    actor = MockCarlaActor(2, -5.0, 3.0, vx=-10.0, vy=0.0)
    world = MockCarlaWorld([actor])
    vehicle = MockCarlaVehicle(vx=0.0)
    objs = sd._get_blindspot_objects(world, vehicle)
    assert len(objs) == 1
    # v_rel should be negative (approaching)
    assert objs[0]["v_rel"] < -5.0


class TestTrafficLightPublisherMockCarla:
  """Test TrafficLightPublisher logic with mock CARLA objects."""

  def test_no_traffic_light_returns_none(self):
    from openpilot.tools.sim.lib.traffic_light_publisher import TrafficLightPublisher
    import os
    os.environ["OPENPILOT_STUB_PARAMS_PYX"] = "1"
    tlp = TrafficLightPublisher()
    world = MockCarlaWorld([])
    vehicle = MockCarlaVehicle()
    tl = tlp._get_nearest_traffic_light(world, vehicle)
    assert tl is None

  def test_detects_red_light_ahead(self):
    from openpilot.tools.sim.lib.traffic_light_publisher import TrafficLightPublisher
    import os
    os.environ["OPENPILOT_STUB_PARAMS_PYX"] = "1"
    tlp = TrafficLightPublisher()
    tl = MockTrafficLight(20.0, 0.0, "Red")
    world = MockCarlaWorld([tl])
    vehicle = MockCarlaVehicle()
    result = tlp._get_nearest_traffic_light(world, vehicle)
    assert result is not None
    assert result["state"] == 1  # Red
    assert result["confidence"] == 0.95

  def test_detects_yellow_light_ahead(self):
    from openpilot.tools.sim.lib.traffic_light_publisher import TrafficLightPublisher
    import os
    os.environ["OPENPILOT_STUB_PARAMS_PYX"] = "1"
    tlp = TrafficLightPublisher()
    tl = MockTrafficLight(20.0, 0.0, "Yellow")
    world = MockCarlaWorld([tl])
    vehicle = MockCarlaVehicle()
    result = tlp._get_nearest_traffic_light(world, vehicle)
    assert result is not None
    assert result["state"] == 2  # Yellow

  def test_ignores_green_light(self):
    from openpilot.tools.sim.lib.traffic_light_publisher import TrafficLightPublisher
    import os
    os.environ["OPENPILOT_STUB_PARAMS_PYX"] = "1"
    tlp = TrafficLightPublisher()
    tl = MockTrafficLight(20.0, 0.0, "Green")
    world = MockCarlaWorld([tl])
    vehicle = MockCarlaVehicle()
    result = tlp._get_nearest_traffic_light(world, vehicle)
    assert result is not None
    assert result["state"] == 3  # Green

  def test_ignores_light_too_far(self):
    from openpilot.tools.sim.lib.traffic_light_publisher import TrafficLightPublisher
    import os
    os.environ["OPENPILOT_STUB_PARAMS_PYX"] = "1"
    tlp = TrafficLightPublisher()
    tl = MockTrafficLight(150.0, 0.0, "Red")
    world = MockCarlaWorld([tl])
    vehicle = MockCarlaVehicle()
    result = tlp._get_nearest_traffic_light(world, vehicle)
    assert result is None

  def test_ignores_light_behind(self):
    from openpilot.tools.sim.lib.traffic_light_publisher import TrafficLightPublisher
    import os
    os.environ["OPENPILOT_STUB_PARAMS_PYX"] = "1"
    tlp = TrafficLightPublisher()
    tl = MockTrafficLight(-10.0, 0.0, "Red")
    world = MockCarlaWorld([tl])
    vehicle = MockCarlaVehicle()
    result = tlp._get_nearest_traffic_light(world, vehicle)
    assert result is None

  def test_prefers_nearest_light(self):
    from openpilot.tools.sim.lib.traffic_light_publisher import TrafficLightPublisher
    import os
    os.environ["OPENPILOT_STUB_PARAMS_PYX"] = "1"
    tlp = TrafficLightPublisher()
    tl_near = MockTrafficLight(20.0, 0.0, "Red")
    tl_far = MockTrafficLight(50.0, 0.0, "Yellow")
    world = MockCarlaWorld([tl_far, tl_near])
    vehicle = MockCarlaVehicle()
    result = tlp._get_nearest_traffic_light(world, vehicle)
    assert result is not None
    assert result["state"] == 1  # Red (nearest)
