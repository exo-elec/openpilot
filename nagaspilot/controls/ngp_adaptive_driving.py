"""Adaptive driving: personality and acceleration limits from vehicle telemetry (battery SOC, range, temperatures).

Moved from EOP10's `selfdrive/adaptd/adaptd.py` (the computer), logic unchanged and golden-tested against recorded outputs of the
original. Differences: personalities are plain ints (aggressive 0, standard 1, relaxed 2, traffic 3: no dependence on the cereal enum,
NGP10's has only the first three), the clock is injectable, and the cold-battery test ignores the "unknown" sentinel unless
`legacy_cold_sentinel_bug=True` (EOP10's behaviour). Pure: no cereal, no Params.
"""
import time
from dataclasses import dataclass

AGGRESSIVE, STANDARD, RELAXED, TRAFFIC = 0, 1, 2, 3
UNKNOWN_TEMP = -270.0        # telemetry sentinel for "no reading" is -273.0


@dataclass(frozen=True)
class Thresholds:
  """Adaptive driving thresholds."""
  # SOC thresholds (%)
  soc_critical: float = 10.0   # traffic personality, max conservation
  soc_low: float = 20.0        # relaxed personality
  soc_high: float = 80.0       # normal operation

  # Battery temperature thresholds (°C)
  batt_temp_cold: float = 0.0   # limited regen
  batt_temp_hot: float = 45.0   # thermal derating
  batt_temp_critical: float = 55.0  # max derating

  # Motor/inverter temperature thresholds (°C)
  motor_temp_hot: float = 80.0
  motor_temp_critical: float = 100.0
  inverter_temp_hot: float = 70.0
  inverter_temp_critical: float = 90.0

  # Range threshold (km)
  range_low: float = 50.0
  range_critical: float = 25.0

  # Coolant temperature threshold (ICE)
  coolant_temp_high: float = 100.0

  # Default accel limits (Tesla)
  default_accel_max: float = 2.0
  default_accel_min: float = -3.48
  default_regen_strength: float = 1.0


THRESHOLDS = Thresholds()


# ---------------------------------------------------------------------------
# Adaptive driving state
# ---------------------------------------------------------------------------

@dataclass
class AdaptiveProfile:
  """Computed adaptive driving profile."""
  personality: int = STANDARD
  reason: str = ""
  reason_code: str = ""
  accel_max: float = THRESHOLDS.default_accel_max
  accel_min: float = THRESHOLDS.default_accel_min
  regen_strength: float = THRESHOLDS.default_regen_strength
  thermal_derating: bool = False
  enabled: bool = False
  timestamp: int = 0


class AdaptiveDrivingComputer:
  """Compute adaptive driving profile from vehicle data."""

  def __init__(self, thresholds: Thresholds = THRESHOLDS, clock=time.monotonic, legacy_cold_sentinel_bug: bool = False):
    """`clock`: injectable (hysteresis). `legacy_cold_sentinel_bug=True` reproduces EOP10's behaviour exactly: its cold-battery test
    `batteryTempMin < 0` also fires for the "unknown" sentinel (-273), so a car with no battery temperature reads as a cold battery."""
    self._clock = clock
    self._legacy_cold = legacy_cold_sentinel_bug
    self.thresholds = thresholds
    self._last_personality: int = STANDARD
    self._hysteresis_sec: float = 10.0  # Min time between personality changes
    self._last_change_time: float = 0.0
    self._enabled: bool = False

  def update(self, vd) -> AdaptiveProfile:
    """Compute adaptive profile from NcpVehicleData."""
    now = self._clock()
    profile = AdaptiveProfile(timestamp=int(now * 1e9))
    profile.enabled = self._enabled

    if not vd.valid:
      profile.reason = "No vehicle data"
      profile.reason_code = "no_data"
      return profile

    reasons: list[str] = []
    reason_codes: list[str] = []

    # Default to standard
    target_personality = STANDARD
    accel_max = self.thresholds.default_accel_max
    accel_min = self.thresholds.default_accel_min
    regen = self.thresholds.default_regen_strength
    thermal_derating = False

    soc = vd.batterySoc
    range_km = vd.rangeRemaining
    batt_temp_max = vd.batteryTempMax
    batt_temp_min = vd.batteryTempMin
    motor_temp = vd.motorTemp
    inverter_temp = vd.inverterTemp
    coolant_temp = vd.coolantTemp

    # --- SOC-based adaptation ---
    if soc >= 0:
      if soc <= self.thresholds.soc_critical:
        target_personality = TRAFFIC
        accel_max = min(accel_max, 1.0)
        regen = min(regen, 0.5)  # Limit regen to avoid voltage spikes
        reasons.append(f"Critical SOC {soc:.0f}%")
        reason_codes.append("critical_soc")
      elif soc <= self.thresholds.soc_low:
        target_personality = RELAXED
        accel_max = min(accel_max, 1.4)
        reasons.append(f"Low SOC {soc:.0f}%")
        reason_codes.append("low_soc")
      elif soc >= self.thresholds.soc_high:
        # High SOC = full battery, limited regen
        regen = min(regen, 0.6)
        reasons.append(f"High SOC {soc:.0f}% (limited regen)")
        reason_codes.append("high_soc")

    # --- Range-based adaptation ---
    if range_km >= 0:
      if range_km <= self.thresholds.range_critical:
        target_personality = max(target_personality, TRAFFIC)
        accel_max = min(accel_max, 0.8)
        reasons.append(f"Critical range {range_km:.0f}km")
        reason_codes.append("critical_range")
      elif range_km <= self.thresholds.range_low:
        target_personality = max(target_personality, RELAXED)
        accel_max = min(accel_max, 1.2)
        reasons.append(f"Low range {range_km:.0f}km")
        reason_codes.append("low_range")

    # --- Battery thermal adaptation ---
    if batt_temp_max > self.thresholds.batt_temp_critical:
      thermal_derating = True
      accel_max = min(accel_max, 0.8)
      regen = min(regen, 0.5)
      reasons.append(f"Critical battery temp {batt_temp_max:.1f}°C")
      reason_codes.append("critical_batt_temp")
    elif batt_temp_max > self.thresholds.batt_temp_hot:
      thermal_derating = True
      accel_max = min(accel_max, 1.2)
      reasons.append(f"Hot battery {batt_temp_max:.1f}°C")
      reason_codes.append("hot_batt_temp")
    elif batt_temp_min < self.thresholds.batt_temp_cold and (self._legacy_cold or batt_temp_min > UNKNOWN_TEMP):
      # Cold battery = limited regen, but accel can be normal
      regen = min(regen, 0.4)
      reasons.append(f"Cold battery {batt_temp_min:.1f}°C")
      reason_codes.append("cold_batt_temp")

    # --- Motor/inverter thermal adaptation ---
    if motor_temp > self.thresholds.motor_temp_critical:
      thermal_derating = True
      accel_max = min(accel_max, 0.6)
      reasons.append(f"Critical motor temp {motor_temp:.1f}°C")
      reason_codes.append("critical_motor_temp")
    elif motor_temp > self.thresholds.motor_temp_hot:
      thermal_derating = True
      accel_max = min(accel_max, 1.0)
      reasons.append(f"Hot motor {motor_temp:.1f}°C")
      reason_codes.append("hot_motor_temp")

    if inverter_temp > self.thresholds.inverter_temp_critical:
      thermal_derating = True
      accel_max = min(accel_max, 0.6)
      reasons.append(f"Critical inverter temp {inverter_temp:.1f}°C")
      reason_codes.append("critical_inverter_temp")
    elif inverter_temp > self.thresholds.inverter_temp_hot:
      thermal_derating = True
      accel_max = min(accel_max, 1.2)
      reasons.append(f"Hot inverter {inverter_temp:.1f}°C")
      reason_codes.append("hot_inverter_temp")

    # --- ICE coolant temp adaptation ---
    if coolant_temp > self.thresholds.coolant_temp_high:
      thermal_derating = True
      accel_max = min(accel_max, 1.0)
      reasons.append(f"High coolant {coolant_temp:.1f}°C")
      reason_codes.append("high_coolant_temp")

    # --- Hysteresis to avoid personality oscillation ---
    if target_personality != self._last_personality:
      if now - self._last_change_time < self._hysteresis_sec:
        target_personality = self._last_personality
      else:
        self._last_personality = target_personality
        self._last_change_time = now

    profile.personality = target_personality
    profile.accel_max = accel_max
    profile.accel_min = accel_min
    profile.regen_strength = regen
    profile.thermal_derating = thermal_derating
    profile.reason = "; ".join(reasons) if reasons else "Normal"
    profile.reason_code = ";".join(reason_codes) if reason_codes else "normal"
    profile.enabled = self._enabled

    return profile

  def set_enabled(self, enabled: bool) -> None:
    self._enabled = enabled


