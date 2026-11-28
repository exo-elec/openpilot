"""One speed-cap adapter for the map-, navigation-, light- and distraction-based controllers (MTSC, MSLC, NSLC, TLSC, DDSC, RCSC).

The planner calls `update` once per cycle and takes `min(v_cruise, cap)`: these controllers only ever LOWER the cruise speed, so
the speed the driver set is never exceeded. Per-controller switches: `MTSCEnabled`, `MSLCEnabled`, `NSLCEnabled`,
`TLSCEnabled`, `DDSCEnabled`, `RCSCEnabled`. The posted limits, map (MSLC, mapdOut) and navigation (NSLC, navInstruction), are
one speed limit: `SpeedPolicy` picks between them by `SpeedLimitPolicy` (default 4, the lower of the two). Both use the same
per-range offsets `SLCOffset1..7` (km/h) and confirmation `SLCConfirmLower` / `SLCConfirmHigher`.
Optional, for boards that have them: `learned_speed(lat, lon, curvature) -> m/s` (a curve-speed store, at mapd's position)
and `lat_accel()` (MTSC's comfort lateral acceleration). Without them MTSC uses the map curvature and 1.8 m/s^2.

Two fixes over EOP10's inline planner code:
  - EOP10 reset the MTSC/MSLC target every cycle and set it only on the cycle a map message arrived (1 Hz): the cap existed for
    one planner cycle in twenty. Here the last result is HELD for MAP_HOLD_S after the last map message.
Map data comes from the offline mapd (third_party/mapd): limits from `mapdOut`, curves from `mapdExtendedOut`'s path.
  - TLSC gets the lights that lie ahead on our planned path (|lateral - path| <= LIGHT_LATERAL_M), not every light in view.
MTSC -> VTSC handover is the shared `blend_mtsc_vtsc` (the planner passes its VTSC target in).
DDSC has no DMS here: its "distracted" input comes from the driver-activity monitor (`driverMonitoringState.awarenessStatus`):
prompt stage (<= 25 %) or lower = distracted (cap 60 km/h as EOP's base), awareness at zero for UNCONSCIOUS_S = unresponsive driver.
Pure apart from reading the SubMaster-like `sm` and the params getter; no cereal import.
"""
import math
import time
from dataclasses import dataclass
from types import SimpleNamespace as NS

from exopilot.selfdrive.controls.lib.curve_speed import blend_mtsc_vtsc, curves_from_path
from exopilot.selfdrive.controls.lib.feature_keys import MAP_SPEED_KEYS
from exopilot.selfdrive.controls.lib.ddsc import DMS_BASE_LIMIT_MPS, DDSC
from exopilot.selfdrive.controls.lib.mslc import MSLC
from exopilot.selfdrive.controls.lib.mtsc import MTSC
from exopilot.selfdrive.controls.lib.nslc import NSLC
from exopilot.selfdrive.controls.lib.speed_policy import SpeedLimitObservation, SpeedLimitPolicy, SpeedLimitSource, SpeedPolicy
from exopilot.selfdrive.controls.lib.tlsc import TLSC, TLSC_CONFIDENCE
from exopilot.selfdrive.controls.lib.cutin_adapter import cutin_path
from exopilot.selfdrive.controls.lib.rcsc import RCSC

MAP_HOLD_S = 3.0              # mapdExtendedOut is 1 Hz: hold a result this long after its last message
LIGHT_HOLD_S = 0.5            # same as EOP10's TLSC stale limit
LIGHT_LATERAL_M = 3.5         # a light for our lane is within this of the planned path
PROMPT_AWARENESS = 0.25
UNCONSCIOUS_S = 20.0
CONFIG_REFRESH_S = 2.0
SLC_OFFSET_KEYS = tuple(f"SLCOffset{i + 1}" for i in range(7))


@dataclass(frozen=True)
class MapSpeedResult:
  cap: float | None
  sources: dict                       # the caps that applied this cycle; 'speed_limit' is the resolved posted limit
  mslc: float | None = None           # map limit target (before the policy), m/s
  nslc: float | None = None           # navigation limit target (before the policy), m/s
  limit: SpeedLimitObservation | None = None    # what the policy chose
  mtsc: float | None = None           # map curve target, m/s
  mtsc_distance: float = math.inf
  mtsc_using_learned: bool = False
  tlsc_distance: float | None = None  # distance to active red/yellow light ahead, m


NO_CAP = MapSpeedResult(None, {})     # nothing enabled: the same answer every cycle, built once


def distraction_status(awareness: float, critical_s: float) -> NS:
  """driverStatus-like input for DDSC from the driver-activity monitor's awareness (1 = fully aware, <= 0 = nothing for too long)."""
  distracted = math.isfinite(awareness) and awareness <= PROMPT_AWARENESS
  return NS(safeSpeedLimitMps=DMS_BASE_LIMIT_MPS if distracted else 0.0, unconsciousActive=bool(distracted and critical_s >= UNCONSCIOUS_S))


class MapSpeed:
  def __init__(self, get_bool=None, get=None, clock=time.monotonic, learned_speed=None, lat_accel=None):
    self._get_bool = get_bool or (lambda k: False)
    self._get = get or (lambda k: None)
    self._clock = clock
    self._lat_accel = lat_accel
    self.flags = dict.fromkeys(('mtsc', 'mslc', 'nslc', 'tlsc', 'ddsc', 'rcsc'), False)
    self._cfg_t = -1e9
    self.mtsc = MTSC(enabled=True, curve_learn_enabled=learned_speed is not None, learned_speed=learned_speed)
    self.mslc, self.nslc, self.tlsc, self.ddsc = MSLC(enabled=True), NSLC(), TLSC(enabled=True), DDSC()
    self.policy = SpeedPolicy(SpeedLimitPolicy.LOWEST)
    self.rcsc = RCSC(lambda k: True)      # gated by flags['rcsc'] below; RCSC's own switch is always on
    self._mslc_t = self._mtsc_t = -1e9
    self._mtsc: tuple[float | None, float] = (None, math.inf)      # (target, distance)
    self._mslc: float | None = None
    self._mtsc_learned = False
    self._light_t = -1e9
    self._light: float | None = None
    self._light_dist: float | None = None
    self._blend_state: bool | None = None   # previous frame had both MTSC and VTSC targets
    self._critical_s = 0.0
    self._last = None

  def _refresh_config(self, now: float) -> None:
    if now - self._cfg_t < CONFIG_REFRESH_S:
      return
    self._cfg_t = now
    self.flags = {k: bool(self._get_bool(MAP_SPEED_KEYS[k])) for k in self.flags}
    offsets = [self._get(k) for k in SLC_OFFSET_KEYS]
    confirm = bool(self._get_bool("SLCConfirmLower")), bool(self._get_bool("SLCConfirmHigher"))
    self.mslc._offset.set_offsets_kph(offsets)
    self.mslc._confirmation.configure(*confirm)
    self.nslc.configure(offsets, *confirm)
    try:
      self.policy.policy = SpeedLimitPolicy(int(self._get("SpeedLimitPolicy")))
    except (TypeError, ValueError):
      self.policy.policy = SpeedLimitPolicy.LOWEST
    if self._lat_accel is not None:
      try:
        self.mtsc.a_comfort = float(self._lat_accel())
      except (TypeError, ValueError):
        pass

  @staticmethod
  def _fresh(sm, name: str) -> bool:
    valid = getattr(sm, 'valid', None)
    return bool(valid.get(name, False)) if valid is not None else (name in sm)

  @staticmethod
  def _updated(sm, name: str) -> bool:
    updated = getattr(sm, 'updated', None)
    return bool(updated.get(name, False)) if updated is not None else (name in sm)

  @staticmethod
  def _alive(sm, name: str) -> bool:
    alive = getattr(sm, 'alive', None)
    return bool(alive.get(name, False)) if alive is not None else True

  def update(self, sm, v_ego: float, v_cruise: float, vtsc_target: float | None, should_stop: bool, dt: float) -> MapSpeedResult:
    now = self._clock()
    self._refresh_config(now)
    if not any(self.flags.values()) or not math.isfinite(v_ego):
      return NO_CAP
    sources: dict[str, float] = {}
    cs = sm['carState']
    gas = bool(cs.gasPressed)

    # --- map (offline mapd): MSLC on mapdOut, MTSC on mapdExtendedOut's path; results held between messages
    if self.flags['mslc'] and self._updated(sm, 'mapdOut') and self._fresh(sm, 'mapdOut'):
      mo = sm['mapdOut']
      self._mslc, _ = self.mslc.update(mo.speedLimit, mo.nextSpeedLimit, mo.nextSpeedLimitDistance, v_ego, gas, now)
      self._mslc_t = now
    if self.flags['mtsc'] and self._updated(sm, 'mapdExtendedOut') and self._fresh(sm, 'mapdExtendedOut'):
      ext = sm['mapdExtendedOut']
      out = self.mtsc.update(curves_from_path(ext.position, ext.path), v_ego, ext.position.latitude, ext.position.longitude)
      self._mtsc = (out['v_target'], out['distance'])
      self._mtsc_learned = bool(out.get('using_learned', False))
      self._mtsc_t = now
    if now - self._mtsc_t > MAP_HOLD_S:
      self._mtsc = (None, math.inf)
      self._mtsc_learned = False
    if now - self._mslc_t > MAP_HOLD_S:
      self._mslc = None
    curve, self._blend_state = blend_mtsc_vtsc(self._mtsc[0] if self.flags['mtsc'] else None, vtsc_target, self._mtsc[1], v_cruise, self._blend_state, dt)
    if self.flags['mtsc'] and curve is not None:
      sources['mtsc'] = curve
    # --- posted limit: map (MSLC) and navigation (NSLC) through the one policy
    nslc = None
    if self.flags['nslc'] and self._fresh(sm, 'navInstruction'):
      nslc = self.nslc.update(float(sm['navInstruction'].speedLimit), gas, now)
    mslc = self._mslc if self.flags['mslc'] else None
    observations = []
    if mslc is not None:
      observations.append(SpeedLimitObservation(SpeedLimitSource.MAP, mslc, getattr(self.mslc, 'distance_to_limit', None)))
    if nslc is not None:
      observations.append(SpeedLimitObservation(SpeedLimitSource.NAVIGATION, nslc))
    limit = self.policy.resolve(observations)
    if limit is not None:
      sources['speed_limit'] = limit.limit_mps

    # --- traffic lights on our path, held briefly between stereoObjects
    lead = sm['radarState'].leadOne if self._fresh(sm, 'radarState') else None
    lead_present = bool(lead is not None and lead.status)
    if gas or lead_present:
      self._light = None
      self._light_dist = None
    elif self.flags['tlsc'] and self._fresh(sm, 'stereoObjects') and self._alive(sm, 'stereoObjects'):
      path = cutin_path(sm)
      lights = []
      for o in sm['stereoObjects'].objects:
        if str(o.obstacleType) != 'trafficLight':
          continue
        d = float(o.dRel)
        if path is not None and abs(float(o.yRel) - path.y_at(d)) > LIGHT_LATERAL_M:
          continue
        lights.append((str(o.trafficLightState), float(o.trafficLightConfidence), d))
      target = self.tlsc.update_from(False, v_ego, lights)
      if target is not None:
        self._light, self._light_dist, self._light_t = target, self.tlsc.best_dist, now
      elif any(s == 'green' and c >= TLSC_CONFIDENCE for s, c, _ in lights):
        # Confirmed green light on our path: release stop hold immediately instead of dragging for LIGHT_HOLD_S.
        self._light = None
        self._light_dist = None
    if now - self._light_t > LIGHT_HOLD_S:
      self._light = None
      self._light_dist = None
    if self.flags['tlsc'] and self._light is not None:
      sources['tlsc'] = self._light

    # --- distraction cap from the driver-activity monitor
    if self.flags['ddsc'] and self._fresh(sm, 'driverMonitoringState'):
      awareness = float(sm['driverMonitoringState'].awarenessStatus)
      self._critical_s = self._critical_s + dt if awareness <= 0.0 else 0.0
      lead = sm['radarState'].leadOne if self._fresh(sm, 'radarState') else None
      lead_speed = float(lead.vLead) if lead is not None and lead.status else None
      cap = self.ddsc.update(distraction_status(awareness, self._critical_s),
                           NS(gasPressed=gas, vEgo=v_ego, standstill=bool(cs.standstill)), lead_speed, should_stop)
      if cap is not None:
        sources['ddsc'] = cap

    # --- road condition (wet / ice / debris) from surfaced or the card, where those exist
    if self.flags['rcsc']:
      st = self.rcsc.update(sm)
      if st.is_active and st.speed_limit_ms > 0:
        sources['rcsc'] = st.speed_limit_ms

    cap = min(sources.values()) if sources else None
    if cap is not None:
      cap = max(cap, 0.0)
    self._last = cap
    return MapSpeedResult(cap, sources, mslc, nslc, limit, self._mtsc[0] if self.flags['mtsc'] else None, self._mtsc[1],
                          self._mtsc_learned, self._light_dist if (self.flags['tlsc'] and self._light is not None) else None)
