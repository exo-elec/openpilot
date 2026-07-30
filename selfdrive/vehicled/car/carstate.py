#!/usr/bin/env python3
"""Vehicle state parser (Tesla-format protocol).

Parses CAN messages from vehicles using Tesla-format protocol and produces CarState.
"""
from __future__ import annotations

import copy
from typing import Any

from cereal import car
from openpilot.selfdrive.vehicled.tesla.values import (
  CANBUS, GEAR_MAP, STEER_THRESHOLD, DBC
)

# Conversion factors
KPH_TO_MS = 1 / 3.6
MPH_TO_MS = 0.44704


class CarState:
  """Parses CAN messages into CarState.
  
  This replaces the generic CarState with a protocol-specific
  implementation that doesn't require the opendbc_repo submodule.
  """
  
  def __init__(self, CP: car.CarParams):
    self.CP = CP
    self.can_define = None  # Will be initialized with DBC info
    
    # State tracking
    self.autopark = False
    self.autopark_prev = False
    self.cruise_enabled_prev = False
    self.hands_on_level = 0
    self.das_control: dict[str, Any | None] = None
    
    # CAN value dictionaries (populated from parsed CAN data)
    self.shifter_values = GEAR_MAP
    
  def update_autopark_state(self, autopark_state: str, cruise_enabled: bool) -> None:
    """Update autopark state tracking."""
    autopark_now = autopark_state in ("ACTIVE", "COMPLETE", "SELFPARK_STARTED")
    if autopark_now and not self.autopark_prev and not self.cruise_enabled_prev:
      self.autopark = True
    if not autopark_now:
      self.autopark = False
    self.autopark_prev = autopark_now
    self.cruise_enabled_prev = cruise_enabled
    
  def update_steering_pressed(self, pressed: bool, min_frames: int = 5) -> bool:
    """Hysteresis for steering pressed detection."""
    if not hasattr(self, '_steering_pressed_frames'):
      self._steering_pressed_frames = 0
      self._steering_pressed = False
      
    if pressed:
      self._steering_pressed_frames += 1
    else:
      self._steering_pressed_frames = 0
      
    if self._steering_pressed_frames >= min_frames:
      self._steering_pressed = True
    elif self._steering_pressed_frames == 0:
      self._steering_pressed = False
      
    return self._steering_pressed
    
  def update(self, can_parsers: dict[int, Any]) -> car.CarState:
    """Update vehicle state from CAN parsers."""
    # Get parsers for each bus
    cp_party = can_parsers.get(CANBUS.party)
    cp_ap_party = can_parsers.get(CANBUS.autopilot_party)
    
    if cp_party is None or cp_ap_party is None:
      return car.CarState.new_message()
    
    ret = car.CarState.new_message()
    
    # --- Vehicle speed ---
    # DI_speed message on party bus
    di_speed = cp_party.vl.get("DI_speed", {})
    ret.vEgoRaw = di_speed.get("DI_vehicleSpeed", 0) * KPH_TO_MS
    # Simple low-pass filter for vEgo
    ret.vEgo = ret.vEgoRaw
    ret.aEgo = 0.0  # Would need to compute from vEgo derivative
    
    # --- Gas pedal ---
    di_system = cp_party.vl.get("DI_systemStatus", {})
    ret.gasPressed = di_system.get("DI_accelPedalPos", 0) > 0
    
    # --- Brake pedal ---
    ibst_status = cp_party.vl.get("IBST_status", {})
    esp_status = cp_party.vl.get("ESP_status", {})
    ret.brake = 0.0
    ret.brakePressed = (
        ibst_status.get("IBST_driverBrakeApply", 0) == 2 or
        esp_status.get("ESP_driverBrakeApply", 0) == 2
    )
    
    # --- Steering ---
    epas_status = cp_party.vl.get("EPAS3S_sysStatus", {})
    self.hands_on_level = epas_status.get("EPAS3S_handsOnLevel", 0)
    ret.steeringAngleDeg = -epas_status.get("EPAS3S_internalSAS", 0)
    
    # Steering rate from autopilot party bus
    sccm_angle = cp_ap_party.vl.get("SCCM_steeringAngleSensor", {})
    ret.steeringRateDeg = -sccm_angle.get("SCCM_steeringAngleSpeed", 0)
    
    ret.steeringTorque = -epas_status.get("EPAS3S_torsionBarTorque", 0)
    
    # Steering pressed detection with hysteresis
    ret.steeringPressed = self.update_steering_pressed(
      abs(ret.steeringTorque) > STEER_THRESHOLD
    )
    
    # Parse EAC status
    eac_status_val = epas_status.get("EPAS3S_eacStatus", 0)
    eac_status_map = {0: "EAC_INACTIVE", 1: "EAC_ACTIVE", 2: "EAC_INHIBITED", 3: "EAC_FAULT"}
    eac_status = eac_status_map.get(eac_status_val, "EAC_INACTIVE")
    
    ret.steerFaultPermanent = eac_status == "EAC_FAULT"
    ret.steerFaultTemporary = eac_status == "EAC_INHIBITED"
    
    # --- Cruise state ---
    di_state = cp_party.vl.get("DI_state", {})
    cruise_state_val = di_state.get("DI_cruiseState", 0)
    cruise_state_map = {
      0: "OFF", 1: "STANDBY", 2: "ENABLED", 3: "STANDSTILL", 
      4: "OVERRIDE", 5: "PRE_FAULT", 6: "PRE_CANCEL", 7: "FAULT"
    }
    cruise_state = cruise_state_map.get(cruise_state_val, "OFF")
    
    speed_units_val = di_state.get("DI_speedUnits", 0)
    speed_units_map = {0: "KPH", 1: "MPH"}
    speed_units = speed_units_map.get(speed_units_val, "KPH")
    
    autopark_state_val = di_state.get("DI_autoparkState", 0)
    autopark_map = {
      0: "INACTIVE", 1: "ACTIVE", 2: "COMPLETE", 3: "SELFPARK_STARTED",
      4: "ABORTING", 5: "ABORTED"
    }
    autopark_state = autopark_map.get(autopark_state_val, "INACTIVE")
    
    cruise_enabled = cruise_state in ("ENABLED", "STANDSTILL", "OVERRIDE", "PRE_FAULT", "PRE_CANCEL")
    self.update_autopark_state(autopark_state, cruise_enabled)
    
    ret.cruiseState.enabled = cruise_enabled and not self.autopark
    
    digital_speed = di_state.get("DI_digitalSpeed", 0)
    if speed_units == "KPH":
      ret.cruiseState.speed = max(digital_speed * KPH_TO_MS, 1e-3)
    else:
      ret.cruiseState.speed = max(digital_speed * MPH_TO_MS, 1e-3)
      
    ret.cruiseState.available = cruise_state == "STANDBY" or ret.cruiseState.enabled
    ret.cruiseState.standstill = False  # Can resume from stop without special handling
    ret.standstill = cruise_state == "STANDSTILL"
    ret.accFaulted = cruise_state == "FAULT"
    
    # --- Gear ---
    gear_val = di_system.get("DI_gear", 0)
    gear_map_inv = {v: k for k, v in GEAR_MAP.items()}
    gear_str = gear_map_inv.get(gear_val, "DI_GEAR_INVALID")
    ret.gearShifter = GEAR_MAP.get(gear_str, car.CarState.GearShifter.unknown)
    
    # --- Doors ---
    ui_warning = cp_party.vl.get("UI_warning", {})
    ret.doorOpen = ui_warning.get("anyDoorOpen", 0) == 1
    
    # --- Blinkers ---
    ret.leftBlinker = ui_warning.get("leftBlinkerBlinking", 0) in (1, 2)
    ret.rightBlinker = ui_warning.get("rightBlinkerBlinking", 0) in (1, 2)
    
    # --- Seatbelt ---
    ret.seatbeltUnlatched = ui_warning.get("buckleStatus", 0) != 1
    
    # --- Blindspot ---
    # DAS_status (0x39B) — TC275 sends on CAN0 (autopilot_party bus 2)
    das_status = cp_ap_party.vl.get("DAS_status", {})
    ret.leftBlindspot = das_status.get("DAS_blindSpotRearLeft", 0) != 0
    ret.rightBlindspot = das_status.get("DAS_blindSpotRearRight", 0) != 0

    # EOP: Dashboard speed limit (kph -> m/s)
    dash_speed_limit = das_status.get("DAS_speedLimit", 0)
    if dash_speed_limit > 0:
      ret.speedLimit = dash_speed_limit * KPH_TO_MS
    else:
      ret.speedLimit = 0.0

    # --- ALCC state (TC275 0x700) ---
    # confidence=85: real driver hands on (normal), 20: TC275-managed fake (alarm only), 0: ALCC inactive
    # TC275 suppresses handsOnLevel in 0x370 to 0 when ALCC active, preventing openpilot auto-disengage.
    alcc_msg = cp_party.vl.get("ALCC_state", {})
    self.alcc_confidence = int(alcc_msg.get("ALCC_confidence", 0))
    self.alcc_active = alcc_msg.get("ALCC_active", 0) == 1
    
    # --- AEB ---
    das_control = cp_ap_party.vl.get("DAS_control", {})
    ret.stockAeb = das_control.get("DAS_aebEvent", 0) == 1
    
    # --- LKAS ---
    das_steering = cp_ap_party.vl.get("DAS_steeringControl", {})
    ret.stockLkas = das_steering.get("DAS_steeringControlType", 0) == 2  # LANE_KEEP_ASSIST
    
    # --- Invalid LKAS setting check ---
    das_settings = cp_ap_party.vl.get("DAS_settings", {})
    if self.CP.carFingerprint in ("SEDAN_D", "SUV_D"):
      ret.invalidLkasSetting = das_settings.get("DAS_autosteerEnabled", 0) != 0
    
    # Store DAS control for carcontroller
    self.das_control = copy.copy(das_control)
    
    # CAN validity
    ret.canValid = True
    
    return ret
    
  @staticmethod
  def get_can_parsers(CP: car.CarParams) -> dict[int, Any]:
    """Get CAN parsers for each bus."""
    from openpilot.selfdrive.vehicled.tesla.tesla_parser import SimpleCANParser
    
    dbc_file = DBC.get(CP.carFingerprint, {}).get(CANBUS.party, "tesla_model3_party")
    
    return {
      CANBUS.party: SimpleCANParser(dbc_file, [], CANBUS.party),
      CANBUS.autopilot_party: SimpleCANParser(dbc_file, [], CANBUS.autopilot_party)
    }
