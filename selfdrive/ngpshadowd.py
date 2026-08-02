#!/usr/bin/env python3
"""Publish the minimized NGP10 feature suite as non-controlling diagnostics."""

import time

from cereal import messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.selfdrive.controls.lib.ngp_alcc import ALCCInput
from openpilot.selfdrive.controls.lib.ngp_dlon import DLONInput
from openpilot.selfdrive.controls.lib.ngp_lca import LCAInput
from openpilot.selfdrive.controls.lib.ngp_radar import RadarObservation, RadarSource
from openpilot.selfdrive.controls.lib.ngp_road_edge import evaluate_road_edges
from openpilot.selfdrive.controls.lib.ngp_speed_policy import SpeedLimitObservation, SpeedLimitSource
from openpilot.selfdrive.controls.lib.ngp_suite import NGP10FeatureSuite
from openpilot.selfdrive.gridd.ngp_capabilities import NGP10Capabilities
from openpilot.selfdrive.pathd.ngp_soc import SOCInput


class NGPShadowD:
  def __init__(self):
    self.suite = NGP10FeatureSuite()
    self.pm = messaging.PubMaster(["ngpState"])
    self.sm = messaging.SubMaster([
      "modelV2", "carState", "radarState", "liveTracks",
      "driverMonitoringState", "navInstruction",
    ])
    self._last_update_time = time.monotonic()

  @staticmethod
  def _radar_observations(radar_data):
    return tuple(RadarObservation(
      track_id=int(point.trackId), d_rel=float(point.dRel), y_rel=float(point.yRel),
      v_rel=float(point.vRel), probability=1.0,
      source=RadarSource.RADAR_2D,
    ) for point in radar_data.points)

  def update(self):
    self.sm.update(0)
    cs = self.sm["carState"]
    model = self.sm["modelV2"]
    v_ego = float(cs.vEgo)

    dlat = self.suite.dlat.update_model(model if self.sm.valid["modelV2"] else None, v_ego)
    vtsc = self.suite.vtsc.update(v_ego, model if self.sm.valid["modelV2"] else None)

    lead = self.sm["radarState"].leadOne
    dlon = self.suite.dlon.evaluate(DLONInput(
      v_ego=v_ego,
      has_lead=bool(lead.status),
      lead_delta_v=float(lead.vRel) if lead.status else 0.0,
      turn_signal=bool(cs.leftBlinker or cs.rightBlinker),
      curve_lat_acc=vtsc.predicted_lat_acc,
      should_stop=bool(model.action.shouldStop) if self.sm.valid["modelV2"] else False,
    ))

    timestamp = time.monotonic()
    observations = self._radar_observations(self.sm["liveTracks"]) if self.sm.valid["liveTracks"] else ()
    tracks = self.suite.radar.update(observations, timestamp)
    collision = self.suite.collision_risk.evaluate(v_ego, tracks)
    zones = self.suite.radar.with_vehicle_bsm(
      self.suite.radar.assess_zones(tracks), bool(cs.leftBlindspot), bool(cs.rightBlindspot),
    )

    road_edges = evaluate_road_edges(model.roadEdgeStds, model.laneLineProbs) if self.sm.valid["modelV2"] \
      else evaluate_road_edges((), ())
    dm = self.sm["driverMonitoringState"]
    driver_attentive = not bool(dm.isDistracted) and float(dm.awarenessStatus) > 0.0
    lane_probs = tuple(float(value) for value in model.laneLineProbs) if self.sm.valid["modelV2"] else ()
    lca = self.suite.lca.update(LCAInput(
      enabled=True,
      v_ego=v_ego,
      left_blinker=bool(cs.leftBlinker),
      right_blinker=bool(cs.rightBlinker),
      driver_nudge=bool(cs.steeringPressed),
      driver_attentive=driver_attentive,
      left_lane_available=len(lane_probs) >= 3 and lane_probs[1] >= 0.5,
      right_lane_available=len(lane_probs) >= 3 and lane_probs[2] >= 0.5,
      left_road_edge=road_edges.left_blocked,
      right_road_edge=road_edges.right_blocked,
    ), zones)
    alcc = self.suite.alcc.update(ALCCInput(
      feature_enabled=True,
      engage_request=bool(cs.cruiseState.enabled),
      user_disable=not bool(cs.cruiseState.enabled),
      steering_override=bool(cs.steeringPressed),
    ))

    speed_observations = ()
    nav = self.sm["navInstruction"]
    if self.sm.valid["navInstruction"] and nav.speedLimit > 0.0:
      speed_observations = (SpeedLimitObservation(SpeedLimitSource.NAVIGATION, float(nav.speedLimit)),)
    speed = self.suite.speed_policy.evaluate(v_ego, float(cs.cruiseState.speed), speed_observations)

    lane_lines = tuple(tuple(float(y) for y in line.y) for line in model.laneLines) if self.sm.valid["modelV2"] else ()
    lane_stds = tuple(float(value) for value in model.laneLineStds) if self.sm.valid["modelV2"] else ()
    soc = self.suite.soc.update(SOCInput(
      v_ego, zones.left_detected, zones.right_detected, lane_lines, lane_probs, lane_stds,
    ))
    capabilities = NGP10Capabilities.comma3()
    capabilities = NGP10Capabilities(
      cameras=capabilities.cameras,
      driver_camera=bool(dm.faceDetected),
      radar_2d=bool(tracks),
    )
    bev = self.suite.bev.update(tracks, capabilities, metric_observations=bool(tracks))
    dt = max(0.0, min(1.0, timestamp - self._last_update_time))
    self._last_update_time = timestamp
    trip = self.suite.trip_stats.update(
      v_ego=v_ego,
      a_ego=float(cs.aEgo),
      engaged=bool(cs.cruiseState.enabled),
      driver_override=bool(cs.gasPressed or cs.brakePressed or cs.steeringPressed),
      dt=dt,
    )

    msg = messaging.new_message("ngpState")
    state = msg.ngpState
    state.controlAuthority = False
    state.modelValid = dlat.model_valid
    state.dlatSuggestion = 1 if dlat.suggestion.value == "laneless" else 0
    state.dlatLaneConfidence = dlat.lane_confidence
    state.dlatPathConfidence = dlat.path_confidence
    state.dlatModelConfidence = dlat.model_confidence
    state.dlatHasPathDeviation = dlat.path_deviation is not None
    state.dlatPathDeviation = dlat.path_deviation or 0.0
    state.dlonE2eSuggestion = dlon.e2e_suggestion
    state.dlonTriggers = list(dlon.triggers)
    state.dlonForceStopSuggestion = dlon.force_stop_suggestion
    state.vtscState = list(type(vtsc.state)).index(vtsc.state)
    state.vtscHasTarget = vtsc.target_speed is not None
    state.vtscTargetSpeed = vtsc.target_speed or 0.0
    state.vtscPredictedLatAccel = vtsc.predicted_lat_acc
    state.speedZone = int(speed.zone)
    state.speedLimitSource = int(speed.source)
    state.speedLimitValid = speed.resolved_limit_mps is not None
    state.speedLimit = speed.resolved_limit_mps or 0.0
    state.alccState = int(alcc.state)
    state.alccActiveSuggestion = alcc.active_suggestion
    state.lcaState = int(lca.state)
    state.lcaDirection = int(lca.direction)
    state.lcaSafeToStart = lca.safe_to_start
    state.lcaDesireSuggestion = lca.desire_suggestion
    state.lcaBlockedReasons = list(lca.blocked_reasons)
    state.roadEdgeValid = road_edges.valid
    state.leftRoadEdge = road_edges.left_blocked
    state.rightRoadEdge = road_edges.right_blocked
    state.radarTrackCount = len(tracks)
    state.radarLeftBlocked = zones.lca_blocked_left
    state.radarRightBlocked = zones.lca_blocked_right
    state.socActiveSuggestion = soc.active_suggestion
    state.socOffset = soc.offset_m
    state.bevAvailable = bev.available
    state.bevCellCount = len(bev.cells)
    state.collisionLevel = int(collision.level)
    state.collisionTrackValid = collision.track_id is not None
    state.collisionTrackId = collision.track_id or 0
    state.collisionTtc = collision.ttc
    state.collisionSafeDistance = collision.safe_distance
    state.tripDistance = trip.distance_m
    state.tripEngagementRatio = trip.engagement_ratio
    self.pm.send("ngpState", msg)


def main():
  daemon = NGPShadowD()
  ratekeeper = Ratekeeper(20.0)
  while True:
    daemon.update()
    ratekeeper.keep_time()


if __name__ == "__main__":
  main()
