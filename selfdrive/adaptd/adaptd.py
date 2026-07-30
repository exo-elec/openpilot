#!/usr/bin/env python3
"""adaptd — Adaptive driving daemon.

Receives interpreted OBD VehicleData from NavPilot via BLE/NCP,
computes adaptive driving profiles (personality, accel limits),
and publishes adaptiveDrivingState for controlsd/selfdrived.

Architecture (clean separation):
    obd2d ──(raw OBD, NO interpretation)──> BLE ──> NavPilot
    NavPilot ──(interprets Mode 22 PIDs, subscription value)──> BLE ──> bluetoothd SPP
    bluetoothd ──(ncpVehicleData)──> adaptd ──(adaptiveDrivingState)──> controlsd

Design principle: obd2d does ZERO interpretation. All PID decoding, proprietary
Mode 22 parsing, and vehicle-specific logic lives in NavPilot (subscription).
adaptd ONLY consumes ncpVehicleData from NavPilot — never from obd2d.
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Optional

from cereal import log
from cereal import custom

try:
  import cereal.messaging as messaging
  from openpilot.common.params import Params
  from openpilot.common.realtime import Ratekeeper, DT_CTRL
except ImportError:
  messaging = None
  Params = None
  Ratekeeper = None
  DT_CTRL = 0.01

logger = logging.getLogger('adaptd')

LongitudinalPersonality = log.LongitudinalPersonality
NcpVehicleData = custom.NcpVehicleData


# ---------------------------------------------------------------------------
# Thresholds & tuning
# ---------------------------------------------------------------------------

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
  personality: int = LongitudinalPersonality.standard
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

  def __init__(self, thresholds: Thresholds = THRESHOLDS):
    self.thresholds = thresholds
    self._last_personality: int = LongitudinalPersonality.standard
    self._hysteresis_sec: float = 10.0  # Min time between personality changes
    self._last_change_time: float = 0.0
    self._enabled: bool = False

  def update(self, vd: NcpVehicleData) -> AdaptiveProfile:
    """Compute adaptive profile from NcpVehicleData."""
    now = time.monotonic()
    profile = AdaptiveProfile(timestamp=int(now * 1e9))
    profile.enabled = self._enabled

    if not vd.valid:
      profile.reason = "No vehicle data"
      profile.reason_code = "no_data"
      return profile

    reasons: list[str] = []
    reason_codes: list[str] = []

    # Default to standard
    target_personality = LongitudinalPersonality.standard
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
        target_personality = LongitudinalPersonality.traffic
        accel_max = min(accel_max, 1.0)
        regen = min(regen, 0.5)  # Limit regen to avoid voltage spikes
        reasons.append(f"Critical SOC {soc:.0f}%")
        reason_codes.append("critical_soc")
      elif soc <= self.thresholds.soc_low:
        target_personality = LongitudinalPersonality.relaxed
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
        target_personality = max(target_personality, LongitudinalPersonality.traffic)
        accel_max = min(accel_max, 0.8)
        reasons.append(f"Critical range {range_km:.0f}km")
        reason_codes.append("critical_range")
      elif range_km <= self.thresholds.range_low:
        target_personality = max(target_personality, LongitudinalPersonality.relaxed)
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
    elif batt_temp_min < self.thresholds.batt_temp_cold:
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


class AdaptD:
  """adaptd daemon."""

  RATE = 2.0  # Hz

  def __init__(self):
    self.params = Params() if Params else None
    self.computer = AdaptiveDrivingComputer()
    self._enabled = False
    self._last_data_time: float = 0.0
    self._data_timeout_sec: float = 60.0
    self._last_profile: Optional[AdaptiveProfile] = None

    if messaging:
      self.pm = messaging.PubMaster(['adaptiveDrivingState'])
      self.sm = messaging.SubMaster(['ncpVehicleData', 'obdState'], frequency=int(1 / DT_CTRL))
    else:
      self.pm = None
      self.sm = None

  def _obd_state_to_vehicle_data(self, obd) -> NcpVehicleData:
    """Convert obdState to NcpVehicleData for fallback adaptation.

    obd2d provides basic Mode 01 PIDs. We map what we have; missing
    fields stay at sentinel values so the computer ignores them.
    """
    vd = NcpVehicleData.new_message()
    vd.valid = obd.obdConnected
    vd.batterySoc = obd.batterySoc if obd.batterySoc >= 0 else -1.0
    vd.batteryVoltage = obd.batteryVoltage if obd.batteryVoltage > 0 else -1.0
    vd.batteryCurrent = obd.batteryCurrent if obd.batteryCurrent != 0 else 0.0
    vd.batteryTempMax = obd.batteryTempMax if obd.batteryTempMax > -40 else -273.0
    vd.batteryTempMin = obd.batteryTempMin if obd.batteryTempMin > -40 else -273.0
    vd.rangeRemaining = obd.rangeRemaining if obd.rangeRemaining > 0 else -1.0
    vd.motorTemp = obd.motorTemp if obd.motorTemp > -40 else -273.0
    vd.inverterTemp = obd.inverterTemp if obd.inverterTemp > -40 else -273.0
    vd.engineRpm = obd.engineRpm if obd.engineRpm > 0 else -1.0
    vd.coolantTemp = obd.coolantTemp if obd.coolantTemp > -40 else -273.0
    vd.throttlePos = obd.throttlePos if obd.throttlePos >= 0 else -1.0
    vd.engineLoad = obd.engineLoad if obd.engineLoad >= 0 else -1.0
    vd.fuelLevel = obd.fuelLevel if obd.fuelLevel >= 0 else -1.0
    vd.vehicleSpeed = obd.vehicleSpeed if obd.vehicleSpeed >= 0 else -1.0
    vd.odometer = obd.odometer if obd.odometer > 0 else -1.0
    vd.vin = obd.vin
    vd.vehicleType = obd.vehicleType
    vd.timestamp = int(time.time() * 1e9)
    return vd

  def _refresh_params(self) -> None:
    """Refresh daemon params."""
    if not self.params:
      return
    self._enabled = self.params.get_bool('EOPAdaptdEnabled')
    self.computer.set_enabled(self._enabled)

  def _publish_profile(self, profile: AdaptiveProfile) -> None:
    """Publish adaptiveDrivingState."""
    if not self.pm or not messaging:
      return

    msg = messaging.new_message('adaptiveDrivingState')
    ads = msg.adaptiveDrivingState
    ads.enabled = profile.enabled
    ads.personality = profile.personality
    ads.reason = profile.reason
    ads.reasonCode = profile.reason_code
    ads.accelMax = profile.accel_max
    ads.decelMax = abs(profile.accel_min)
    ads.regenStrength = profile.regen_strength
    ads.thermalDerating = profile.thermal_derating
    ads.soc = -1.0
    ads.rangeKm = -1.0
    ads.batteryTemp = -273.0
    ads.motorTemp = -273.0
    ads.coolantTemp = -273.0
    ads.timestamp = profile.timestamp

    # Populate raw telemetry if available from last vehicle data
    if self.sm and self.sm.updated['ncpVehicleData']:
      vd = self.sm['ncpVehicleData']
      ads.soc = vd.batterySoc
      ads.rangeKm = vd.rangeRemaining
      ads.batteryTemp = vd.batteryTempMax
      ads.motorTemp = vd.motorTemp
      ads.coolantTemp = vd.coolantTemp

    self.pm.send('adaptiveDrivingState', msg)

  def update(self) -> None:
    """Main update loop."""
    self._refresh_params()

    if self.sm:
      self.sm.update(0)

    if not self._enabled:
      return

    profile: Optional[AdaptiveProfile] = None
    vd: Optional[NcpVehicleData] = None

    if self.sm and self.sm.updated['ncpVehicleData']:
      vd = self.sm['ncpVehicleData']
    elif self.sm and self.sm.updated['obdState']:
      # Fallback: use obd2d's basic OBD data when NavPilot isn't connected
      vd = self._obd_state_to_vehicle_data(self.sm['obdState'])

    if vd is not None:
      self._last_data_time = time.monotonic()
      profile = self.computer.update(vd)
      self._last_profile = profile
    elif self._last_profile is not None:
      # Use stale data if within timeout
      if time.monotonic() - self._last_data_time < self._data_timeout_sec:
        profile = self._last_profile
      else:
        profile = AdaptiveProfile(
          reason="Vehicle data timeout",
          reason_code="data_timeout",
          enabled=self._enabled,
        )
    else:
      profile = AdaptiveProfile(
        reason="Waiting for vehicle data",
        reason_code="waiting",
        enabled=self._enabled,
      )

    self._publish_profile(profile)

  def run(self) -> None:
    """Run daemon loop."""
    if not Ratekeeper:
      logger.warning("Ratekeeper not available — running once")
      self.update()
      return

    rk = Ratekeeper(self.RATE)
    logger.info(f"adaptd started (rate={self.RATE}Hz, enabled={self._enabled})")

    while True:
      self.update()
      rk.keep_time()


def main() -> None:
  import argparse
  parser = argparse.ArgumentParser(description='adaptd — Adaptive driving daemon')
  parser.add_argument('--debug', action='store_true', help='Enable debug logging')
  args = parser.parse_args()

  logging.basicConfig(
    level=logging.DEBUG if args.debug else logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
  )

  d = AdaptD()
  d.run()


if __name__ == '__main__':
  main()
