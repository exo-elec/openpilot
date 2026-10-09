"""One speed-cap adapter for the map-, light- and distraction-based controllers (MTSC, MSLC, TLSC, DDSC, RCD).

The planner calls `update` once per cycle and takes `min(v_cruise, cap)`: these controllers only ever LOWER the cruise speed, so
the speed the driver set is never exceeded. Per-controller switches (default off): `EOPMTSCEnabled`, `EOPMSLCEnabled`,
`EOPTLSCEnabled`, `EOPDDSCEnabled`, `EOPRCDEnabled`; MSLC per-range offsets `EOPSharedSLCOffsets` ("5,5,5,5,5,5,5" km/h per speed bucket).

Two fixes over EOP10's inline planner code:
  - EOP10 reset the MTSC/MSLC target every cycle and set it only on the cycle a `mapData` message arrived (1 Hz): the cap existed for
    one planner cycle in twenty. Here the last result is HELD for MAP_HOLD_S after the last map message.
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

from nagaspilot.controls.eop_curve_speed import blend_mtsc_vtsc
from nagaspilot.runtime.feature_keys import EOP_MAP_KEYS
from nagaspilot.controls.eop_ddsc import DMS_BASE_LIMIT_MPS, DDSC
from nagaspilot.controls.eop_mslc import MSLC
from nagaspilot.controls.eop_mtsc import MTSC
from nagaspilot.controls.eop_tlsc import TLSC
from nagaspilot.runtime.cutin_adapter import cutin_path
from nagaspilot.runtime.rcd import RCDRuntime

MAP_HOLD_S = 3.0              # mapd publishes at 1 Hz: hold a result this long
LIGHT_HOLD_S = 0.5            # same as EOP10's TLSC stale limit
LIGHT_LATERAL_M = 3.5         # a light for our lane is within this of the planned path
PROMPT_AWARENESS = 0.25
UNCONSCIOUS_S = 20.0
CONFIG_REFRESH_S = 2.0


@dataclass(frozen=True)
class MapSpeedResult:
  cap: float | None
  sources: dict


def distraction_status(awareness: float, critical_s: float) -> NS:
  """driverStatus-like input for DDSC from the driver-activity monitor's awareness (1 = fully aware, <= 0 = nothing for too long)."""
  distracted = math.isfinite(awareness) and awareness <= PROMPT_AWARENESS
  return NS(safeSpeedLimitMps=DMS_BASE_LIMIT_MPS if distracted else 0.0, unconsciousActive=bool(distracted and critical_s >= UNCONSCIOUS_S))


class MapSpeed:
  def __init__(self, get_bool=None, get_str=None, clock=time.monotonic):
    self._get_bool = get_bool or (lambda k: False)
    self._get_str = get_str or (lambda k: "")
    self._clock = clock
    self.flags = dict.fromkeys(('mtsc', 'mslc', 'tlsc', 'ddsc', 'rcd'), False)
    self._cfg_t = -1e9
    self.mtsc, self.mslc, self.tlsc, self.ddsc = MTSC(enabled=True), MSLC(enabled=True), TLSC(enabled=True), DDSC()
    self.rcd = RCDRuntime(lambda k: True)      # gated by flags['rcd'] below; the runtime's own switch is always on
    self._map_t = -1e9
    self._mtsc: tuple[float | None, float] = (None, math.inf)      # (target, distance)
    self._mslc: float | None = None
    self._light_t = -1e9
    self._light: float | None = None
    self._blend_state: float | None = None
    self._critical_s = 0.0
    self._last = None

  def _refresh_config(self, now: float) -> None:
    if now - self._cfg_t < CONFIG_REFRESH_S:
      return
    self._cfg_t = now
    self.flags = {k: bool(self._get_bool(EOP_MAP_KEYS[k])) for k in self.flags}
    raw = str(self._get_str(EOP_MAP_KEYS["offsets"]) or "")
    self.mslc._offset.set_offsets_kph([float(v) for v in raw.split(',') if v.strip().lstrip('-').replace('.', '', 1).isdigit()])

  @staticmethod
  def _fresh(sm, name: str) -> bool:
    return bool(sm.valid.get(name, False))

  def update(self, sm, v_ego: float, v_cruise: float, vtsc_target: float | None, should_stop: bool, dt: float) -> MapSpeedResult:
    now = self._clock()
    self._refresh_config(now)
    sources: dict[str, float] = {}
    if not any(self.flags.values()) or not math.isfinite(v_ego):
      return MapSpeedResult(None, sources)
    cs = sm['carState']
    gas = bool(cs.gasPressed)

    # --- map: MTSC + MSLC on mapData, held between messages
    if (self.flags['mtsc'] or self.flags['mslc']) and sm.updated.get('mapData', False) and self._fresh(sm, 'mapData'):
      lat = lon = 0.0
      md = sm['mapData']
      if self.flags['mtsc']:
        out = self.mtsc.update(md, v_ego, lat, lon)
        self._mtsc = (out['v_target'], out['distance'])
      if self.flags['mslc']:
        self._mslc, _ = self.mslc.update(md, v_ego, gas, now)
      self._map_t = now
    if now - self._map_t > MAP_HOLD_S:
      self._mtsc, self._mslc = (None, math.inf), None
    curve, self._blend_state = blend_mtsc_vtsc(self._mtsc[0] if self.flags['mtsc'] else None, vtsc_target, self._mtsc[1], v_cruise, self._blend_state, dt)
    if self.flags['mtsc'] and curve is not None:
      sources['mtsc'] = curve
    if self.flags['mslc'] and self._mslc is not None:
      sources['mslc'] = self._mslc

    # --- traffic lights on our path, held briefly between stereoObjects
    if self.flags['tlsc'] and self._fresh(sm, 'stereoObjects'):
      path = cutin_path(sm)
      lights = []
      for o in sm['stereoObjects'].objects:
        if str(o.obstacleType) != 'trafficLight':
          continue
        d = float(o.dRel)
        if path is not None and abs(float(o.yRel) - path.y_at(d)) > LIGHT_LATERAL_M:
          continue
        lights.append((str(o.trafficLightState), float(o.trafficLightConfidence), d))
      lead = sm['radarState'].leadOne if self._fresh(sm, 'radarState') else None
      target = self.tlsc.update_from(bool(lead is not None and lead.status), v_ego, lights)
      if target is not None:
        self._light, self._light_t = target, now
    if now - self._light_t > LIGHT_HOLD_S:
      self._light = None
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
    if self.flags['rcd']:
      st = self.rcd.update(sm)
      if st.is_active and st.speed_limit_ms > 0:
        sources['rcd'] = st.speed_limit_ms

    cap = min(sources.values()) if sources else None
    if cap is not None:
      cap = max(cap, 0.0)
    self._last = cap
    return MapSpeedResult(cap, sources)
