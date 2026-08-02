"""Pure adaptive profile computer for gateway-normalized telemetry.

BLE, NCP, OBD transport, and vehicle-specific PID interpretation stay outside
NGP10. The returned limits are proposals and have no control consumer.
"""

from dataclasses import dataclass
from enum import IntEnum


class AdaptivePersonality(IntEnum):
  AGGRESSIVE = 0
  STANDARD = 1
  RELAXED = 2


@dataclass(frozen=True)
class VehicleTelemetry:
  valid: bool
  battery_soc: float | None = None
  range_remaining_km: float | None = None
  battery_temp_min_c: float | None = None
  battery_temp_max_c: float | None = None
  motor_temp_c: float | None = None
  inverter_temp_c: float | None = None
  coolant_temp_c: float | None = None


@dataclass(frozen=True)
class AdaptiveProfile:
  personality: AdaptivePersonality
  accel_max: float
  accel_min: float
  regen_strength: float
  thermal_derating: bool
  reasons: tuple[str, ...]
  valid: bool
  control_authority: bool = False


class NGP10AdaptiveProfile:
  DEFAULT_ACCEL_MAX = 2.0
  DEFAULT_ACCEL_MIN = -3.48

  def __init__(self, personality_hysteresis_s: float = 10.0):
    self.personality_hysteresis_s = max(0.0, float(personality_hysteresis_s))
    self._personality = AdaptivePersonality.STANDARD
    self._last_personality_change = float("-inf")

  @staticmethod
  def _above(value, threshold):
    return value is not None and value > threshold

  @staticmethod
  def _below(value, threshold):
    return value is not None and value <= threshold

  def update(self, telemetry: VehicleTelemetry, now: float) -> AdaptiveProfile:
    if not telemetry.valid:
      return AdaptiveProfile(self._personality, self.DEFAULT_ACCEL_MAX, self.DEFAULT_ACCEL_MIN,
                             1.0, False, ("no_data",), False)

    target = AdaptivePersonality.STANDARD
    accel_max = self.DEFAULT_ACCEL_MAX
    regen = 1.0
    thermal_derating = False
    reasons = []

    if self._below(telemetry.battery_soc, 10.0):
      target = AdaptivePersonality.RELAXED
      accel_max = min(accel_max, 1.0)
      regen = min(regen, 0.5)
      reasons.append("critical_soc")
    elif self._below(telemetry.battery_soc, 20.0):
      target = AdaptivePersonality.RELAXED
      accel_max = min(accel_max, 1.4)
      reasons.append("low_soc")
    elif self._above(telemetry.battery_soc, 80.0):
      regen = min(regen, 0.6)
      reasons.append("high_soc")

    if self._below(telemetry.range_remaining_km, 25.0):
      target = AdaptivePersonality.RELAXED
      accel_max = min(accel_max, 0.8)
      reasons.append("critical_range")
    elif self._below(telemetry.range_remaining_km, 50.0):
      target = AdaptivePersonality.RELAXED
      accel_max = min(accel_max, 1.2)
      reasons.append("low_range")

    if self._above(telemetry.battery_temp_max_c, 55.0):
      thermal_derating = True
      accel_max = min(accel_max, 0.8)
      regen = min(regen, 0.5)
      reasons.append("critical_battery_temperature")
    elif self._above(telemetry.battery_temp_max_c, 45.0):
      thermal_derating = True
      accel_max = min(accel_max, 1.2)
      reasons.append("hot_battery")
    if telemetry.battery_temp_min_c is not None and telemetry.battery_temp_min_c < 0.0:
      regen = min(regen, 0.4)
      reasons.append("cold_battery")

    thermal_checks = (
      (telemetry.motor_temp_c, 100.0, 80.0, "motor"),
      (telemetry.inverter_temp_c, 90.0, 70.0, "inverter"),
    )
    for value, critical, hot, name in thermal_checks:
      if self._above(value, critical):
        thermal_derating = True
        accel_max = min(accel_max, 0.6)
        reasons.append(f"critical_{name}_temperature")
      elif self._above(value, hot):
        thermal_derating = True
        accel_max = min(accel_max, 1.0 if name == "motor" else 1.2)
        reasons.append(f"hot_{name}")
    if self._above(telemetry.coolant_temp_c, 100.0):
      thermal_derating = True
      accel_max = min(accel_max, 1.0)
      reasons.append("hot_coolant")

    if target != self._personality and float(now) - self._last_personality_change >= self.personality_hysteresis_s:
      self._personality = target
      self._last_personality_change = float(now)

    return AdaptiveProfile(
      personality=self._personality,
      accel_max=accel_max,
      accel_min=self.DEFAULT_ACCEL_MIN,
      regen_strength=regen,
      thermal_derating=thermal_derating,
      reasons=tuple(reasons) or ("normal",),
      valid=True,
    )
