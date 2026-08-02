"""
ddsc.py - Driver Distraction Speed Controller

Reads DMS status (driverStatus), vehicle state (carState), and lead car
speed (radarState), then returns a speed CAP for the longitudinal planner.

Boundary: DDSC consumes DMS outputs; it does NOT do driver monitoring or
attention tracking. That is driverd.py's responsibility.

Rules:
- DMS provides a base safe-speed signal (safeSpeedLimitMps > 0 when
too_distracted). DDSC turns that into a traffic-aware CAP.
- Following a lead car: cap = lead_speed * 0.9 (follow with margin)
- Highway with no lead: cap = 80 km/h (don't be a highway hazard)
- Urban with no lead: cap = 60 km/h (standard DMS limit)
- Driver gas-pedal override cancels the cap immediately
- Steering input does NOT cancel the cap
- Returns None when inactive (no limit)
- Gentle deceleration when the cap drops

UNCONSCIOUS MODE (medical emergency - heart attack, seizure, etc.):
- Triggered by DMS unconsciousActive flag (no driver detected + no steering for 20s)
- Immediate speed reduction, hazard lights auto-activate
- Follow lead car if present; decelerate to standstill if traffic light/police
- Once at standstill: latch stop, require gas press to resume
- Gas override works for first responders
"""

from typing import Optional
from nagaspilot.speed_zones import HIGHWAY_SPEED_MPS

# Base DMS cap when too_distracted (m/s)
DMS_BASE_LIMIT_MPS = 16.67  # 60 km/h

# Highway cap — empty highway, no lead car
HIGHWAY_CAP_MPS = 22.2  # 80 km/h
HIGHWAY_VEGO_THRESHOLD_MPS = HIGHWAY_SPEED_MPS
HIGHWAY_EXIT_THRESHOLD_MPS = 20.0  # < 72 km/h: highway exit (hysteresis band 20–24)

# Lead-car margin — when following, stay within this ratio of lead speed
LEAD_SPEED_RATIO = 0.90

# Absolute minimum — car must keep moving to avoid rear-end collisions
ABSOLUTE_MIN_MPS = 1.0  # ~3.6 km/h — crawling, never stop

# Gentle deceleration when the cap drops — avoids startling driver
DDSC_MAX_DECEL_MPS2 = -1.5  # m/s² (vs normal -3.0 m/s²)

# Unconscious mode: more aggressive deceleration for medical emergencies
UNCONSCIOUS_DECEL_MPS2 = -2.0  # m/s² — still gentle but firm

# Standstill threshold — below this we consider the car stopped
STANDSTILL_THRESHOLD_MPS = 0.3  # ~1 km/h

# Crawl speed during unconscious episode when no lead and no stop signal
UNCONSCIOUS_CRAWL_MPS = 4.17  # 15 km/h — enough to not be rear-ended


class DDSC:
  """Driver Distraction Speed Controller."""

  def __init__(self):
    self._active = False
    self._unconscious = False
    self._v_target: Optional[float] = None
    self._a_limit: Optional[float] = None
    self._standstill_latched = False  # Once stopped in unconscious mode, stay stopped
    self._highway_latched = False     # Hysteresis: stay highway until speed < exit threshold

  def update(self, driver_status, car_state, lead_speed: Optional[float] = None,
             should_stop: bool = False) -> Optional[float]:
    """Compute distraction speed CAP.

    This is a CAP on maximum speed, not a target. The longitudinal planner's
    ACC already handles lead-car following and stopping; DDSC just clamps
    v_cruise so the distracted driver never goes too fast.

    Args:
      driver_status: driverStatus message (or None)
      car_state: carState message (or None)
      lead_speed: Lead car speed in m/s from radar/vision (or None)
      should_stop: True if longitudinal planner wants to stop (traffic light, etc.)

    Returns:
      Speed CAP in m/s, or None if no limit should be applied.
    """
    dms_limit = 0.0
    unconscious_active = False
    if driver_status is not None:
      dms_limit = driver_status.safeSpeedLimitMps
      unconscious_active = getattr(driver_status, 'unconsciousActive', False)

    gas_pressed = False
    v_ego = 0.0
    standstill = False
    if car_state is not None:
      gas_pressed = car_state.gasPressed
      v_ego = max(0.0, car_state.vEgo)
      standstill = getattr(car_state, 'standstill', False)

    # Highway hysteresis: latch True at >= 24 m/s, clear below 20 m/s
    if v_ego >= HIGHWAY_VEGO_THRESHOLD_MPS:
      self._highway_latched = True
    elif v_ego < HIGHWAY_EXIT_THRESHOLD_MPS:
      self._highway_latched = False

    # Gas press = conscious intent (first responder or driver recovery)
    # In unconscious mode: gas cancels standstill latch and resumes
    if gas_pressed:
      self._standstill_latched = False

    # No DMS limit or gas override = inactive
    if dms_limit <= 0.0 or gas_pressed:
      self._active = False
      self._unconscious = False
      self._v_target = None
      self._a_limit = None
      return None

    self._unconscious = unconscious_active

    # Unconscious standstill latch: once stopped, stay stopped until gas
    if unconscious_active and (standstill or v_ego < STANDSTILL_THRESHOLD_MPS):
      self._standstill_latched = True

    if self._standstill_latched:
      self._active = True
      self._v_target = 0.0
      self._a_limit = UNCONSCIOUS_DECEL_MPS2
      return 0.0

    # Traffic-aware CAP
    if lead_speed is not None and lead_speed > 0.0:
      # Following a lead car: cap at lead speed with margin.
      # ACC handles the actual following; DDSC just prevents passing.
      cap = max(lead_speed * LEAD_SPEED_RATIO, ABSOLUTE_MIN_MPS)
    elif should_stop:
      # Traffic light, stop sign, or police vehicle ahead — stop
      cap = 0.0
    elif unconscious_active:
      # Unconscious mode: no lead, no stop signal — crawl to avoid being rear-ended
      # But if we're already going slower than crawl, just maintain
      cap = min(v_ego, UNCONSCIOUS_CRAWL_MPS) if v_ego > 0.0 else UNCONSCIOUS_CRAWL_MPS
    elif self._highway_latched:
      # Empty highway: higher cap so we don't become a stationary hazard
      cap = HIGHWAY_CAP_MPS
    else:
      # Urban with no lead: standard DMS cap
      cap = DMS_BASE_LIMIT_MPS

    self._v_target = cap
    self._a_limit = UNCONSCIOUS_DECEL_MPS2 if unconscious_active else DDSC_MAX_DECEL_MPS2
    self._active = True
    return self._v_target

  @property
  def active(self) -> bool:
    return self._active

  @property
  def unconscious(self) -> bool:
    return self._unconscious

  @property
  def standstill_latched(self) -> bool:
    return self._standstill_latched

  @property
  def v_target(self) -> Optional[float]:
    return self._v_target

  @property
  def a_limit(self) -> Optional[float]:
    """Gentle deceleration limit when the cap drops."""
    return self._a_limit
