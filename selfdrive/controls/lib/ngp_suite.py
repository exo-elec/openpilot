"""Composition root for the minimized NGP10 application feature layer."""

from dataclasses import dataclass
from enum import IntEnum

from openpilot.selfdrive.adaptd.ngp_profile import NGPAdaptiveProfile
from openpilot.selfdrive.controls.lib.ngp_alcc import NGPALCC
from openpilot.selfdrive.controls.lib.ngp_coasting import NGPCoasting
from openpilot.selfdrive.controls.lib.ngp_collision import NGPCollisionRisk
from openpilot.selfdrive.controls.lib.ngp_dlat import NGPDLAT
from openpilot.selfdrive.controls.lib.ngp_dlon import NGPDLON
from openpilot.selfdrive.controls.lib.ngp_lca import NGPLCA
from openpilot.selfdrive.controls.lib.ngp_mtsc import NGPMTSC
from openpilot.selfdrive.controls.lib.ngp_radar import NGPRadarTracker
from openpilot.selfdrive.controls.lib.ngp_road_condition import NGPRoadCondition
from openpilot.selfdrive.controls.lib.ngp_speed_policy import NGPSpeedPolicy
from openpilot.selfdrive.controls.lib.ngp_traffic_control import NGPTrafficControl
from openpilot.selfdrive.controls.lib.ngp_vtsc import NGPVTSC
from openpilot.selfdrive.gridd.lazy_bev import NGPLazyBEV
from openpilot.selfdrive.gridd.ngp_capabilities import NGPCapabilities
from openpilot.selfdrive.monod.ngp_monod import NGPMonoD
from openpilot.selfdrive.pathd.ngp_soc import NGPSOC
from openpilot.selfdrive.tripd.ngp_trip import NGPTripStats


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


class NGPFeatureSuite:
  """Own all portable feature state without changing upstream control paths."""

  def __init__(self, capabilities: NGPCapabilities | None = None, monod_backend=None):
    self.capabilities = capabilities or NGPCapabilities.comma3()
    self.dlat = NGPDLAT()
    self.dlon = NGPDLON()
    self.coasting = NGPCoasting()
    self.collision_risk = NGPCollisionRisk()
    self.vtsc = NGPVTSC()
    self.mtsc = NGPMTSC()
    self.speed_policy = NGPSpeedPolicy()
    self.alcc = NGPALCC()
    self.radar = NGPRadarTracker()
    self.road_condition = NGPRoadCondition()
    self.traffic_control = NGPTrafficControl()
    self.lca = NGPLCA()
    self.bev = NGPLazyBEV()
    self.monod = NGPMonoD(enabled=False, backend=monod_backend)
    self.soc = NGPSOC()
    self.adaptive_profile = NGPAdaptiveProfile()
    self.trip_stats = NGPTripStats()

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
