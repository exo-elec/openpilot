#!/usr/bin/env python3
"""adaptd: adaptive driving daemon (moved from EOP10's selfdrive/adaptd; shared by every branch).

Turns vehicle telemetry (battery SOC, range, temperatures; engine coolant for ICE) into `adaptiveDrivingState`: a recommended
personality and acceleration / deceleration / regen limits, applied by controlsd (accel clamp) and selfdrived (personality) through
nagaspilot/controls/ngp_adaptive_limits.py. The computation is nagaspilot/controls/ngp_adaptive_driving.py.

Inputs, in order: `ncpVehicleData` (NavPilot's interpreted data, delivered over BLE by bluetoothd) and, when that is absent,
`obdState` (basic OBD-II read straight from the car by obd2d over CAN with UDS/ISO-TP: no BLE needed). Without either it publishes
"Waiting for vehicle data". Default off (`ngp_adaptd_enabled`).
"""
from __future__ import annotations

import logging
import time

from nagaspilot.controls.ngp_adaptive_driving import AdaptiveDrivingComputer, AdaptiveProfile

try:
  import cereal.messaging as messaging
  from cereal import custom
  from openpilot.common.params import Params
  from openpilot.common.realtime import DT_CTRL, Ratekeeper
except Exception:       # no built cereal (dev PC tests)
  messaging = None
  custom = None
  Params = None
  Ratekeeper = None
  DT_CTRL = 0.01

logger = logging.getLogger('adaptd')


class AdaptD:
  """adaptd daemon."""

  RATE = 2.0  # Hz

  def __init__(self, enabled_key: str = "ngp_adaptd_enabled", legacy_cold_sentinel_bug: bool = False):
    """`enabled_key`: the param that switches it on (EOP10's shim passes EOPAdaptdEnabled and keeps its original cold-battery
    behaviour). Inputs: `ncpVehicleData` (NavPilot over BLE) or, when that is absent, `obdState` (basic OBD-II read from the car)."""
    self.params = Params() if Params else None
    self._enabled_key = enabled_key
    self.computer = AdaptiveDrivingComputer(legacy_cold_sentinel_bug=legacy_cold_sentinel_bug)
    self._enabled = False
    self._last_data_time: float = 0.0
    self._data_timeout_sec: float = 60.0
    self._last_profile: AdaptiveProfile | None = None

    if messaging:
      self.pm = messaging.PubMaster(['adaptiveDrivingState'])
      self.sm = messaging.SubMaster(['ncpVehicleData', 'obdState'], frequency=int(1 / DT_CTRL))
    else:
      self.pm = None
      self.sm = None

  def _obd_state_to_vehicle_data(self, obd):
    """Convert obdState to NcpVehicleData for fallback adaptation.

    obd2d provides basic Mode 01 PIDs. We map what we have; missing
    fields stay at sentinel values so the computer ignores them.
    """
    vd = custom.NcpVehicleData.new_message()
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
    vd.timestamp = int(time.time() * 1e9)  # noqa: TID251
    return vd

  def _refresh_params(self) -> None:
    """Refresh daemon params."""
    if not self.params:
      return
    self._enabled = self.params.get_bool(self._enabled_key)
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

    profile: AdaptiveProfile | None = None
    vd = None

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
  parser = argparse.ArgumentParser(description='adaptd: adaptive driving daemon')
  parser.add_argument('--debug', action='store_true', help='Enable debug logging')
  args = parser.parse_args()
  logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format='%(asctime)s %(name)s %(levelname)s: %(message)s')
  AdaptD().run()


if __name__ == '__main__':
  main()
