"""Composition root for the minimized NGP10 application feature layer."""

from dataclasses import dataclass
from enum import IntEnum

from openpilot.selfdrive.adaptd.ngp_profile import NGPAdaptiveProfile
from nagaspilot.controls.ngp_alcc import NGPALCC
from nagaspilot.controls.ngp_coasting import NGPCoasting
from nagaspilot.controls.ngp_collision import NGPCollisionRisk
from nagaspilot.controls.ngp_dlat import NGPDLAT
from nagaspilot.controls.ngp_dlon import NGPDLON
from nagaspilot.controls.ngp_lca import NGPLCA
from nagaspilot.controls.ngp_mtsc import NGPMTSC
from nagaspilot.controls.ngp_radar import NGPRadarTracker
from nagaspilot.controls.ngp_road_condition import NGPRoadCondition
from nagaspilot.controls.ngp_speed_policy import NGPSpeedPolicy
from nagaspilot.controls.ngp_traffic_control import NGPTrafficControl
from nagaspilot.controls.ngp_tja import TrafficJamAssist
from nagaspilot.controls.ngp_vtsc import NGPVTSC
from openpilot.selfdrive.gridd.lazy_bev import NGPLazyBEV
from openpilot.selfdrive.gridd.ngp_capabilities import NGPCapabilities
from openpilot.selfdrive.monod.ngp_monod import NGPMonoD
from openpilot.selfdrive.pathd.ngp_soc import NGPSOC
from openpilot.selfdrive.tripd.ngp_trip import NGPTripStats


class PortState(IntEnum):
  INTEGRATED = 0
  PORTABLE = 1
  EXTERNAL = 2
  EXCLUDED = 3


@dataclass(frozen=True)
class FeaturePort:
  name: str
  state: PortState
  replacement: str
  control_authority: bool = False


class NGPFeatureSuite:
  """Inventory and construct the minimized NGP application features."""

  def __init__(self, capabilities: NGPCapabilities | None = None, monod_backend=None):
    self.capabilities = capabilities or NGPCapabilities.comma3()
    self.dlat = NGPDLAT()
    self.dlon = NGPDLON()
    self.coasting = NGPCoasting()
    self.tja = TrafficJamAssist(0.05)
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
      FeaturePort("DLAT", PortState.PORTABLE, "EOP10 DLAT"),
      FeaturePort("DLON", PortState.INTEGRATED, "EOP10 DLON", True),
      FeaturePort("adaptive_coasting", PortState.INTEGRATED, "EDP10 ACM", True),
      FeaturePort("TJA", PortState.INTEGRATED, "EOP10/EDP10 traffic-jam assist", True),
      FeaturePort("collision_risk", PortState.PORTABLE, "EOP10 AEB predictor; stock AEB retained"),
      FeaturePort("VTSC", PortState.PORTABLE, "EOP10 VTSC; no EDP implementation"),
      FeaturePort("MTSC", PortState.PORTABLE, "EOP10 MTSC"),
      FeaturePort("speed_policy", PortState.PORTABLE, "EOP10 MSLC/NSLC/resolver"),
      FeaturePort("ALCC", PortState.INTEGRATED, "EOP10 and EDP10 ALCC presentation", True),
      FeaturePort("LCA", PortState.INTEGRATED, "EOP10 LCA and EDP10 LCA/road-edge gate", True),
      FeaturePort("normalized_radar", PortState.PORTABLE, "EOP10 radar zones via upstream liveTracks"),
      FeaturePort("GridD", PortState.PORTABLE, "EOP10 GridD sparse portable subset"),
      FeaturePort("MonoD", PortState.PORTABLE, "EOP10 MonoD backend boundary"),
      FeaturePort("SOC", PortState.PORTABLE, "EOP10 PathD SOC"),
      FeaturePort("overlays", PortState.PORTABLE, "EOP10 side/rear overlays"),
      FeaturePort("adaptive_telemetry", PortState.PORTABLE, "EOP10 adaptd computer"),
      FeaturePort("road_condition", PortState.PORTABLE, "EOP10 RCD/SQSC policy without camera classifier"),
      FeaturePort("traffic_control", PortState.PORTABLE, "EOP10 TLSC policy without actuation"),
      FeaturePort("route_curvature", PortState.PORTABLE, "EOP10 mapd curvature math without OSM daemon"),
      FeaturePort("trip_stats", PortState.PORTABLE, "EOP10 TripD in-memory subset"),
      FeaturePort("vehicle_stack", PortState.EXTERNAL, "upstream OpenDBC + TC275 Tesla gateway"),
      FeaturePort("RK3588_HAL_stereo_radar4d", PortState.EXCLUDED, "not applicable to comma 3"),
    )
