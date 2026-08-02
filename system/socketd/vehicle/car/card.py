#!/usr/bin/env python3
"""Car Interface Daemon (card.py) - Tesla-format protocol.

This is the socketd-owned Tesla/BrownPanda vehicle adapter.
"""
import os
import time
import threading
from typing import Any

import cereal.messaging as messaging

from cereal import car, log

from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process, Priority, Ratekeeper
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.swaglog import cloudlog

# Vehicle-specific adapter modules remain under this socketd package.
from openpilot.system.socketd.vehicle.tesla.values import VEHICLE, CarControllerParams
from openpilot.system.socketd.vehicle.car.carstate import CarState
from openpilot.system.socketd.vehicle.car.carcontroller import CarController
from openpilot.system.socketd.vehicle.safety.safety_manager import SafetyManager, SafetyLimits
from openpilot.system.socketd.vehicle.car.cruise import VCruiseHelper
from openpilot.system.socketd.vehicle.car.events import VehicleEvents
from openpilot.system.socketd import can_capnp_to_list, can_list_to_can_capnp
from opendbc.car.tesla.radar_interface import RadarInterface

REPLAY = "REPLAY" in os.environ
SIMULATION = "SIMULATION" in os.environ
DT_CTRL = 0.01  # 100Hz control loop
EventName = log.OnroadEvent.EventName

# forward
carlog = cloudlog  # Use cloudlog directly


class Car:
  """Car Interface Daemon for Tesla-format protocol."""

  CS: CarState
  CC: CarController
  CP: car.CarParams

  def __init__(self, CS=None, CC=None) -> None:
    self.can_sock = messaging.sub_sock('can', timeout=20)
    self.sm = messaging.SubMaster(['carControl', 'onroadEvents'])
    self.pm = messaging.PubMaster(['sendcan', 'carState', 'carParams', 'carOutput', 'radar3d'])

    self.can_rcv_cum_timeout_counter = 0

    self.CC_prev = car.CarControl.new_message()
    self.CS_prev = car.CarState.new_message()
    self.initialized_prev = False

    self.last_actuators_output = car.CarControl.Actuators()

    self.params = Params()

    self.is_metric = self.params.get_bool("IsMetric")
    self.experimental_mode = self.params.get_bool("ExperimentalMode")

    # No fingerprinting needed (single protocol)
    self.CP = self._get_vehicle_params()

    # Initialize car state and controller
    self.CS = CS or CarState(self.CP)
    self.CC = CC or CarController(self.CP)
    self.can_parsers = self.CS.can_parsers
    self.radar = RadarInterface(self.CP)

    # Layer 1 Safety (replaces panda safety)
    safety_limits = SafetyLimits()
    self.safety = SafetyManager(safety_limits)

    # Cruise helper
    self.v_cruise_helper = VCruiseHelper(self.CP)

    # Events
    self.events = VehicleEvents(self.CP)

    # Ratekeeper for 100Hz loop
    self.rk = Ratekeeper(100, print_delay_threshold=None)

    # Write CarParams for other processes
    self._write_car_params()

    cloudlog.info("Car daemon initialized for Tesla-format protocol")

  def _get_vehicle_params(self) -> car.CarParams:
    """Get vehicle CarParams (replaces fingerprinting)."""
    from openpilot.common.vehicle_config import VehicleType

    CP = car.CarParams.new_message()

    # Read vehicle type from params (simulation or real)
    vehicle_type_name = self.params.get("EOPVehicleType") or "SUV_C"
    vehicle_cfg = VehicleType.get(vehicle_type_name)
    platform = VEHICLE.from_vehicle_config(vehicle_cfg)

    # OpenDBC owns Tesla DBC/parser/controller selection. BrownPanda adapts
    # non-Tesla vehicle geometry at the gateway, so the comma-facing protocol
    # remains the proven Tesla Model 3 party-bus contract.
    CP.carFingerprint = "TESLA_MODEL_3"
    CP.brand = "tesla"
    CP.carName = "tesla"

    # Vehicle specs
    CP.mass = platform.mass
    CP.wheelbase = platform.wheelbase
    CP.steerRatio = platform.steer_ratio
    # OpenDBC's proven Tesla controller consumes these standard vehicle-model
    # fields. BrownPanda supplies the same geometry through EOP params.
    CP.centerToFront = platform.wheelbase * 0.5
    CP.steerRatioRear = 0.0
    CP.rotationalInertia = platform.mass * platform.wheelbase ** 2 / 12.0
    CP.tireStiffnessFront = platform.mass * 9.81 * 0.5 / max(platform.steer_ratio, 1.0)
    CP.tireStiffnessRear = CP.tireStiffnessFront

    # Steering
    CP.steerControlType = car.CarParams.SteerControlType.angle
    CP.steerActuatorDelay = CarControllerParams.STEER_ACTUATOR_DELAY
    CP.steerLimitTimer = CarControllerParams.STEER_LIMIT_TIMER
    CP.steerAtStandstill = CarControllerParams.STEER_AT_STANDSTILL

    # Safety
    CP.safetyModel = car.CarParams.SafetyModel.tesla

    # Longitudinal
    CP.openpilotLongitudinalControl = self.params.get_bool("AlphaLongitudinalEnabled")
    # EOP10 parses BrownPanda v2's converted ARS4-B frames on party bus 0. The parser
    # reports unavailable itself when that stream is absent or stale.
    CP.radarUnavailable = False
    CP.minEnableSpeed = -1.0
    CP.minSteerSpeed = 0.0

    # Stop/go
    CP.vEgoStopping = CarControllerParams.V_EGO_STOPPING
    CP.vEgoStarting = CarControllerParams.V_EGO_STARTING
    CP.stoppingDecelRate = CarControllerParams.STOPPING_DECEL_RATE

    # Alpha longitudinal
    CP.alphaLongitudinalAvailable = True

    # Dashcam only for unsupported models
    CP.dashcamOnly = False

    # Passive mode if no control
    openpilot_enabled = self.params.get_bool("OpenpilotEnabledToggle")
    CP.passive = not openpilot_enabled or CP.dashcamOnly

    if CP.passive:
      CP.safetyModel = car.CarParams.SafetyModel.noOutput

    return CP

  def _write_car_params(self):
    """Write CarParams to params for other processes."""
    cp_bytes = self.CP.to_bytes()
    self.params.put("CarParams", cp_bytes)
    self.params.put_nonblocking("CarParamsCache", cp_bytes)
    self.params.put_nonblocking("CarParamsPersistent", cp_bytes)

  def state_update(self) -> tuple[car.CarState, Any | None]:
    """carState update loop, driven by can."""
    can_strs = messaging.drain_sock_raw(self.can_sock, wait_for_one=True)
    can_list = can_capnp_to_list(can_strs)

    # OpenDBC owns CAN parsing and Tesla CarState translation.
    CS = self.CS.update(can_list)

    # In simulation, Tesla CAN is not present. Override with valid defaults so
    # selfdrived doesn't generate wrongGear/seatbelt/wrongCarMode events.
    if SIMULATION:
      CS.gearShifter = car.CarState.GearShifter.drive
      CS.seatbeltUnlatched = False
      CS.cruiseState.available = True
      CS.canValid = True

    # Process RX messages through safety
    for can_msg in can_list:
      self.safety.process_rx_message(can_msg.address, bytes(can_msg.dat), can_msg.src)

    # Update submaster
    self.sm.update(0)

    can_rcv_valid = len(can_strs) > 0

    # Check for CAN timeout
    if not can_rcv_valid:
      self.can_rcv_cum_timeout_counter += 1

    if can_rcv_valid and REPLAY:
      self.can_log_mono_time = messaging.log_from_bytes(can_strs[0]).logMonoTime

    self.v_cruise_helper.update_v_cruise(CS, self.sm['carControl'].enabled, self.is_metric)
    if self.sm['carControl'].enabled and not self.CC_prev.enabled:
      # Use CarState w/ buttons from the step selfdrived enables on
      self.v_cruise_helper.initialize_v_cruise(self.CS_prev, self.experimental_mode)

    # TODO: mirror the carState.cruiseState struct?
    CS.vCruise = float(self.v_cruise_helper.v_cruise_kph)
    CS.vCruiseCluster = float(self.v_cruise_helper.v_cruise_cluster_kph)

    RD = self.radar.update(can_list)
    return CS, RD

  def state_publish(self, CS: car.CarState, RD=None):
    """carState and carParams publish loop."""

    # carParams - logged every 50 seconds (> 1 per segment)
    if self.sm.frame % int(50. / DT_CTRL) == 0:
      cp_send = messaging.new_message('carParams')
      cp_send.valid = True
      cp_send.carParams = self.CP
      self.pm.send('carParams', cp_send)

    # Re-write CarParams to params store every 10 frames (~100ms at 100Hz) so
    # selfdrived.params.get("CarParams", block=True) unblocks quickly even if
    # the manager cleared it on the started=True onroad-transition.
    if self.sm.frame % 10 == 0:
      self._write_car_params()

    # publish new carOutput
    co_send = messaging.new_message('carOutput')
    co_send.valid = self.sm.all_checks(['carControl'])
    co_send.carOutput.actuatorsOutput = self.last_actuators_output
    self.pm.send('carOutput', co_send)

    # kick off controlsd step while we actuate the latest carControl packet
    cs_send = messaging.new_message('carState')
    cs_send.valid = CS.canValid
    cs_send.carState = CS
    cs_send.carState.canErrorCounter = self.can_rcv_cum_timeout_counter
    cs_send.carState.cumLagMs = -self.rk.remaining * 1000.
    self.pm.send('carState', cs_send)

    # publish radar tracks when available (feeds radard.py)
    if RD is not None:
      tracks_msg = messaging.new_message('radar3d')
      tracks_msg.valid = not (RD.errors)
      tracks_msg.radar3d = RD
      self.pm.send('radar3d', tracks_msg)

  def controls_update(self, CS: car.CarState, CC: car.CarControl):
    """control update loop, driven by carControl."""

    if not self.initialized_prev:
      # Initialize CarInterface, once controls are ready
      # TODO: this can make us miss at least a few cycles when doing an ECU knockout
      # signal socketd to switch to car safety mode
      self.params.put_bool_nonblocking("ControlsReady", True)

    if self.sm.all_alive(['carControl']):
      # Update safety heartbeat
      self.safety.update_heartbeat(CC.latActive or CC.longActive)

      # SteamD defense-in-depth: clamp actuators if local driver is overriding.
      # This catches the single-frame race before SteamD clears the param.
      if CC.latActive or CC.longActive:
        if CS.brakePressed:
          CC.actuators.accel = min(CC.actuators.accel, 0.0)
          CC.actuators.gas = 0.0
        if abs(CS.steeringTorque) > 200:  # DRIVER_TORQUE_THRESHOLD
          CC.actuators.steer = 0.0
          CC.actuators.steeringAngleDeg = 0.0

      # send car controls over can
      now_nanos = self.can_log_mono_time if REPLAY else int(time.monotonic() * 1e9)
      self.last_actuators_output, can_sends = self.CC.update(CC, self.CS, now_nanos)

      # Layer 1 Safety: Check all TX messages before sending
      safe_can_sends = []
      for addr, data, bus in can_sends:
        if self.safety.check_tx_message(addr, data, bus):
          safe_can_sends.append((addr, data, bus))
        else:
          cloudlog.warning(f"Safety blocked message 0x{addr:03X}")

      # Publish sendcan (only safe messages)
      if safe_can_sends:
        self.pm.send('sendcan', can_list_to_can_capnp(safe_can_sends, msgtype='sendcan', valid=CS.canValid))

      self.CC_prev = CC

  def step(self):
    CS, RD = self.state_update()

    self.state_publish(CS, RD)

    initialized = (not any(e.name == EventName.selfdriveInitializing for e in self.sm['onroadEvents'].events) and
                   self.sm.seen['onroadEvents'])
    if not self.CP.passive and initialized:
      self.controls_update(CS, self.sm['carControl'])

    self.initialized_prev = initialized
    self.CS_prev = CS

    # Periodic safety tick
    self.safety.safety_tick()

  def params_thread(self, evt):
    while not evt.is_set():
      self.is_metric = self.params.get_bool("IsMetric")
      self.experimental_mode = self.params.get_bool("ExperimentalMode") and self.CP.openpilotLongitudinalControl
      time.sleep(0.1)

  def card_thread(self):
    e = threading.Event()
    t = threading.Thread(target=self.params_thread, args=(e, ))
    try:
      t.start()
      while True:
        self.step()
        self.rk.monitor_time()
    finally:
      e.set()
      t.join()


def main():
  set_daemon_affinity("socketd")
  config_realtime_process(DT_CTRL, Priority.CTRL_HIGH)
  car = Car()
  car.card_thread()


if __name__ == "__main__":
  main()
