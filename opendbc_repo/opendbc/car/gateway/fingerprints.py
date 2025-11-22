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
    # Derived from 2025-11-22 BYD Atto 3 BLF captures (classic CAN @ 500 kbps)
    85: 8,   # 0x055  - Steering control heartbeat
    140: 8,  # 0x08C  - EPS status
    213: 8,  # 0x0D5  - Brake booster state
    287: 5,  # 0x11F  - Steering angle sensor
    289: 8,  # 0x121  - Vehicle speed (VCU)
    290: 8,  # 0x122  - Wheel speeds (front)
    291: 8,  # 0x123  - Wheel speeds (rear)
    300: 8,  # 0x12C  - Motor torque limits
    301: 8,  # 0x12D  - Motor temps
    307: 8,  # 0x133  - Stalk state
    309: 8,  # 0x135  - Turn signal lamps
    324: 8,  # 0x144  - Lighting
    327: 8,  # 0x147  - Seatbelt status
    330: 8,  # 0x14A  - Body domain status
    337: 8,  # 0x151  - Airbag state
    356: 8,  # 0x164
    371: 8,  # 0x173
    418: 8,  # 0x1A2
    450: 8,  # 0x1C2
    482: 8,  # 0x1E2  - Stock lateral command
    496: 8,  # 0x1F0  - ESP vehicle speed
    508: 8,  # 0x1FC  - EPS motor state
    511: 8,  # 0x1FF
    522: 8,  # 0x20A
    536: 8,  # 0x218
    537: 8,  # 0x219
    544: 8,  # 0x220
    546: 8,  # 0x222
    547: 8,  # 0x223
    576: 8,  # 0x240
    577: 8,  # 0x241
    578: 8,  # 0x242  - Drive state (pedals/gear)
    588: 8,  # 0x24C
    629: 8,  # 0x275
    638: 8,  # 0x27E
    639: 8,  # 0x27F
    660: 8,  # 0x294  - Cabin state / hazards
    692: 8,  # 0x2B4
    694: 8,  # 0x2B6
    724: 8,  # 0x2D4
    748: 8,  # 0x2EC
    786: 8,  # 0x312
    790: 8,  # 0x316  - Stock MPC state
    792: 8,  # 0x318  - EPS state to MPC
    797: 8,  # 0x31D
    798: 8,  # 0x31E
    800: 8,  # 0x320
    801: 8,  # 0x321  - Brake pressure
    802: 8,  # 0x322
    803: 8,  # 0x323
    812: 8,  # 0x32C
    813: 8,  # 0x32D  - HUD / ACC state
    814: 8,  # 0x32E  - Stock longitudinal command
    815: 8,  # 0x32F
    831: 8,  # 0x33F
    833: 8,  # 0x341
    834: 8,  # 0x342  - Pedal pressures
    835: 8,  # 0x343
    836: 8,  # 0x344
    843: 8,  # 0x34B
    847: 8,  # 0x34F
    848: 8,  # 0x350
    854: 8,  # 0x356
    860: 8,  # 0x35C
    863: 8,  # 0x35F
    879: 8,  # 0x36F
    884: 8,  # 0x374
    906: 8,  # 0x38A
    944: 8,  # 0x3B0  - Button state
    951: 8,  # 0x3B7
    965: 8,  # 0x3C5
    973: 8,  # 0x3CD
    985: 8,  # 0x3D9
    1004: 8, # 0x3EC
    1023: 8, # 0x3FF
    1028: 8, # 0x404
    1031: 8, # 0x407
    1037: 8, # 0x40D
    1040: 8, # 0x410
    1048: 8, # 0x418  - Blind spot / BSM
    1052: 8, # 0x41C
    1058: 8, # 0x422
    1062: 8, # 0x426
    1074: 8, # 0x432
    1076: 8, # 0x434
    1098: 8, # 0x44A
    1107: 8, # 0x453
    1141: 8, # 0x475
    1168: 8, # 0x490
    1178: 8, # 0x49A
    1184: 8, # 0x4A0
    1189: 8, # 0x4A5
    1193: 8, # 0x4A9
    1211: 8, # 0x4BB
    1215: 8, # 0x4BF
    1246: 8, # 0x4DE
    1274: 8, # 0x4FA
    1278: 8, # 0x4FE
    1297: 8, # 0x511
    1298: 8, # 0x512
    1319: 8, # 0x527
    1322: 8, # 0x52A
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
