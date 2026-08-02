"""Composition root for the minimized NGP10 application feature layer."""

from dataclasses import dataclass
from enum import IntEnum

from openpilot.selfdrive.adaptd.ngp_profile import NGP10AdaptiveProfile
from openpilot.selfdrive.controls.lib.ngp_alcc import NGP10ALCC
from openpilot.selfdrive.controls.lib.ngp_coasting import NGP10Coasting
from openpilot.selfdrive.controls.lib.ngp_collision import NGP10CollisionRisk
from openpilot.selfdrive.controls.lib.ngp_dlat import NGP10DLAT
from openpilot.selfdrive.controls.lib.ngp_dlon import NGP10DLON
from openpilot.selfdrive.controls.lib.ngp_lca import NGP10LCA
from openpilot.selfdrive.controls.lib.ngp_mtsc import NGP10MTSC
from openpilot.selfdrive.controls.lib.ngp_radar import NGP10RadarTracker
from openpilot.selfdrive.controls.lib.ngp_road_condition import NGP10RoadCondition
from openpilot.selfdrive.controls.lib.ngp_speed_policy import NGP10SpeedPolicy
from openpilot.selfdrive.controls.lib.ngp_traffic_control import NGP10TrafficControl
from openpilot.selfdrive.controls.lib.ngp_vtsc import NGP10VTSC
from openpilot.selfdrive.gridd.lazy_bev import NGP10LazyBEV
from openpilot.selfdrive.gridd.ngp_capabilities import NGP10Capabilities
from openpilot.selfdrive.monod.ngp_monod import NGP10MonoD
from openpilot.selfdrive.pathd.ngp_soc import NGP10SOC
from openpilot.selfdrive.tripd.ngp_trip import NGP10TripStats


class PortState(IntEnum):
  SHADOW = 0
  PROPOSAL = 1
  EXTERNAL = 2
  EXCLUDED = 3


@dataclass(frozen=True)
class FeaturePort:
  name: str
  state: PortState
  replacement: str
  control_authority: bool = False


class NGP10FeatureSuite:
  """Own all portable feature state without changing upstream control paths."""

  def __init__(self, capabilities: NGP10Capabilities | None = None, monod_backend=None):
    self.capabilities = capabilities or NGP10Capabilities.comma3()
    self.dlat = NGP10DLAT()
    self.dlon = NGP10DLON()
    self.coasting = NGP10Coasting()
    self.collision_risk = NGP10CollisionRisk()
    self.vtsc = NGP10VTSC()
    self.mtsc = NGP10MTSC()
    self.speed_policy = NGP10SpeedPolicy()
    self.alcc = NGP10ALCC()
    self.radar = NGP10RadarTracker()
    self.road_condition = NGP10RoadCondition()
    self.traffic_control = NGP10TrafficControl()
    self.lca = NGP10LCA()
    self.bev = NGP10LazyBEV()
    self.monod = NGP10MonoD(enabled=False, backend=monod_backend)
    self.soc = NGP10SOC()
    self.adaptive_profile = NGP10AdaptiveProfile()
    self.trip_stats = NGP10TripStats()

  @staticmethod
  def manifest() -> tuple[FeaturePort, ...]:
    return (
      FeaturePort("DLAT", PortState.SHADOW, "EOP10 DLAT"),
      FeaturePort("DLON", PortState.SHADOW, "EOP10 DLON and EDP10 AEM"),
      FeaturePort("adaptive_coasting", PortState.PROPOSAL, "EDP10 ACM"),
      FeaturePort("collision_risk", PortState.SHADOW, "EOP10 AEB predictor; stock AEB retained"),
      FeaturePort("VTSC", PortState.PROPOSAL, "EOP10/EDP10 VTSC"),
      FeaturePort("MTSC", PortState.PROPOSAL, "EOP10 MTSC"),
      FeaturePort("speed_policy", PortState.PROPOSAL, "EOP10 MSLC/NSLC/resolver"),
      FeaturePort("ALCC", PortState.PROPOSAL, "EOP10 ALCC and EDP10 ALKA"),
      FeaturePort("LCA", PortState.PROPOSAL, "EOP10 LCA and EDP10 LCA/road-edge gate"),
      FeaturePort("radar2d_radar3d", PortState.SHADOW, "EOP10 radar zones"),
      FeaturePort("GridD", PortState.SHADOW, "EOP10 GridD sparse portable subset"),
      FeaturePort("MonoD", PortState.SHADOW, "EOP10 MonoD backend boundary"),
      FeaturePort("SOC", PortState.PROPOSAL, "EOP10 PathD SOC"),
      FeaturePort("overlays", PortState.SHADOW, "EOP10 side/rear overlays"),
      FeaturePort("adaptive_telemetry", PortState.PROPOSAL, "EOP10 adaptd computer"),
      FeaturePort("road_condition", PortState.PROPOSAL, "EOP10 RCD/SQSC policy without camera classifier"),
      FeaturePort("traffic_control", PortState.PROPOSAL, "EOP10 TLSC policy without actuation"),
      FeaturePort("route_curvature", PortState.PROPOSAL, "EOP10 mapd curvature math without OSM daemon"),
      FeaturePort("trip_stats", PortState.SHADOW, "EOP10 TripD in-memory subset"),
      FeaturePort("vehicle_stack", PortState.EXTERNAL, "upstream OpenDBC + TC275 Tesla gateway"),
      FeaturePort("RK3588_HAL_stereo_radar4d", PortState.EXCLUDED, "not applicable to comma 3"),
    )
