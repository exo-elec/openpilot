"""
Gateway carstate
================

This module holds every signal-parsing detail for gateway vehicles.  To keep
the file readable we group logic into the following sections:

1.  Imports & helper lookups (button map, gear map, stalk states)
2.  Small checksum/counter validation helpers
3.  The CarState class itself (detection helpers, constructor, update routine)
4.  CAN parser definitions (which messages we listen to on each bus)

Whenever a new model is added, its parsing quirks should live here so anyone
reading the file can follow the labeled sections.
"""

# ---------------------------------------------------------------------------
# Imports and lookup tables
# ---------------------------------------------------------------------------

import copy
from collections import defaultdict

# OpenPilot framework imports
from opendbc.can.parser import CANParser
from opendbc.can.can_define import CANDefine
from opendbc.car import Bus, create_button_events, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.gateway.values import DBC, STEER_THRESHOLD, CAR
from opendbc.car.gateway.gatewaycan import CanBus, get_model_messages, inverted_sum_checksum
from opendbc.car.interfaces import CarStateBase

ButtonType = structs.CarState.ButtonEvent.Type
GearShifter = structs.CarState.GearShifter

# Button and control mapping constants (physical -> cereal events)
BUTTONS_DICT = {
  # Core cruise control buttons (from B_0x3B0_ButtonState)
  "btnCruiseScrollUp": ButtonType.accelCruise,       # Speed up / Resume  (CruiseScroll_Direction=3)
  "btnCruiseScrollDn": ButtonType.decelCruise,       # Speed down / Set   (CruiseScroll_Direction=1)
  "btnCruiseSet": ButtonType.setCruise,              # Set cruise speed
  "btnAccOnOff": ButtonType.mainCruise,              # ACC main on/off

  # Following distance control (from B_0x3B0_ButtonState)
  "btnDistFar": ButtonType.gapAdjustCruise,          # Increase following distance
  "btnDistNr": ButtonType.gapAdjustCruise,           # Decrease following distance

  # Lane keeping (from B_0x3B0_ButtonState)
  "btnLKA": ButtonType.lkas,                         # Lane keeping enable

  # Turn signals (from B_0x133_StalkState)
  "btnBlinkL": ButtonType.leftBlinker,               # Left turn signal
  "btnBlinkR": ButtonType.rightBlinker,              # Right turn signal
}

# Transmission gear mapping: DBC values → cereal GearShifter enum
# From B_0x242_VCU_DriveState.Gear fields
GEAR_DICT = {
  0: GearShifter.park,     # P - Park
  1: GearShifter.reverse,  # R - Reverse
  2: GearShifter.neutral,  # N - Neutral (D in DBC value table is actually N)
  3: GearShifter.drive,    # D - Drive (N in DBC value table is actually D)
}

# Turn signal stalk states (from B_0x133_BCM_StalkState.MPC turn signals)
TURN_SIGNAL_STATES = {
  1: (False, False),  # Neutral
  2: (True, False),   # LeftPartial
  3: (True, False),   # LeftFull
  4: (False, True),   # RightPartial
  5: (False, True),   # RightFull
}


# ---------------------------------------------------------------------------
# Checksum / counter helper functions
# ---------------------------------------------------------------------------

def _checksum_field(msg_dict):
  for key in msg_dict.keys():
    if 'Checksum' in key or 'CRC' in key:
      return key
  return None


def _counter_field(msg_dict):
  for key in msg_dict.keys():
    if 'Counter' in key:
      return key
  return None


def validate_message(cp, msg_name):
  """Simple checksum validation using inverted sum."""
  try:
    msg_data = cp.vl[msg_name]
    raw_data = cp.to_can_parser.msgs[msg_name]['data']
    if raw_data and len(raw_data) >= 8:
      checksum_key = _checksum_field(msg_data)
      if checksum_key is None:
        return True
      expected_checksum = inverted_sum_checksum(raw_data[:7])
      actual_checksum = int(msg_data.get(checksum_key, expected_checksum))
      return expected_checksum == actual_checksum
  except (KeyError, TypeError):
    pass
  return True  # No validation if checksum not available


def check_counter(self, msg_name, msg_dict):
  """Simple counter freshness check (4-bit counter, wraps at 16)."""
  counter_key = _counter_field(msg_dict)
  if counter_key is None:
    return True

  counter_val = int(msg_dict[counter_key]) & 0xF

  last_counter = self.msg_counters.get(msg_name)
  if last_counter is None:
    self.msg_counters[msg_name] = counter_val
    return True

  expected_counter = (last_counter + 1) & 0xF

  # Allow some tolerance for missed messages
  counter_ok = counter_val in {expected_counter, (expected_counter + 1) & 0xF}
  if counter_ok:
    self.msg_counters[msg_name] = counter_val

  return counter_ok


# ---------------------------------------------------------------------------
# Main state container exposed to the rest of openpilot
# ---------------------------------------------------------------------------
class CarState(CarStateBase):

  @staticmethod
  def detect_car_model(can_recv):
    """Detect BYD DOLPHIN from stock CAN messages

    Uses stock messages that are always present during fingerprinting.
    0x6F0 diagnostic is excluded as it's only sent by our controller.
    """
    # BYD ATTO3 uses MPC_* message naming in its DBC
    if 'A_0x1E2_MPC_Lateral_Cmd_L8_20ms' in can_recv:
      return CAR.BYD_ATTO3

    # BYD DOLPHIN detection: Check for unique message presence (M2E naming)
    if 'A_0x1E2_M2E_Lateral_Cmd_L8_20ms' in can_recv:
      return CAR.BYD_DOLPHIN

    return None


  def __init__(self, CP):
    super().__init__(CP)
    can_define = CANDefine(DBC[CP.carFingerprint][Bus.pt])
    self.msgs = get_model_messages(CP.carFingerprint)

    # Gear position tracking (simple direct mapping for EVs)
    self.shifter_values = {0: 0, 1: 1, 2: 2, 3: 3}

    # Button state tracking for edge detection
    self.button_states = defaultdict(int)

    # Driver assistance system states
    self.main_on_last = False
    self.lkas_enabled = False           # Lane keeping active status
    self.lane_enabled_status = False    # Lane keeping enabled (from button)
    self.acc_enabled = False            # ACC enabled status

    # Following distance control (1=closest, 4=farthest)
    self.gap_index = 2                  # Default medium following distance

    # Automatic model detection state
    self.detected_model = None          # Currently detected car model
    self.detection_confidence = 0       # Detection confidence counter
    self.last_model_marker = None       # Last seen modelMarker value
    self.gap_debounce_frames = 0        # Prevent button bounce

    # System capabilities
    self.has_camera = True              # Vision-based ADAS

    # MPC message counter for synchronization (0x318)
    self.eps_state_counter = 0    # 0x318 counter (for MPC happiness)

    # EPS ready status from stock 0x318 (EPS prepared to accept LKA commands)
    self.eps_lka_ready = False    # EPS ready flag from raw 0x318 byte

    # Simple counter tracking for message freshness (following other brands)
    self.msg_counters = defaultdict(int)  # Track last seen counter per message

    # Preserve latest stock MPC commands for unknown bit preservation
    self.stock_lat_cmd = None
    self.stock_long_cmd = None
    self.stock_mpc_state = None
    self.stock_eps_state = None


  def update(self, can_parsers) -> structs.CarState:
    cp = can_parsers[Bus.pt]
    cp_cam = can_parsers[Bus.cam]

    ret = structs.CarState()

    # Comprehensive validation for all critical messages (Honda/Toyota pattern)
    critical_messages = [
      "B_0x121_VCU_SpeedState_L8_20ms",      # Speed (CRITICAL)
      "B_0x11F_SAS_SensorState_L5_10ms",     # Steering angle (CRITICAL)
      "B_0x1FC_EPS_MotorState_L8_20ms",      # Driver torque (CRITICAL)
      "B_0x242_VCU_DriveState_L8_20ms",      # Pedals/gear (CRITICAL)
      "B_0x342_VCU_PedalState_L8_20ms",      # Brake pressure (CRITICAL)
      "B_0x32D_HUD_AdasState_L8_20ms",       # ACC state (CRITICAL)
      "B_0x3B0_VCU_ButtonState_L8_50ms",     # Buttons (HIGH)
      "B_0x1F0_VCU_ESP_VehSpeed_L8_20ms",    # Redundant speed (HIGH)
    ]

    validation_failed_count = 0
    for msg in critical_messages:
      if not validate_message(cp, msg):
        validation_failed_count += 1

      # Counter check for freshness (if message has counter)
      try:
        if not check_counter(self, msg, cp.vl[msg]):
          validation_failed_count += 1
      except (KeyError, TypeError):
        pass

    # Set fault if multiple critical messages fail validation
    if validation_failed_count > 2:  # Allow 2 occasional failures
      ret.carFaultedNonCritical = True

    # Update button debounce
    if self.gap_debounce_frames > 0:
      self.gap_debounce_frames -= 1

    # =========================================================================
    # Vehicle Dynamics (B_ messages - MUST READ)
    # =========================================================================

    # Speed validation with redundant sources (AND logic for safety)
    speed_main = cp.vl["B_0x121_VCU_SpeedState_L8_20ms"]["VCU_VehicleSpeed"]  # km/h (primary)
    speed_esp = cp.vl["B_0x1F0_VCU_ESP_VehSpeed_L8_20ms"]["ESP_VehicleSpeed"]     # km/h (redundant)

    # Speed mismatch detection (safety check for rollover/sensor failure)
    SPEED_MISMATCH_THRESHOLD = 5.0  # km/h tolerance
    speed_diff = abs(speed_main - speed_esp)
    speed_valid = speed_diff < SPEED_MISMATCH_THRESHOLD

    # Use primary speed if valid, otherwise disable
    if speed_valid:
      ret.vEgo = speed_main * CV.KPH_TO_MS  # Convert km/h to m/s
    else:
      # Speed mismatch - use conservative value and flag fault to disable control
      ret.vEgo = min(speed_main, speed_esp) * CV.KPH_TO_MS  # Use lower speed for safety
      ret.carFaultedNonCritical = True  # Flag fault to disable control
      ret.cruiseState.enabled = False   # Force cruise off for safety

    ret.vEgoRaw = ret.vEgo
    ret.standstill = ret.vEgo < 0.1  # Vehicle stopped threshold

    # Wheel speeds (NO individual wheel speeds in DBC - use main speed for all)
    ret.wheelSpeeds.fl = ret.vEgo
    ret.wheelSpeeds.fr = ret.vEgo
    ret.wheelSpeeds.rl = ret.vEgo
    ret.wheelSpeeds.rr = ret.vEgo

    # Steering from B_0x11F (SAS sensor - absolute position ±500°)
    ret.steeringAngleDeg = cp.vl["B_0x11F_SAS_SensorState_L5_10ms"]["SAS_SteeringAngle"]  # Steering wheel angle (±500° range)
    ret.steeringRateDeg = cp.vl["B_0x11F_SAS_SensorState_L5_10ms"]["SAS_SteeringAngleSpeed"]  # Already scaled to deg/s in DBC

    # Driver torque from B_0x1FC (EPS motor state)
    ret.steeringTorque = cp.vl["B_0x1FC_EPS_MotorState_L8_20ms"]["EPS_DriverTorque"]  # n.m
    ret.steeringPressed = abs(ret.steeringTorque) > STEER_THRESHOLD  # Driver override detection

    # =========================================================================
    # Driver Inputs (B_ messages - MUST READ)
    # =========================================================================

    # Gas pedal
    ret.gas = cp.vl["B_0x242_VCU_DriveState_L8_20ms"]["VCU_AccelPedal"] / 100.0  # Accelerator pedal (0.0-1.0)
    ret.gasPressed = ret.gas > 0.05  # 5% threshold for gas pressed

    # Brake validation with redundant sources (OR logic for safety)
    brake_switch_242 = bool(cp.vl["B_0x242_VCU_DriveState_L8_20ms"]["VCU_BrakePressed"])    # Switch
    brake_pedal_bar = cp.vl["B_0x342_VCU_PedalState_L8_20ms"]["VCU_BrakePedalPressure"]             # Pedal sensor (bar)

    BRAKE_PRESSURE_THRESHOLD = 0.1  # bar threshold for pressure sensors
    ret.brakePressed = brake_switch_242 or brake_pedal_bar > BRAKE_PRESSURE_THRESHOLD

    # Brake value derived from pedal pressure only (no hydraulic feedback available)
    ret.brake = min(brake_pedal_bar / 100.0, 1.0)  # Normalize to 0.0-1.0 (100 bar = full brake)

    # Gear and parking brake from B_0x242
    ret.gearShifter = GEAR_DICT.get(cp.vl["B_0x242_VCU_DriveState_L8_20ms"]["VCU_GearPosition"], GearShifter.unknown)
    ret.parkingBrake = bool(cp.vl["B_0x242_VCU_DriveState_L8_20ms"]["VCU_ParkingBrake"])  # Parking brake engaged

    # =========================================================================
    # Regenerative Braking Detection
    # =========================================================================

    # Regenerative braking: not directly observable without hydraulic data
    ret.regenBraking = False

    # =========================================================================
    # Powertrain State (NOT AVAILABLE in new DBC - use safe defaults)
    # =========================================================================

    # No powertrain fault signals in new DBC
    ret.steerFaultTemporary = False
    ret.steerFaultPermanent = False
    ret.accFaulted = False

    # No RPM/battery/fuel signals in new DBC
    ret.engineRPM = 0  # Not available
    ret.fuelGauge = 0.5  # Unknown - use 50% as safe default
    ret.charging = False  # Not available

    # Non-critical fault heuristic (no fault data available)
    ret.carFaultedNonCritical = False

    # =========================================================================
    # Cruise Control State (B_0x32D_HudState - MUST READ)
    # =========================================================================

    # ACC state from HUD message
    acc_state = cp.vl["B_0x32D_HUD_AdasState_L8_20ms"]["ACC_State"]  # 0-7
    # ACC states: 0=OFF, 1-7=various active states
    ret.cruiseState.enabled = bool(acc_state > 0)
    ret.cruiseState.available = True  # Always available if ACC hardware present

    # Set speed from HUD
    set_speed_kph = cp.vl["B_0x32D_HUD_AdasState_L8_20ms"]["VCU_SetSpeed"]  # km/h
    if set_speed_kph > 0:
      ret.cruiseState.speedCluster = set_speed_kph * CV.KPH_TO_MS  # Convert to m/s
      ret.cruiseState.speed = ret.cruiseState.speedCluster
    else:
      ret.cruiseState.speed = ret.vEgo  # Fallback to current speed

    ret.cruiseState.standstill = ret.standstill

    # =========================================================================
    # Lane Keeping State (infer from button presses - no explicit state in DBC)
    # =========================================================================

    # Lane keeping enabled status from button tracking
    self.lane_enabled_status = self.lkas_enabled  # Track from button events

    # =========================================================================
    # Doors / Cabin (B_0x294_CabinState - MUST READ)
    # =========================================================================

    ret.doorOpen = any([
      cp.vl["B_0x294_BCM_CabinState_L8_50ms"]["BCM_DoorFrontLeft"],  # Front left door
      cp.vl["B_0x294_BCM_CabinState_L8_50ms"]["BCM_DoorFrontRight"],  # Front right door
      cp.vl["B_0x294_BCM_CabinState_L8_50ms"]["BCM_DoorRearLeft"],  # Rear left door
      cp.vl["B_0x294_BCM_CabinState_L8_50ms"]["BCM_DoorRearRight"],  # Rear right door
    ])
    ret.seatbeltUnlatched = not cp.vl["B_0x294_BCM_CabinState_L8_50ms"]["BCM_DriverSeatbelt"]  # Driver seatbelt
    ret.genericToggle = True  # Vehicle ready to drive (no explicit signal, assume true if parsing)

    # =========================================================================
    # Blind Spot Detection (B_0x418_BsmState - MUST READ)
    # =========================================================================

    ret.blindSpotLeft = bool(cp.vl["B_0x418_VCU_BsdState_L8_50ms"]["VCU_BSDLeftApproach"]) or \
                        bool(cp.vl["B_0x418_VCU_BsdState_L8_50ms"]["VCU_BSDLeftApproach"])
    ret.blindSpotRight = bool(cp.vl["B_0x418_VCU_BsdState_L8_50ms"]["VCU_BSDRightApproach"])

    # Additional blindspot mapping for cereal compatibility
    ret.leftBlindspot = ret.blindSpotLeft
    ret.rightBlindspot = ret.blindSpotRight

    # =========================================================================
    # Lead Vehicle Detection (B_0x32D_HudState - MUST READ)
    # =========================================================================

    lead_vehicle = bool(cp.vl["B_0x32D_HUD_AdasState_L8_20ms"]["Lead_Vehicle"])
    lead_distance_index = cp.vl["B_0x32D_HUD_AdasState_L8_20ms"]["VCU_LeadDistance"]  # 0-7 index

    # Convert lead distance index to meters (rough estimate: 0=none, 1-7=increasing distance)
    lead_distance_m = lead_distance_index * 10.0 if lead_distance_index > 0 else 0.0

    ret.radarState.leadOne.status = lead_vehicle
    ret.radarState.leadOne.dRel = lead_distance_m if lead_vehicle else 0.0  # Distance ahead (m)
    ret.radarState.leadOne.yRel = 0.0  # No lateral offset available in DBC
    ret.radarState.leadOne.vRel = 0.0  # No relative velocity from camera
    ret.radarState.radarUnavailable = True  # Camera-based detection

    # =========================================================================
    # Buttons (B_0x3B0_ButtonState - MUST READ)
    # =========================================================================

    button_events = []

    # Cruise scroll button (up/down combined in one signal)
    cruise_scroll_direction = cp.vl["B_0x3B0_VCU_ButtonState_L8_50ms"]["SWC_CruiseScrollDirection"]

    # Scroll up button (direction = 3)
    scroll_up_pressed = (cruise_scroll_direction == 3)
    if scroll_up_pressed != self.button_states["btnCruiseScrollUp"]:
      button_events.extend(create_button_events(
        int(scroll_up_pressed), int(self.button_states["btnCruiseScrollUp"]), {1: ButtonType.accelCruise}
      ))
      self.button_states["btnCruiseScrollUp"] = scroll_up_pressed

    # Scroll down button (direction = 1)
    scroll_dn_pressed = (cruise_scroll_direction == 1)
    if scroll_dn_pressed != self.button_states["btnCruiseScrollDn"]:
      button_events.extend(create_button_events(
        int(scroll_dn_pressed), int(self.button_states["btnCruiseScrollDn"]), {1: ButtonType.decelCruise}
      ))
      self.button_states["btnCruiseScrollDn"] = scroll_dn_pressed

    # Other buttons from B_0x3B0
    button_signals = {
      "btnDistFar": ("SWC_DistanceSetFarButton", ButtonType.gapAdjustCruise),
      "btnDistNr": ("SWC_DistanceSetNearButton", ButtonType.gapAdjustCruise),
      "btnCruiseSet": ("SWC_CruiseSetButton", ButtonType.setCruise),
      "btnLKA": ("SWC_LCCButton", ButtonType.lkas),
      "btnAccOnOff": ("BTN_TOGGLE_ACC_OnOff", ButtonType.mainCruise),
    }

    for button_name, (signal_name, button_type) in button_signals.items():
      current_pressed = bool(cp.vl["B_0x3B0_VCU_ButtonState_L8_50ms"][signal_name])
      previous_pressed = self.button_states[button_name]

      if current_pressed != previous_pressed:
        button_events.extend(create_button_events(
          int(current_pressed), int(previous_pressed), {1: button_type}
        ))
        self.button_states[button_name] = current_pressed

        # Track LKA enabled state from button
        if button_name == "btnLKA" and current_pressed:
          self.lkas_enabled = not self.lkas_enabled  # Toggle LKA

        # Handle following distance adjustment (with debounce)
        if current_pressed and self.gap_debounce_frames == 0:
          if button_name == "btnDistFar":
            self.gap_index = min(self.gap_index + 1, 4)  # Increase distance (max 4)
            self.gap_debounce_frames = 5  # Prevent button bounce
          elif button_name == "btnDistNr":
            self.gap_index = max(self.gap_index - 1, 1)  # Decrease distance (min 1)
            self.gap_debounce_frames = 5  # Prevent button bounce

    ret.buttonEvents = button_events

    # Following distance setting (use tracked gap_index)
    follow_distance_index = cp.vl["B_0x32D_HUD_AdasState_L8_20ms"]["VCU_FollowDistance"]  # 1/2/4/8 bit pattern
    # Convert bit pattern to gap index (1=1bar, 2=2bar, 4=3bar, 8=4bar)
    if follow_distance_index in [1, 2, 4, 8]:
      gap_map = {1: 1, 2: 2, 4: 3, 8: 4}
      self.gap_index = gap_map.get(follow_distance_index, self.gap_index)

    ret.gapAdjustCruiseTr = int(self.gap_index)  # Current following distance setting

    # =========================================================================
    # Turn Signals (B_0x133_StalkState - MUST READ)
    # =========================================================================

    turn_signal_stalk = cp.vl["B_0x133_BCM_StalkState_L8_50ms"]["BCM_TurnSignalStalk"]
    left_blinker, right_blinker = TURN_SIGNAL_STATES.get(turn_signal_stalk, (False, False))

    # Hazard lights from B_0x294
    hazard = bool(cp.vl["B_0x294_BCM_CabinState_L8_50ms"]["BCM_HazardButton"])
    if hazard:
      left_blinker = True
      right_blinker = True

    ret.leftBlinker = left_blinker
    ret.rightBlinker = right_blinker
    ret.leftBlinkerOn = left_blinker   # Final left turn signal status
    ret.rightBlinkerOn = right_blinker  # Final right turn signal status

    # =========================================================================
    # Stock MPC Safety Features (B_0x32D_HudState - MUST READ)
    # =========================================================================

    ret.stockAeb = bool(cp.vl["B_0x32D_HUD_AdasState_L8_20ms"].get("VCU_AEBEnabled", 0))
    ret.stockFcw = bool(cp.vl["B_0x32D_HUD_AdasState_L8_20ms"].get("VCU_CloseDistanceWarning", 0))

    # ESP status (not available in DBC)
    ret.espDisabled = False  # Electronic Stability Program status unknown

    # =========================================================================
    # Traffic Sign Recognition - not available on this protocol revision
    # =========================================================================

    ret.speedLimit = 0.0

    # =========================================================================
    # Automatic Model Detection (from diagnostic messages)
    # =========================================================================

    detected = self.detect_car_model(cp.vl)
    if detected is not None:
      if detected == self.detected_model:
        self.detection_confidence = min(self.detection_confidence + 1, 100)
      else:
        self.detected_model = detected
        self.detection_confidence = 1
        print(f"Gateway: Auto-detected car model {detected.name} via diagnostics (confidence: {self.detection_confidence})")

    # =========================================================================
    # EPS State Counter Sync (from camera parser - B_0x318)
    # =========================================================================

    # Extract 0x318 counter and EPS ready signal for MPC synchronization
    try:
      eps_vals = cp_cam.vl[self.msgs.eps_state]
      self.stock_eps_state = dict(eps_vals)
      self.eps_state_counter = int(eps_vals.get("EPS_RollingCounter_0D5", self.eps_state_counter))

      raw_eps = cp_cam.to_can_parser.msgs[self.msgs.eps_state]["data"]
      if raw_eps and len(raw_eps) >= 1:
        self.eps_lka_ready = bool(raw_eps[0] & 0x01)
    except KeyError:
      pass

    # Preserve latest stock commands (camera bus) to reuse unknown bits when transmitting
    try:
      self.stock_lat_cmd = copy.copy(cp_cam.vl[self.msgs.lat_cmd])
    except KeyError:
      pass

    try:
      self.stock_long_cmd = copy.copy(cp_cam.vl[self.msgs.long_cmd])
    except KeyError:
      pass

    try:
      self.stock_mpc_state = copy.copy(cp_cam.vl[self.msgs.mpc_state])
    except KeyError:
      pass

    # Finalize
    self.out = ret.as_reader()
    return ret

  @staticmethod
  def get_can_parsers(CP):
    msgs = get_model_messages(CP.carFingerprint)
    # ==========================================================================
    # CAN BUS MESSAGE PARSERS
    # ==========================================================================
    # Bus 0 (pt): Parse B_ messages - vehicle state FROM vehicle TO gateway
    # Bus 2 (cam): Parse stock A_ messages - preserve unknown bits for TX
    #              Parse B_0x318 - EPS state counter synchronization
    # ==========================================================================

    # Bus 0: Vehicle state messages (B_ prefix - all from vehicle ECUs)
    pt_messages = [
      # Vehicle dynamics (B_ messages - MUST READ)
      ("B_0x121_VCU_SpeedState_L8_20ms", 50),         # Primary speed (20Hz actual, 50Hz tolerance)
      ("B_0x1F0_VCU_ESP_VehSpeed_L8_20ms", 50),       # ESP speed (redundant for validation)
      ("B_0x11F_SAS_SensorState_L5_10ms", 100),       # Steering wheel angle + rate (100Hz)
      ("B_0x1FC_EPS_MotorState_L8_20ms", 50),         # Driver steering torque (20Hz actual, 50Hz tolerance)

      # Driver inputs (B_ messages - MUST READ)
      ("B_0x242_VCU_DriveState_L8_20ms", 50),         # Gas/brake/gear/parking brake (20Hz actual)
      ("B_0x342_VCU_PedalState_L8_20ms", 50),         # Brake pedal + drive mode (20Hz actual)

      # Buttons and stalks (B_ messages - MUST READ)
      ("B_0x3B0_VCU_ButtonState_L8_50ms", 50),        # Cruise/LKA buttons (50Hz)
      ("B_0x133_BCM_StalkState_L8_50ms", 50),         # Turn signal stalk (50Hz)

      # Cabin state (B_ messages - MUST READ)
      ("B_0x294_BCM_CabinState_L8_50ms", 50),         # Doors + seatbelt + hazard (50Hz)

      # ADAS state (B_ messages - MUST READ)
      ("B_0x32D_HUD_AdasState_L8_20ms", 50),          # ACC/HUD state + lead vehicle + AEB/FCW (20Hz actual)
      ("B_0x418_VCU_BsdState_L8_50ms", 50),           # Blind spot detection (50Hz)

      # Diagnostic messages (optional - for model detection)
      ("A_0x6F0_M2V_DIAG_ControlsState_L8_50ms", 50), # Diagnostics (50Hz)
    ]

    # Bus 2: Stock MPC messages (cam bus) - preserve unknown bits & sync counters
    # We parse these to copy their unknown signal values into our own A_ messages
    # This ensures we preserve bits we don't understand when sending commands
    cam_messages = [
      (msgs.eps_state, 50),   # Stock EPS state (counter sync)
      (msgs.lat_cmd, 50),     # Stock lateral cmd (preserve bits)
      (msgs.long_cmd, 50),    # Stock long cmd (preserve bits)
      (msgs.mpc_state, 50),   # Stock MPC state (preserve bits)
    ]

    # Return structure consumed by CarInterfaceBase
    return {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], pt_messages, Bus.pt),
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], cam_messages, Bus.cam),
    }
