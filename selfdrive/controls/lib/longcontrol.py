import time
import numpy as np
from cereal import car
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib.drive_helpers import CONTROL_N
from openpilot.common.pid import PIDController
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.common.params import Params

CONTROL_N_T_IDX = ModelConstants.T_IDXS[:CONTROL_N]

LongCtrlState = car.CarControl.Actuators.LongControlState

# TJA Constants
TJA_VELOCITY_THRESHOLD = 5.0  # m/s (~18 km/h)
TJA_RAMP_DURATION = 2.0  # seconds
TJA_MIN_ACCEL = 0.25  # m/s²
TJA_MAX_ACCEL = 1.2  # m/s²


def long_control_state_trans(CP, active, long_control_state, v_ego,
                             should_stop, brake_pressed, cruise_standstill):
  stopping_condition = should_stop
  starting_condition = (not should_stop and
                        not cruise_standstill and
                        not brake_pressed)
  started_condition = v_ego > CP.vEgoStarting

  if not active:
    long_control_state = LongCtrlState.off

  else:
    if long_control_state == LongCtrlState.off:
      if not starting_condition:
        long_control_state = LongCtrlState.stopping
      else:
        if starting_condition and CP.startingState:
          long_control_state = LongCtrlState.starting
        else:
          long_control_state = LongCtrlState.pid

    elif long_control_state == LongCtrlState.stopping:
      if starting_condition and CP.startingState:
        long_control_state = LongCtrlState.starting
      elif starting_condition:
        long_control_state = LongCtrlState.pid

    elif long_control_state in [LongCtrlState.starting, LongCtrlState.pid]:
      if stopping_condition:
        long_control_state = LongCtrlState.stopping
      elif started_condition:
        long_control_state = LongCtrlState.pid
  return long_control_state


class LongControl:
  def __init__(self, CP):
    self.CP = CP
    self.long_control_state = LongCtrlState.off
    self.pid = PIDController((CP.longitudinalTuning.kpBP, CP.longitudinalTuning.kpV),
                             (CP.longitudinalTuning.kiBP, CP.longitudinalTuning.kiV),
                             k_f=CP.longitudinalTuning.kf, rate=1 / DT_CTRL)
    self.last_output_accel = 0.0

    # EOP: TJA (Traffic Jam Assist) state
    self.params = Params()
    self.tja_start_time = 0.0
    self.tja_standstill_start_time = 0.0
    # tja_resume_required: set True when hold timeout expires.
    # Wired to soundd via ttsRequest in controlsd.py.
    self.tja_resume_required = False
    # Cache TJA params — refresh at most once per second
    self._tja_enabled = False
    self._tja_max_hold_s = 600.0
    self._last_tja_param_update = 0.0
    self._tja_param_interval = 1.0
    self._update_tja_params()

  def _update_tja_params(self):
    """Cache TJA parameters — file I/O no more than once per second."""
    now = time.monotonic()
    if now - self._last_tja_param_update < self._tja_param_interval:
      return
    self._last_tja_param_update = now

    self._tja_enabled = self.params.get_bool("EOPTJAEnabled")
    try:
      max_hold_min = int(self.params.get("EOPTJAMaxHoldMinutes") or 10)
      max_hold_min = max(2, min(20, max_hold_min))
      self._tja_max_hold_s = max_hold_min * 60.0
    except (ValueError, TypeError):
      self._tja_max_hold_s = 600.0

  def _get_tja_max_hold_s(self):
    """Get TJA max hold time from cached parameter."""
    return self._tja_max_hold_s

  def _check_tja_hold_timeout(self, standstill):
    """
    Check if TJA standstill hold has timed out.

    Args:
      standstill: True if vehicle is at standstill

    Returns:
      True if resume is required (hold timeout exceeded)
    """
    if not self._tja_enabled:
      self.tja_resume_required = False
      self.tja_standstill_start_time = 0.0
      return False

    if not standstill:
      # Reset when moving
      self.tja_standstill_start_time = 0.0
      self.tja_resume_required = False
      return False

    # Standstill - track duration
    if self.tja_standstill_start_time == 0.0:
      self.tja_standstill_start_time = time.monotonic()
      self.tja_resume_required = False
      return False

    # Check timeout
    hold_duration = time.monotonic() - self.tja_standstill_start_time
    max_hold_s = self._get_tja_max_hold_s()

    if hold_duration > max_hold_s:
      self.tja_resume_required = True
      return True

    return False

  def reset(self):
    self.pid.reset()

  def _apply_tja_ramp(self, a_target, v_ego, long_control_state):
    """
    Apply Traffic Jam Assist smooth acceleration ramp.

    TJA provides smooth acceleration from standstill in stop-and-go traffic
    by limiting acceleration to a progressive ramp (0.25→1.2 m/s² over 2s).

    Args:
      a_target: Target acceleration from planner
      v_ego: Current ego velocity (m/s)
      long_control_state: Current longitudinal control state

    Returns:
      Acceleration with TJA ramp applied (if enabled and conditions met)
    """
    # When TJA is disabled, fall back to the upstream fixed startAccel from CarParams.
    if not self._tja_enabled:
      return self.CP.startAccel

    # Only apply in starting state and below velocity threshold
    if long_control_state != LongCtrlState.starting or v_ego >= TJA_VELOCITY_THRESHOLD:
      self.tja_start_time = 0.0
      return a_target

    # Initialize start time when entering starting state
    if self.tja_start_time == 0.0:
      self.tja_start_time = time.monotonic()

    # Bypass TJA for braking (safety)
    if a_target < 0:
      return a_target

    # Calculate ramp progress
    t_since_start = time.monotonic() - self.tja_start_time
    ramp_progress = min(1.0, t_since_start / TJA_RAMP_DURATION)

    # Apply linear ramp: 0.25 → 1.2 m/s² over 2 seconds
    a_cap = TJA_MIN_ACCEL + (TJA_MAX_ACCEL - TJA_MIN_ACCEL) * ramp_progress

    return min(a_target, a_cap)

  def update(self, active, CS, a_target, should_stop, accel_limits):
    """Update longitudinal control. This updates the state machine and runs a PID loop"""
    self._update_tja_params()  # rate-limited to 1 Hz inside
    self.pid.neg_limit = accel_limits[0]
    self.pid.pos_limit = accel_limits[1]

    # EOP: TJA hold timeout check
    tja_hold_timeout = self._check_tja_hold_timeout(CS.cruiseState.standstill)

    # If TJA hold timed out, prevent auto-starting (require driver resume)
    if tja_hold_timeout and self.long_control_state == LongCtrlState.stopping:
      # Force staying in stopping state - require manual resume
      override_should_stop = True
    else:
      override_should_stop = should_stop

    self.long_control_state = long_control_state_trans(self.CP, active, self.long_control_state, CS.vEgo,
                                                       override_should_stop, CS.brakePressed,
                                                       CS.cruiseState.standstill)
    if self.long_control_state == LongCtrlState.off:
      self.reset()
      output_accel = 0.
      self.tja_start_time = 0.0
      self.tja_standstill_start_time = 0.0
      self.tja_resume_required = False

    elif self.long_control_state == LongCtrlState.stopping:
      output_accel = self.last_output_accel
      if output_accel > self.CP.stopAccel:
        output_accel = min(output_accel, 0.0)
        output_accel -= self.CP.stoppingDecelRate * DT_CTRL
      self.reset()
      self.tja_start_time = 0.0

    elif self.long_control_state == LongCtrlState.starting:
      # EOP: Apply TJA smooth acceleration ramp
      output_accel = self._apply_tja_ramp(a_target, CS.vEgo, self.long_control_state)
      self.reset()
      # Reset standstill timer when starting to move
      self.tja_standstill_start_time = 0.0
      self.tja_resume_required = False

    else:  # LongCtrlState.pid
      error = a_target - CS.aEgo
      output_accel = self.pid.update(error, speed=CS.vEgo,
                                     feedforward=a_target)
      self.tja_start_time = 0.0
      self.tja_standstill_start_time = 0.0
      self.tja_resume_required = False

    self.last_output_accel = np.clip(output_accel, accel_limits[0], accel_limits[1])
    return self.last_output_accel
