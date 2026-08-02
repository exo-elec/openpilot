from types import SimpleNamespace

from openpilot.selfdrive.adaptd.ngp_profile import AdaptivePersonality, NGPAdaptiveProfile, VehicleTelemetry
from nagaspilot.controls.ngp_alcc import ALCCInput, ALCCState, NGPALCC
from nagaspilot.controls.ngp_coasting import CoastingInput, NGPCoasting
from nagaspilot.controls.ngp_collision import CollisionLevel, NGPCollisionRisk
from nagaspilot.controls.ngp_lca import LCAInput, LCAState, NGPLCA
from nagaspilot.controls.ngp_radar import NGPRadarTracker, RadarObservation, RadarSource, RadarZones
from nagaspilot.controls.ngp_road_condition import (
  NGPRoadCondition, RoadCondition, RoadConditionObservation,
)
from nagaspilot.controls.ngp_road_edge import evaluate_road_edges
from nagaspilot.controls.ngp_speed_policy import (
  NGPSpeedPolicy, SpeedLimitObservation, SpeedLimitPolicy, SpeedLimitSource,
)
from nagaspilot.controls.ngp_suite import NGPFeatureSuite
from nagaspilot.controls.ngp_traffic_control import (
  NGPTrafficControl, TrafficControlObservation, TrafficControlState,
)


def test_speed_policy_uses_map_nav_then_car_fallback_without_control():
  policy = NGPSpeedPolicy(SpeedLimitPolicy.MAP_NAV_WITH_CAR_FALLBACK)
  car = SpeedLimitObservation(SpeedLimitSource.CAR, 15.0)
  nav = SpeedLimitObservation(SpeedLimitSource.NAVIGATION, 20.0)
  result = policy.evaluate(25.0, 30.0, (car, nav))
  assert result.source is SpeedLimitSource.NAVIGATION
  assert result.suggested_cruise_mps == 20.0
  assert not result.control_applied
  fallback = policy.evaluate(10.0, 30.0, (car,))
  assert fallback.source is SpeedLimitSource.CAR


def test_alcc_latches_pauses_and_never_has_authority():
  alcc = NGPALCC()
  result = alcc.update(ALCCInput(True, engage_request=True))
  assert result.state is ALCCState.ENABLED
  assert result.active_suggestion
  assert not result.control_authority
  assert alcc.update(ALCCInput(True, pause_condition=True)).state is ALCCState.PAUSED
  assert alcc.update(ALCCInput(True)).state is ALCCState.ENABLED


def test_lca_requires_nudge_and_respects_radar_gate():
  lca = NGPLCA()
  clear = RadarZones(False, False, False, False, False)
  sample = LCAInput(True, 25.0, left_blinker=True, driver_attentive=True)
  assert lca.update(sample, clear).state is LCAState.PRE_CHANGE
  assert lca.update(LCAInput(True, 25.0, left_blinker=True, driver_nudge=True), clear).state is LCAState.STARTING

  blocked = RadarZones(True, False, False, True, False)
  result = lca.update(LCAInput(True, 25.0, left_blinker=True), blocked)
  assert result.state is LCAState.PRE_CHANGE
  assert "radar_or_blindspot" in result.blocked_reasons
  assert not result.control_authority


def test_normalized_radar_tracks_and_blocks_fast_rear_approach():
  tracker = NGPRadarTracker(smoothing=1.0)
  tracks = tracker.update((RadarObservation(1, -40.0, 3.0, 12.0, 0.9, RadarSource.RADAR_3D),), 1.0)
  zones = tracker.assess_zones(tracks)
  assert zones.left_detected
  assert zones.lca_blocked_left
  assert 3.0 < zones.left_ttc < 3.5
  assert tracker.update((), 2.0) == ()


def test_coasting_is_integrated_while_collision_remains_advisory():
  coast = NGPCoasting().evaluate(CoastingInput(25.0, 20.0))
  assert coast.coast_suggestion and coast.minimum_brake_mps2 == -0.5
  assert coast.control_authority

  track = SimpleNamespace(track_id=7, d_rel=12.0, y_rel=0.2, v_rel=-10.0)
  collision = NGPCollisionRisk().evaluate(25.0, (track,))
  assert collision.level is CollisionLevel.CRITICAL
  assert collision.track_id == 7
  assert not collision.control_authority


def test_road_and_traffic_policies_fail_closed():
  edges = evaluate_road_edges((0.2, 0.9), (0.1, 0.9, 0.9, 0.8))
  assert edges.valid and edges.left_blocked and not edges.right_blocked

  road = NGPRoadCondition().evaluate((RoadConditionObservation(RoadCondition.ICE, 0.8, "gateway"),))
  assert road.condition is RoadCondition.ICE
  assert road.speed_factor == 0.45
  assert not road.control_authority

  traffic = NGPTrafficControl()
  red = TrafficControlObservation(TrafficControlState.RED, 40.0, 0.9)
  assert traffic.evaluate(10.0, (red,), has_lead=False).stop_suggestion
  assert not traffic.evaluate(10.0, (red,), has_lead=True).stop_suggestion


def test_adaptive_profile_and_manifest_distinguish_integrated_features():
  computer = NGPAdaptiveProfile(personality_hysteresis_s=0.0)
  profile = computer.update(VehicleTelemetry(valid=True, battery_soc=8.0, range_remaining_km=20.0), now=1.0)
  assert profile.personality is AdaptivePersonality.RELAXED
  assert profile.accel_max == 0.8
  assert not profile.control_authority
  authority = {feature.name: feature.control_authority for feature in NGPFeatureSuite.manifest()}
  assert authority["DLON"] and authority["adaptive_coasting"] and authority["TJA"]
  assert not authority["collision_risk"] and not authority["VTSC"]
