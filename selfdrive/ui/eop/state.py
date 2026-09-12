"""UI state: one SubMaster, one timer, Qt signals out.

This is the *only* module in the UI that touches messaging. Widgets take plain
values and emit plain signals, which is what keeps them testable without capnp
and the integration surface small enough to reason about (plan section 5.2).

One SubMaster, shared -- not one per bridge. That bug has already been paid
for once in this codebase, in ncp_session.py, where multiple PubMaster
instances for the same service crashed msgq on boot
the subscribe side has
the same shape, and openpilot's own selfdrive/ui/ui_state.py has always used a
single shared SubMaster.

`cereal` is imported lazily inside _default_submaster() rather than at module
scope, so importing this module does not require a built capnp. Tests inject
their own source.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from openpilot.selfdrive.ui.eop.components.blind_spot import (
  BlindSpotSeverity,
  fuse_blind_spot,
)
from openpilot.selfdrive.ui.eop.qt import QObject, QTimer, Signal

UI_FREQ_HZ = 20

MS_TO_KPH = 3.6
MS_TO_MPH = 2.236936
KPH_TO_MPH = 0.621371

# carState.vCruiseCluster uses 255 to mean "no set speed", matching the CAN
# convention most platforms use.
SET_SPEED_NA = 255

# Re-read Params every Nth tick rather than every tick. See update().
PARAM_POLL_DIVISOR = 5

# Everything the UI reads. Deliberately explicit: a SubMaster subscribes to
# what it is told to, and an over-broad list costs wakeups at 20 Hz for the
# whole onroad session.
SERVICES = [
  "carState",
  "controlsState",
  "selfdriveState",
  "deviceState",
  "modelV2",
  "radarState",
  "longitudinalPlan",
  "liveCalibration",
  "managerState",
  "onroadEvents",
  # Read by the classic 01M HUD and sidebar. Harmless on 02M, whose views
  # simply don't read the fields -- keeping one service list keeps this file
  # byte-identical on both branches so component fixes cherry-pick cleanly.
  "carParams",
  "pandaStates",
  "driverPoseState",
  "driverStatus",
  "navInstruction",
  "mapData",
  "alccState",
]


class UIStatus(Enum):
  DISENGAGED = "disengaged"
  ENGAGED = "engaged"
  OVERRIDE = "override"
  ALCC = "alcc"      # adaptive lane centring active while otherwise disengaged


@dataclass(frozen=True)
class DriverMonitor:
  """driverPoseState (steering-based) merged with driverStatus (camera-based).

  Two daemons, two messages, one thing the driver sees -- so they are merged
  here rather than leaving each view to remember that face geometry lives in
  one and attention probability in the other.
  """
  valid: bool = False
  attention_prob: float = 0.0
  face_detected: bool = False
  face_forward: bool = False
  face_x: float = 0.0
  face_y: float = 0.0
  face_yaw: float = 0.0
  face_pitch: float = 0.0


@dataclass(frozen=True)
class NavManeuver:
  """The next turn only. The route and map live in the NavPilot phone app --
  the device draws an arrow and a distance, nothing more."""
  valid: bool = False
  maneuver_type: str = ""
  modifier: str = ""
  primary_text: str = ""
  distance_m: float = 0.0


@dataclass(frozen=True)
class Snapshot:
  """What the views actually read. A frozen value object rather than live
  capnp readers, so a widget can never accidentally hold a reference into a
  buffer that has since been recycled."""
  started: bool = False
  status: UIStatus = UIStatus.DISENGAGED
  v_ego: float = 0.0
  left_blinker: bool = False
  right_blinker: bool = False
  in_reverse: bool = False
  blind_spot: BlindSpotSeverity = BlindSpotSeverity()
  steering_angle: float = 0.0
  cruise_kph: float = 0.0
  gear: str = "unknown"
  lead_valid: bool = False
  lead_d: float = 0.0
  bearing: float = 0.0
  cpu_temp: float = 0.0
  mem_pct: float = 0.0
  free_gb: float = 0.0
  alert_text1: str = ""
  alert_text2: str = ""
  alert_severity: str = "none"
  warnings: tuple[str, ...] = ()      # Warning.key values, see components/warnings.py

  # ---- read by the classic 01M chrome -----------------------------------
  is_metric: bool = True
  set_speed: float = 0.0              # display units, 0 when not set
  cruise_available: bool = False      # car has cruise at all
  cruise_set: bool = False            # a set point is currently active
  speed_limit_ms: float = 0.0         # 0 = unknown
  driver: DriverMonitor = DriverMonitor()
  nav: NavManeuver = NavManeuver()
  panda_connected: bool = False
  network_type: str = "none"
  network_strength: int = 0
  thermal_status: str = "green"
  ignition: bool = False

  @property
  def speed_display(self) -> float:
    """v_ego in the unit the driver has chosen."""
    return self.v_ego * (MS_TO_KPH if self.is_metric else MS_TO_MPH)

  @property
  def speed_unit(self) -> str:
    return "km/h" if self.is_metric else "mph"

  @property
  def hazards(self) -> bool:
    """Both blinkers. Not an intent to move sideways, so the camera overlays
    deliberately do nothing here -- see plan section 5.7."""
    return self.left_blinker and self.right_blinker


def _default_submaster():
  from cereal import messaging
  return messaging.SubMaster(SERVICES)


def _default_params():
  try:
    from openpilot.common.params import Params
  except ImportError:
    return None
  return Params()


class UIState(QObject):
  """Polls the SubMaster on a timer and publishes an immutable Snapshot."""

  updated = Signal(object)            # Snapshot
  offroad_transition = Signal(bool)   # True when going offroad

  def __init__(self, sm=None, params=None, parent=None):
    super().__init__(parent)
    self._sm = sm if sm is not None else _default_submaster()
    # Params is a compiled extension, so it is resolved the same way the
    # SubMaster is: injected for tests, imported lazily otherwise, and left
    # as None when it cannot be had so the UI still runs on a dev PC.
    self._params = params if params is not None else _default_params()
    self._snapshot = Snapshot()
    self._tick = 0
    self._is_metric = True
    self._ignition = False
    self._timer = QTimer(self)
    self._timer.setInterval(int(1000 / UI_FREQ_HZ))
    self._timer.timeout.connect(self.update)
    self._refresh_params()

  # ---- lifecycle --------------------------------------------------------

  def start(self) -> None:
    if not self._timer.isActive():
      self._timer.start()

  def stop(self) -> None:
    self._timer.stop()

  @property
  def snapshot(self) -> Snapshot:
    return self._snapshot

  # ---- polling ----------------------------------------------------------

  def update(self) -> None:
    self._sm.update(0)
    self._tick += 1
    # Params live on disk and every get() is a read. sidebar.cc already
    # learned this the hard way and cached behind a ParamWatcher; here the
    # two params the snapshot needs are simply re-read at 4 Hz instead of 20.
    # Ignition-off reaching the UI 250 ms late is not something a driver can
    # perceive; 20 disk reads a second for the whole drive is measurable.
    if self._tick % PARAM_POLL_DIVISOR == 0:
      self._refresh_params()

    new = self._read()
    was_started = self._snapshot.started
    self._snapshot = new
    if new.started != was_started:
      self.offroad_transition.emit(not new.started)
    self.updated.emit(new)

  def _refresh_params(self) -> None:
    if self._params is None:
      return
    self._is_metric = bool(self._params.get_bool("IsMetric"))
    # EOP has no Panda, so ignition comes from socketd via this param rather
    # than from pandaState (ui.cc does the same).
    self._ignition = bool(self._params.get_bool("EOPIgnitionOn"))

  # ---- reading ----------------------------------------------------------

  def _read(self) -> Snapshot:
    sm = self._sm

    # started is deviceState.started AND ignition -- NOT "openpilot is
    # engaged". Deriving it from selfdriveState.enabled, as this module used
    # to, left the offroad home screen up for the whole of any drive where
    # the driver never engaged.
    started = False
    if sm.valid("deviceState"):
      started = bool(getattr(sm["deviceState"], "started", False)) and self._ignition

    status = UIStatus.DISENGAGED
    if sm.valid("selfdriveState"):
      ss = sm["selfdriveState"]
      # PRE_ENABLED and OVERRIDING are both "openpilot is up but the driver
      # is in charge", which is what the amber border means. overrideLateral,
      # which this used to read, is a different thing entirely -- it is set
      # during normal engaged driving whenever the driver holds the wheel.
      op_state = str(getattr(ss, "state", "") or "")
      if op_state in ("preEnabled", "overriding"):
        status = UIStatus.OVERRIDE
      elif bool(ss.enabled):
        status = UIStatus.ENGAGED

    if status is UIStatus.DISENGAGED and sm.valid("alccState"):
      if bool(getattr(sm["alccState"], "active", False)):
        status = UIStatus.ALCC

    v_ego = steering_angle = cruise_kph = 0.0
    set_speed = 0.0
    cruise_available = cruise_set = False
    gear = "unknown"
    left_blinker = right_blinker = in_reverse = False
    car_left = car_right = False
    if sm.valid("carState"):
      cs = sm["carState"]
      # vEgoCluster is what the car's own cluster shows; it is 0 on platforms
      # that do not report it, in which case vEgo is the only thing there is.
      v_ego_cluster = float(getattr(cs, "vEgoCluster", 0.0))
      v_ego = v_ego_cluster if v_ego_cluster != 0.0 else float(cs.vEgo)
      steering_angle = float(getattr(cs, "steeringAngleDeg", 0.0))
      cruise_kph = float(getattr(getattr(cs, "cruiseState", None), "speed", 0.0)) * MS_TO_KPH
      gear = str(cs.gearShifter)
      left_blinker = bool(cs.leftBlinker)
      right_blinker = bool(cs.rightBlinker)
      in_reverse = gear == "reverse"
      car_left = bool(cs.leftBlindspot)
      car_right = bool(cs.rightBlindspot)

      raw_set = float(getattr(cs, "vCruiseCluster", 0.0))
      if raw_set == 0.0 and sm.valid("controlsState"):
        raw_set = float(getattr(sm["controlsState"], "vCruiseDEPRECATED", 0.0))
      cruise_available = raw_set != -1
      cruise_set = 0 < raw_set != SET_SPEED_NA
      # vCruiseCluster is km/h on every platform; convert only for display.
      set_speed = raw_set * (1.0 if self._is_metric else KPH_TO_MPH) if cruise_set else 0.0

    lead_valid, lead_d = False, 0.0
    if sm.valid("radarState"):
      lead = getattr(sm["radarState"], "leadOne", None)
      if lead is not None:
        lead_valid = bool(getattr(lead, "status", False))
        lead_d = float(getattr(lead, "dRel", 0.0))

    cpu_temp = mem_pct = free_gb = 0.0
    network_type = "none"
    network_strength = 0
    thermal_status = "green"
    if sm.valid("deviceState"):
      ds = sm["deviceState"]
      temps = list(getattr(ds, "cpuTempC", []) or [])
      cpu_temp = max(temps) if temps else 0.0
      mem_pct = float(getattr(ds, "memoryUsagePercent", 0.0))
      free_gb = float(getattr(ds, "freeSpacePercent", 0.0))
      network_type = str(getattr(ds, "networkType", "none") or "none")
      network_strength = int(getattr(ds, "networkStrength", 0) or 0)
      thermal_status = str(getattr(ds, "thermalStatus", "green") or "green")

    # pandaStates is a list; the UI only cares whether any panda is talking.
    panda_connected = False
    if sm.valid("pandaStates"):
      for ps in sm["pandaStates"]:
        if str(getattr(ps, "pandaType", "unknown")) != "unknown":
          panda_connected = True
          break

    driver = self._read_driver(sm)
    nav = self._read_nav(sm)

    # OSM map data outranks navInstruction: it is the posted limit for the
    # road the car is on, where navInstruction's is the limit for the route
    # step, which lags at the moment the limit actually changes.
    speed_limit_ms = 0.0
    if sm.valid("mapData"):
      osm_kph = float(getattr(sm["mapData"], "speedLimit", 0.0))
      if osm_kph > 0:
        speed_limit_ms = osm_kph / MS_TO_KPH
    if speed_limit_ms == 0.0 and sm.valid("navInstruction"):
      nav_ms = float(getattr(sm["navInstruction"], "speedLimit", 0.0))
      if nav_ms > 0:
        speed_limit_ms = nav_ms

    warnings: list[str] = []
    if sm.valid("carState"):
      cs = sm["carState"]
      if getattr(cs, "doorOpen", False):
        warnings.append("door_open")
      if getattr(cs, "seatbeltUnlatched", False) is not False:
        warnings.append("seatbelt")
      if getattr(cs, "parkingBrake", False):
        warnings.append("parking_brake")
      if getattr(cs, "steerFaultPermanent", False):
        warnings.append("steering_fault")
      if getattr(cs, "canError", False):
        warnings.append("can_fault")
    if sm.valid("liveCalibration"):
      cal = str(getattr(sm["liveCalibration"], "calStatus", "") or "")
      if cal and cal != "calibrated":
        warnings.append("calibration_required")

    alert1 = alert2 = ""
    severity = "none"
    if sm.valid("selfdriveState"):
      ss = sm["selfdriveState"]
      alert1 = str(getattr(ss, "alertText1", "") or "")
      alert2 = str(getattr(ss, "alertText2", "") or "")
      raw = str(getattr(ss, "alertStatus", "") or "")
      if alert1:
        severity = {"critical": "critical", "userPrompt": "warning"}.get(raw, "normal")

    ctrl_left = ctrl_right = 0
    if sm.valid("controlsState"):
      ctrl = sm["controlsState"]
      ctrl_left = int(getattr(ctrl, "leftBlindSpot", 0))
      ctrl_right = int(getattr(ctrl, "rightBlindSpot", 0))

    # Note the 0/False defaults above: a source that is not currently valid
    # contributes nothing rather than being skipped, so a stale WARNING cannot
    # outlive the message that raised it. fuse_blind_spot()'s max() only ever
    # raises, which is exactly why that matters.
    return Snapshot(
      started=started,
      status=status,
      v_ego=v_ego,
      left_blinker=left_blinker,
      right_blinker=right_blinker,
      in_reverse=in_reverse,
      blind_spot=fuse_blind_spot(ctrl_left, ctrl_right, car_left, car_right),
      steering_angle=steering_angle,
      cruise_kph=cruise_kph,
      gear=gear,
      lead_valid=lead_valid,
      lead_d=lead_d,
      cpu_temp=cpu_temp,
      mem_pct=mem_pct,
      free_gb=free_gb,
      alert_text1=alert1,
      alert_text2=alert2,
      alert_severity=severity,
      warnings=tuple(warnings),
      is_metric=self._is_metric,
      set_speed=set_speed,
      cruise_available=cruise_available,
      cruise_set=cruise_set,
      speed_limit_ms=speed_limit_ms,
      driver=driver,
      nav=nav,
      panda_connected=panda_connected,
      network_type=network_type,
      network_strength=network_strength,
      thermal_status=thermal_status,
      ignition=self._ignition,
    )

  @staticmethod
  def _read_driver(sm) -> DriverMonitor:
    """driverPoseState carries attention; driverStatus carries the face box.
    Either may be absent -- the steering-based monitor runs without a camera."""
    valid = False
    attention = 0.0
    if sm.valid("driverPoseState"):
      valid = True
      attention = float(getattr(sm["driverPoseState"], "attentionProb", 0.0))

    detected = forward = False
    fx = fy = yaw = pitch = 0.0
    if sm.valid("driverStatus"):
      valid = True
      fs = sm["driverStatus"]
      detected = bool(getattr(fs, "faceDetected", False))
      forward = bool(getattr(fs, "faceForward", False))
      fx = float(getattr(fs, "faceX", 0.0))
      fy = float(getattr(fs, "faceY", 0.0))
      yaw = float(getattr(fs, "faceYaw", 0.0))
      pitch = float(getattr(fs, "facePitch", 0.0))

    return DriverMonitor(valid=valid, attention_prob=attention,
                         face_detected=detected, face_forward=forward,
                         face_x=fx, face_y=fy, face_yaw=yaw, face_pitch=pitch)

  @staticmethod
  def _read_nav(sm) -> NavManeuver:
    if not sm.valid("navInstruction"):
      return NavManeuver()
    n = sm["navInstruction"]
    kind = str(getattr(n, "maneuverType", "") or "")
    if not kind or kind == "none":
      return NavManeuver()
    return NavManeuver(
      valid=True,
      maneuver_type=kind,
      modifier=str(getattr(n, "maneuverModifier", "") or ""),
      primary_text=str(getattr(n, "maneuverPrimaryText", "") or ""),
      distance_m=float(getattr(n, "maneuverDistance", 0.0)),
    )
