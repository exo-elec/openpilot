"""DPP - Dynamic Path Planner: picks, per driving situation, how much authority the rule channel gets.

Same idea as DLAT/DLON: one automatic arbiter instead of a user-selected mode. Inputs summarise the
situation; the output is a `Mode` for ngp_policy_arbiter.PolicyArbiter plus the named case and the reason.

  ceiling   : the most authority the user allows (`ngp_dpp_max_mode`); DPP never goes above it
  default   : SHADOW (the rule channel runs and is measured, the policy drives)
  degraded  : perception/rule channel unhealthy, or the two channels disagree for long -> OFF
  cut_in / close lead / VRU alongside : SUPERVISE (brake-only authority), PRIMARY_LONG when the rule channel is
              allowed that high and its lead handling is what the situation needs
  alongside, good lanes, straight : PRIMARY_LAT may be used (offsets are the point) when the ceiling allows
Escalation is quick (0.3 s of the situation), de-escalation slow (2.5 s dwell), and a mode never changes
faster than the dwell time, so the car does not flip between channels. Pure: no cereal, no Params.
"""
from dataclasses import dataclass

from nagaspilot.controls.ngp_policy_arbiter import Mode

ESCALATE_S = 0.3
ESCALATE_FAST_S = 0.1        # safety cases (cut-in, close lead) escalate almost at once: two frames at 20 Hz
FAST_CASES = ('cut_in', 'close_lead')
DEESCALATE_S = 2.5
MIN_V_EGO = 8.0
LANE_CONF_MIN = 0.6
CURVE_KAPPA = 0.008          # 1/m: above this the lane geometry is the policy's job (tight curve)
CLOSE_LEAD_HEADWAY_S = 1.2
DISAGREE_STRIKE_S = 4.0


@dataclass(frozen=True)
class Situation:
  v_ego: float
  perception_ok: bool            # monod fresh, exec time in budget, calibration valid
  rule_valid: bool               # RuleCmd.valid
  lane_conf: float               # min confidence of the two inner lane lines, 0..1
  curvature_ahead: float         # 1/m, absolute
  lead_gap_m: float | None       # nearest in-lane object ahead (path-relative)
  cut_in_risk: bool              # a predicted cut-in exists (ngp_cutin_speed.evaluate)
  vru_alongside: bool            # motorcycle/bicycle/person beside us within the longitudinal window
  large_alongside: bool          # truck/bus beside us
  disagree: bool                 # the rule channel is currently LESS cautious than the policy (curvature differs or it wants to accelerate more)
  driver_override: bool = False


@dataclass(frozen=True)
class DppDecision:
  mode: Mode
  case: str
  reason: str


def classify(s: Situation, ceiling: Mode) -> tuple[Mode, str, str]:
  """Instantaneous wish, before hysteresis."""
  if ceiling <= Mode.OFF:
    return Mode.OFF, 'off', 'ceiling is off'
  if s.driver_override:
    return Mode.SHADOW, 'driver', 'driver in charge'
  if not s.perception_ok or not s.rule_valid:
    return Mode.OFF, 'degraded', 'perception or rule channel unhealthy'
  if s.disagree:
    return Mode.SHADOW, 'disagree', 'policy and rule channel disagree'
  if s.v_ego < MIN_V_EGO:
    return Mode.SHADOW, 'low_speed', 'below the minimum speed'
  headway = None if s.lead_gap_m is None else s.lead_gap_m / max(s.v_ego, 0.1)
  lanes_ok = s.lane_conf >= LANE_CONF_MIN and s.curvature_ahead <= CURVE_KAPPA

  if s.cut_in_risk:
    return min(Mode.PRIMARY_LONG, ceiling), 'cut_in', 'predicted cut-in: rule channel handles the gap'
  if headway is not None and headway < CLOSE_LEAD_HEADWAY_S:
    return min(Mode.SUPERVISE, ceiling), 'close_lead', 'lead inside the headway: brake-only authority'
  if (s.vru_alongside or s.large_alongside) and lanes_ok:
    return min(Mode.PRIMARY_LAT, ceiling), 'alongside', 'traffic beside us in a clear lane: offset from the rule channel'
  if (s.vru_alongside or s.large_alongside):
    return min(Mode.SUPERVISE, ceiling), 'alongside_lanes_weak', 'traffic beside us but weak lane lines: brake-only'
  if not lanes_ok:
    return Mode.SHADOW, 'weak_lanes_or_curve', 'weak lane lines or a tight curve: policy drives'
  return Mode.SHADOW, 'cruise', 'nothing special: policy drives, rule channel measured'


class DPP:
  def __init__(self):
    self.mode = Mode.SHADOW
    self._want = Mode.SHADOW
    self._want_t = 0.0
    self._dwell = 0.0
    self._dis_t = 0.0
    self.case = 'cruise'
    self.reason = ''

  def reset(self) -> None:
    self.__init__()

  def update(self, s: Situation, ceiling: int, dt: float) -> DppDecision:
    ceiling = Mode(min(max(int(ceiling), 0), int(Mode.PRIMARY_BOTH)))
    wish, case, reason = classify(s, ceiling)
    self._dwell += dt
    if wish == self.mode:
      self._want, self._want_t = wish, 0.0
    else:
      if wish == self._want:
        self._want_t += dt
      else:
        self._want, self._want_t = wish, dt
      need = (ESCALATE_FAST_S if case in FAST_CASES else ESCALATE_S) if wish > self.mode else DEESCALATE_S
      urgent = wish == Mode.OFF or wish < self.mode and case in ('degraded', 'driver', 'off', 'disagree')
      if urgent or (self._want_t >= need and (self._dwell >= (ESCALATE_FAST_S if case in FAST_CASES else ESCALATE_S) if wish > self.mode else self._dwell >= DEESCALATE_S)):
        self.mode, self._dwell, self._want_t = wish, 0.0, 0.0
    if self.mode > ceiling:
      self.mode = ceiling
    self.case, self.reason = case, reason
    return DppDecision(self.mode, case, reason)
