"""
Gateway fingerprints
====================

Collects CAN fingerprints and firmware byte-strings for each Gateway model.
Keeping everything in one file helps during port bring-up and avoids hunting
through multiple modules for baseline IDs.
"""

from opendbc.car.structs import CarParams
from opendbc.car.gateway.values import CAR

Ecu = CarParams.Ecu

FINGERPRINTS = {
  CAR.BYD_ATTO3: [{
    # Placeholder fingerprint cloned from BYD Dolphin until dedicated logs are captured.
    213: 8, 287: 5, 289: 8, 307: 8, 496: 8, 508: 8, 578: 8, 660: 8, 792: 8, 813: 8, 834: 8, 944: 8, 1048: 8,
    482: 8, 790: 8, 814: 8,
    1776: 8, 1777: 8, 1778: 8, 1779: 8, 1780: 8, 1781: 8, 1782: 8, 1783: 8,
  }],
  CAR.BYD_DOLPHIN: [{
    # =========================================================================
    # Vehicle Fingerprint - Messages Detected During Car Identification
    # =========================================================================
    # During fingerprinting, we detect STOCK messages on the bus to identify
    # the car model. This includes both A_ (stock MPC commands) and B_ (vehicle
    # state) messages.
    # =========================================================================

    # B_ messages - Vehicle state from ECUs (11 messages)
    213: 8,   # B_0x0D5 (brake booster state)
    287: 5,   # B_0x11F_SAS_SensorState (steering angle sensor - continuous ±500°)
    289: 8,   # B_0x121_VCU_SpeedState (vehicle speed)
    307: 8,   # B_0x133_BCM_StalkState (turn signal stalk)
    496: 8,   # B_0x1F0_VCU_ESP_VehSpeed (ESP speed - redundant validation)
    508: 8,   # B_0x1FC_EPS_MotorState (driver torque + EPS angle feedback)
    578: 8,   # B_0x242_VCU_DriveState (gas/brake/gear/parking)
    660: 8,   # B_0x294_BCM_CabinState (doors + seatbelt + hazard)
    792: 8,   # B_0x318_E2X_EpsState (EPS state - sent to stock MPC)
    813: 8,   # B_0x32D_HUD_AdasState (ACC/HUD + lead + AEB/FCW)
    834: 8,   # B_0x342_VCU_PedalState (brake pedal + drive mode)
    944: 8,   # B_0x3B0_VCU_ButtonState (cruise/LKA buttons)
    1048: 8,  # B_0x418_VCU_BsdState (blind spot detection)

    # A_ messages - Stock MPC commands (detected from original camera) (3 messages)
    482: 8,   # A_0x1E2_M2E_Lateral_Cmd (stock MPC lateral command)
    790: 8,   # A_0x316_M2X_MpcState (stock MPC state)
    814: 8,   # A_0x32E_M2V_Long_Cmd (stock MPC longitudinal command)

    # A_ diagnostic messages - Sent by gateway for model detection (8 messages)
    1776: 8,  # A_0x6F0_M2V_DIAG_ControlsState
    1777: 8,  # A_0x6F1_M2V_DIAG_LateralState
    1778: 8,  # A_0x6F2_M2V_DIAG_LongitudinalState
    1779: 8,  # A_0x6F3_M2V_DIAG_DtsaParams
    1780: 8,  # A_0x6F4_M2V_DIAG_CarStateMirror
    1781: 8,  # A_0x6F5_M2V_DIAG_LiveParameters
    1782: 8,  # A_0x6F6_M2V_DIAG_ModelOutputs
    1783: 8,  # A_0x6F7_M2V_DIAG_SystemHealth
  }],
  # CAR.DEEPAL_S05: [{
  #   # Core B_ messages (informative reads) - 8 bytes each
  #   213: 8, 287: 5, 289: 8, 307: 8, 508: 8, 578: 8, 660: 8, 813: 8, 792: 8, 834: 8, 944: 8, 1048: 8,
  #   # Core C_ messages (additional info) - 8 bytes each
  #   546: 8, 547: 8, 801: 8,
  #   # A_ diagnostic messages (brand detection - CHANGAN)
  #   1776: 8, 1777: 8, 1778: 8, 1779: 8
  # }],
}

FW_VERSIONS = {
  CAR.BYD_ATTO3: {
    (Ecu.fwdCamera, 0x750, 0x6d): [
      b'BYD_ATTO3_CAM_V1.0\x00\x00',
    ],
    (Ecu.engine, 0x7e0, None): [
      b'BYD_ATTO3_ECU_V1.0\x00\x00\x00\x00\x00\x00\x00\x00',
    ],
  },
  CAR.BYD_DOLPHIN: {
    (Ecu.fwdCamera, 0x750, 0x6d): [
      b'BYD_DOLPHIN_CAM_V1.0\x00',
    ],
    (Ecu.engine, 0x7e0, None): [
      b'BYD_DOLPHIN_ECU_V1.0\x00\x00\x00\x00\x00\x00\x00',
    ],
  },
  # CAR.DEEPAL_S05: {
  #   (Ecu.fwdCamera, 0x750, 0x6d): [
  #     b'DEEPAL_S05_CAM_V1.0\x00\x00',
  #   ],
  #   (Ecu.engine, 0x7e0, None): [
  #     b'DEEPAL_S05_ECU_V1.0\x00\x00\x00\x00\x00\x00\x00',
  #   ],
  # },
}
