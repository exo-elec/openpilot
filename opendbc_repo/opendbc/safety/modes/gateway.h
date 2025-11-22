// ============================================================================
// Gateway BYD Dolphin Safety Model
// ============================================================================
//
// PURPOSE: Firmware-level safety enforcement for BYD Dolphin lateral control
// BASED ON: sunnypilot-pc BYD safety with Dolphin-specific adaptations
// CREATED: 2025-10-05
//
// CRITICAL: This is the LAST LINE OF DEFENSE. Python code can have bugs,
//           but this firmware layer MUST be bulletproof.
//
// SAFETY CHECKS ENFORCED:
//   1. Torque rate limiting (prevent sudden jerks)
//   2. Absolute torque limiting (prevent EPS overload)
//   3. Vehicle state validation (gas/brake/cruise/EPS ready)
//   4. Driver override detection
//   5. Message forwarding control (prevent loops)
//
// ============================================================================

#pragma once

#include <math.h>

#include "opendbc/safety/safety_declarations.h"

// ============================================================================
// [Section 1] CAN Protocol Definitions
// ============================================================================

// ----------------------------------------------------------------------------
// CAN Message Addresses (BYD Dolphin Specific)
// ----------------------------------------------------------------------------
// ONLY A_ (our commands) and B_ (PT bus) messages - NO C_ (camera bus)!
#define GATEWAY_CANADDR_LATERAL        0x1E2  // A_ Our lateral control command
#define GATEWAY_CANADDR_MPC_STATE      0x316  // A_ MPC state (for display)
#define GATEWAY_CANADDR_LONG_CMD       0x32E  // A_ Longitudinal command
#define GATEWAY_CANADDR_ACC_STATE      0x32D  // B_ ACC/ADAS state (VCU_ACCReady)
#define GATEWAY_CANADDR_SPEED          0x121  // B_ Vehicle speed
#define GATEWAY_CANADDR_ESP_STATE      0x1F0  // B_ ESP/brake state
#define GATEWAY_CANADDR_PEDAL          0x342  // B_ Gas/brake pedals
#define GATEWAY_CANADDR_DRIVE_STATE    0x242  // B_ Drive state (gear, etc.)
#define GATEWAY_CANADDR_DRIVER_TORQUE  0x1FC  // B_ Driver steering torque + EPS assist

// ----------------------------------------------------------------------------
// CAN Bus Layout (Dolphin 3-bus architecture)
// ----------------------------------------------------------------------------
#define GATEWAY_CANBUS_PT   0  // Powertrain/ESC bus (ADAS bus - physical CAN 1)
#define GATEWAY_CANBUS_RES  1  // Reserved (unused)
#define GATEWAY_CANBUS_MPC  2  // MPC camera bus (physical CAN 3)

// ============================================================================
// [Section 2] Safety State Variables
// ============================================================================

static bool gateway_acc_ready = false;          // ACC system ready (from B_0x32D)
static bool gateway_acc_enabled = false;        // ACC actively engaged (from B_0x32D)

// Steer request tolerance (fault avoidance logic like Toyota/Hyundai)
static uint8_t gateway_valid_steer_req_count = 0;        // Consecutive valid steer_req frames
static uint8_t gateway_invalid_steer_req_count = 0;      // Consecutive invalid steer_req frames
static bool gateway_steer_req_latched = false;           // Latched steer request state

// ============================================================================
// [Section 2.5] Checksum & Counter Validation
// ============================================================================
//
// BYD uses "inverted sum" checksums: checksum = ~(sum of first 7 bytes)
// Counters are 4-bit (0-15) and must increment by 1 each message
//
// MESSAGE LAYOUT:
//   Most messages:  Counter @ bits 48-51 (byte 6 lower nibble), Checksum @ byte 7
//   0x1E2 lateral:  Counter @ bits 52-55 (byte 6 upper nibble), Checksum @ byte 7
//   0x1FC torque:   Counter @ bits 52-55 (byte 6 upper nibble), Checksum @ byte 7

static uint32_t gateway_compute_checksum(const CANPacket_t *msg) {
  // Inverted sum of first 7 bytes (bytes 0-6)
  uint8_t checksum = 0;
  for (int i = 0; i < 7; i++) {
    checksum += msg->data[i];
  }
  return (uint8_t)(~checksum);  // Bitwise inversion
}

static uint32_t gateway_get_checksum(const CANPacket_t *msg) {
  // Checksum is always in byte 7 (bits 56-63)
  return msg->data[7];
}

static uint32_t gateway_get_counter(const CANPacket_t *msg) {
  // Counter location depends on message:
  // - Most messages: bits 48-51 (byte 6, lower nibble)
  // - 0x1E2 and 0x1FC: bits 52-55 (byte 6, upper nibble)
  int addr = msg->addr;

  if (addr == GATEWAY_CANADDR_LATERAL || addr == GATEWAY_CANADDR_DRIVER_TORQUE) {
    // Counter at bits 52-55 (byte 6 upper nibble)
    return (msg->data[6] >> 4) & 0x0FU;
  } else {
    // Counter at bits 48-51 (byte 6 lower nibble)
    return msg->data[6] & 0x0FU;
  }
}

static void gateway_handle_acc_state(const CANPacket_t *msg) {
  // B_0x32D_HUD_AdasState: VCU_ACCReady @ bit 15, VCU_ACCStateEnabled @ bits 18-20
  gateway_acc_ready = GET_BIT(msg, 15U);

  // ACC enabled if state bits are non-zero (0=off, 1-7=various engaged states)
  int acc_state = GET_BYTE(msg, 2) & 0x1C;  // Extract bits 18-20 (byte 2, bits 2-4)
  gateway_acc_enabled = (acc_state != 0);
}

static void gateway_handle_driver_torque(const CANPacket_t *msg) {
  // B_0x1FC_EPS_EpsTorque: EPS_DriverTorque @ bytes 0-1, EPS_TorqueAssist @ byte 2
  int torque_driver_new = (int16_t)GET_BYTES(msg, 0, 2);
  update_sample(&torque_driver, torque_driver_new);

  // Also track motor assist for error checking
  int torque_assist = (int8_t)GET_BYTE(msg, 2);
  int torque_motor = torque_assist * 258;  // Scale to match command units
  update_sample(&torque_meas, torque_motor);
}

// ============================================================================
// [Section 3] Safety Limits Configuration
// ============================================================================

// Dolphin Steering Limits
// CRITICAL: These MUST match Python software limits in values.py exactly!
//
// ALL VALUES DERIVED FROM BLF ANALYSIS (6 logs, 3801 samples)
// Source: comprehensive_limits_analysis.py on 8th_LOG dataset (2025-10-15)
//
// Units: Direct motor counts (NOT Nm!) with DBC scale=1.0
//
// BLF Data Summary (all values from stock BYD system in real driving):
//   max_torque:       Observed 8981 max, 7701 p99, 1947 mean
//   max_rate:         Observed 1008 max, 256 p99, 50 mean
//   max_error:        Observed 8980 max, 7700 p99, 1947 mean
//   max_rt_delta:     Observed 3826 max (250ms window)
//
// UPDATED 2025-10-16: All limits increased to match stock BYD performance
//   - OLD limits (300/18/25/80/243) were 30-170x too low!
//   - NEW limits based on max observed + safety margins (25-50%)
//   - Margins accommodate transients and emergency maneuvers
//   - Firmware is LAST LINE OF DEFENSE - must allow normal operation
//
const TorqueSteeringLimits GATEWAY_DOLPHIN_STEERING_LIMITS = {
  .max_torque = 11500,              // BLF max 8981 + 28% margin (allows user's "100% effort")
  .max_rate_up = 700,               // BLF max 1008, but p99=256 → conservative 700
  .max_rate_down = 1400,            // BLF max 1008 + 39% (faster down for safety)
  .max_torque_error = 13500,        // BLF max 8980 + 50% (EPS lag tolerance)
  .max_rt_delta = 5000,             // BLF max 3826 + 31% (real-time delta)
  .type = TorqueMotorLimited,       // Motor torque control (Dolphin has no torque feedback)

  // Steer request tolerance (fault avoidance - like Hyundai)
  .min_valid_request_frames = 89,            // Need 89 consecutive valid frames before latching steer_req
  .max_invalid_request_frames = 2,           // Allow 2 invalid frames before un-latching
  .min_valid_request_rt_interval = 810000,   // 810ms real-time interval for valid requests
  .has_steer_req_tolerance = true,           // Enable fault avoidance logic
};

// ============================================================================
// [Section 4] RX Hook - Monitor Incoming CAN Messages
// ============================================================================
//
// PURPOSE: Parse incoming CAN messages to track vehicle state
// MONITORS: Gas/brake pedals, vehicle speed, EPS state, driver torque
// UPDATES: Global safety state variables
//
static void gateway_rx_hook(const CANPacket_t *msg) {
  int bus = msg->bus;
  int addr = msg->addr;

  // ONLY process messages from PT bus (bus 0) - NO camera bus messages!
  if (bus == GATEWAY_CANBUS_PT) {
    if (addr == GATEWAY_CANADDR_PEDAL) {
      gas_pressed = (msg->data[0] != 0U);
      brake_pressed = (msg->data[1] != 0U);
    } else if (addr == GATEWAY_CANADDR_SPEED) {
      int speed_raw = (((msg->data[1] & 0x0FU) << 8) | msg->data[0]);
      vehicle_moving = (speed_raw != 0);
    } else if (addr == GATEWAY_CANADDR_ACC_STATE) {
      gateway_handle_acc_state(msg);
    } else if (addr == GATEWAY_CANADDR_DRIVER_TORQUE) {
      gateway_handle_driver_torque(msg);
    }
  }
}

// ============================================================================
// [Section 5] TX Hook - Validate Outgoing CAN Messages
// ============================================================================
//
// PURPOSE: Validate every outgoing control message before sending to CAN bus
// VALIDATES: Torque limits, rate limits, vehicle state conditions
// BLOCKS: Any unsafe command
// RETURNS: true = allow transmission, false = block transmission
//
static bool gateway_tx_hook(const CANPacket_t *msg) {
  bool tx = true;  // Default: allow transmission
  int bus = msg->bus;
  int addr = msg->addr;

  // --------------------------------------------------------------------------
  // Validate Lateral Control Messages on Powertrain Bus
  // --------------------------------------------------------------------------
  if (bus == GATEWAY_CANBUS_PT) {

    // --- Validate Lateral Control Command (0x1E2) ---
    if (addr == GATEWAY_CANADDR_LATERAL) {

      // CRITICAL: Extract torque/angle command
      //
      // ARCHITECTURE (2025-10-18):
      // - Openpilot: Generic angle calculation (unlimited range)
      // - gatewaycan.py: Vehicle-specific 8-bit wrapping to ±12.7°
      // - Firmware: Validation only (wrapping already done)
      //
      // WHY WRAP TO ±12.7°?
      // - EPS_SteeringAngle (0x1FC) feedback is 8-bit signed, scale 0.1
      // - EPS can only report ±12.7° (127 × 0.1 = 12.7°)
      // - Python wraps command to match EPS feedback behavior
      // - Otherwise: command 400°, EPS reports 10°, control loop confused!
      //
      // WRAPPING MECHANISM (in gatewaycan.py):
      // - Simulate 8-bit signed overflow in degree domain (0.1° resolution)
      // - Steps: degrees → 0.1° counts → int8_t wrap → degrees
      // - Example: 400° → 4000 counts → -96 counts → -9.6°
      // - Wrapping delta: 256 × 0.1 = 25.6° (confirmed by BLF analysis)
      //
      // DBC Signal Definition:
      //   MPC_SteeringAngleCmd: 16-bit signed, scale 0.000387596899
      //   This 16-bit signal ENCODES the ±12.7° wrapped range
      //
      // By the time we see the message here, angle is already wrapped!
      int desired_torque = (int16_t)GET_BYTES(msg, 2, 2);

      // Extract LKA_Active flag
      // LKA_Active: bit 28 (4th byte, bit 4)
      bool steer_req = GET_BIT(msg, 28U);

      // SAFETY CHECK 1: Vehicle state conditions
      // Only allow steering if:
      //   1. ACC system is ready (B_0x32D VCU_ACCReady)
      //   2. ACC is actively engaged (B_0x32D VCU_ACCStateEnabled)
      bool safety_conditions = gateway_acc_ready && gateway_acc_enabled;

      // SAFETY CHECK 1.5: Steer request tolerance (fault avoidance like Hyundai)
      // This prevents EPS faults from brief steer_req=0 periods during high steering rates
      //
      // Logic:
      // - Track consecutive valid (steer_req=1 + safety_conditions) frames
      // - Latch steer_req=1 only after min_valid_request_frames (89 frames = 1.78s)
      // - Allow brief invalid periods (max_invalid_request_frames = 2 frames)
      // - This smooths engagement and prevents EPS fault conditions
      bool steer_req_with_conditions = steer_req && safety_conditions;

      if (steer_req_with_conditions) {
        // Valid request - increment valid counter, reset invalid counter
        if (gateway_valid_steer_req_count < 255U) {
          gateway_valid_steer_req_count++;
        }
        gateway_invalid_steer_req_count = 0;

        // Latch steer_req after enough consecutive valid frames
        if (gateway_valid_steer_req_count >= GATEWAY_DOLPHIN_STEERING_LIMITS.min_valid_request_frames) {
          gateway_steer_req_latched = true;
        }
      } else {
        // Invalid request - increment invalid counter, reset valid counter
        if (gateway_invalid_steer_req_count < 255U) {
          gateway_invalid_steer_req_count++;
        }
        gateway_valid_steer_req_count = 0;

        // Un-latch if too many consecutive invalid frames
        if (gateway_invalid_steer_req_count > GATEWAY_DOLPHIN_STEERING_LIMITS.max_invalid_request_frames) {
          gateway_steer_req_latched = false;
        }
      }

      // Use latched steer_req for torque checks (prevents brief dropouts from causing faults)
      bool steer_req_final = gateway_steer_req_latched;

      // SAFETY CHECK 2: Apply comprehensive torque safety checks
      // This function checks:
      //   - Absolute torque limit (±11500 counts)
      //   - Rate limit (±700 up, ±1400 down per frame)
      //   - Torque error vs measured (±13500 counts)
      //   - Real-time delta limit (±5000 counts)
      //   - Gas/brake interlock
      //
      if (steer_torque_cmd_checks(desired_torque, steer_req_final,
                                   GATEWAY_DOLPHIN_STEERING_LIMITS)) {
        tx = false;  // BLOCK unsafe torque command
      }
    }

    // --- Longitudinal Command (0x32E) - Future Use ---
    else if (addr == GATEWAY_CANADDR_LONG_CMD) {
      // Longitudinal safety checks would go here
      // For now, allow (Dolphin may not use this)
    }
  }

  return tx;
}

// ============================================================================
// [Section 6] Forward Hook - Prevent Message Loops
// ============================================================================
//
// PURPOSE: Block messages from being forwarded between buses
// PREVENTS: Control message loops, echo conflicts
// RETURNS: true = block forwarding, false = allow forwarding
//
static bool gateway_fwd_hook(int bus, int addr) {
  bool block_msg = false;

  // Define message types (only A_ messages we send)
  const bool is_lat_control = (addr == GATEWAY_CANADDR_LATERAL);
  const bool is_mpc_state = (addr == GATEWAY_CANADDR_MPC_STATE);
  const bool is_long_cmd = (addr == GATEWAY_CANADDR_LONG_CMD);

  // --------------------------------------------------------------------------
  // Block openpilot control messages from being forwarded
  // --------------------------------------------------------------------------
  if (bus == GATEWAY_CANBUS_PT) {
    if (is_lat_control || is_mpc_state || is_long_cmd) {
      block_msg = true;  // Don't forward our commands back
    }
  }

  return block_msg;
}

// ============================================================================
// [Section 7] Safety Initialization
// ============================================================================
//
// PURPOSE: Configure safety model parameters
// DEFINES: Allowed TX messages and required RX messages
// RETURNS: Safety configuration structure
//
static safety_config gateway_init(uint16_t param) {
  UNUSED(param);

  // --------------------------------------------------------------------------
  // TX Messages We're Allowed to Send (ONLY A_ messages - our commands)
  // --------------------------------------------------------------------------
  static const CanMsg GATEWAY_TX_MSGS[] = {
    // Message ID, Bus, Len, check_relay, disable_static_blocking
    {GATEWAY_CANADDR_LATERAL,    GATEWAY_CANBUS_PT,   8,   .check_relay = true,  .disable_static_blocking = false},   // A_ Lateral control (WITH safety checks)
    {GATEWAY_CANADDR_MPC_STATE,  GATEWAY_CANBUS_PT,   8,   .check_relay = false, .disable_static_blocking = false},  // A_ MPC state (display only)
    {GATEWAY_CANADDR_LONG_CMD,   GATEWAY_CANBUS_PT,   8,   .check_relay = false, .disable_static_blocking = false},  // A_ Long control (if used)
    // Note: Diagnosis messages 0x6F0-0x6F8 can be added here if needed
  };

  // --------------------------------------------------------------------------
  // RX Messages We Must Monitor for Safety (ONLY B_ messages - PT bus)
  // --------------------------------------------------------------------------
  // CHECKSUM/COUNTER VALIDATION ENABLED for messages that have them:
  //   - 0x32D: Has counter + checksum
  //   - 0x121: Has counter + checksum
  //   - 0x1F0: Has checksum ONLY (no counter)
  //   - 0x342: Has checksum ONLY (no counter)
  //   - 0x242: Has checksum ONLY (no counter)
  //   - 0x1FC: Has counter + checksum
  static RxCheck gateway_rx_checks[] = {
    // Message ID,                  Bus,                    Len, Checksum, Counter, Frequency
    {.msg = {{GATEWAY_CANADDR_ACC_STATE,    GATEWAY_CANBUS_PT, 8, .check_checksum = true, .max_counter = 15U, .frequency = 50U}, { 0 }, { 0 }}},   // B_0x32D ACC state (has counter)
    {.msg = {{GATEWAY_CANADDR_SPEED,        GATEWAY_CANBUS_PT, 8, .check_checksum = true, .max_counter = 15U, .frequency = 50U}, { 0 }, { 0 }}},   // B_0x121 Speed (has counter)
    {.msg = {{GATEWAY_CANADDR_ESP_STATE,    GATEWAY_CANBUS_PT, 8, .check_checksum = true, .max_counter = 0U,  .frequency = 50U}, { 0 }, { 0 }}},   // B_0x1F0 ESP (no counter)
    {.msg = {{GATEWAY_CANADDR_PEDAL,        GATEWAY_CANBUS_PT, 8, .check_checksum = true, .max_counter = 0U,  .frequency = 50U}, { 0 }, { 0 }}},   // B_0x342 Pedals (no counter)
    {.msg = {{GATEWAY_CANADDR_DRIVE_STATE,  GATEWAY_CANBUS_PT, 8, .check_checksum = true, .max_counter = 0U,  .frequency = 50U}, { 0 }, { 0 }}},   // B_0x242 Drive state (no counter)
    {.msg = {{GATEWAY_CANADDR_DRIVER_TORQUE, GATEWAY_CANBUS_PT, 8, .check_checksum = true, .max_counter = 15U, .frequency = 50U}, { 0 }, { 0 }}},  // B_0x1FC Driver torque (has counter)
  };

  // Build and return safety configuration
  safety_config ret = BUILD_SAFETY_CFG(gateway_rx_checks, GATEWAY_TX_MSGS);

  // Register checksum and counter validation functions
  ret.get_checksum = gateway_get_checksum;
  ret.compute_checksum = gateway_compute_checksum;
  ret.get_counter = gateway_get_counter;

  return ret;
}

// ============================================================================
// [Section 8] Safety Hooks Export
// ============================================================================
//
// STRUCTURE: Exports all safety hook functions to the panda firmware
//
const safety_hooks gateway_hooks = {
  .init = gateway_init,      // Initialization function
  .rx = gateway_rx_hook,      // Receive hook (monitor incoming messages)
  .tx = gateway_tx_hook,      // Transmit hook (validate outgoing messages)
  .fwd = gateway_fwd_hook,    // Forward hook (prevent message loops)
};

// ============================================================================
// END OF GATEWAY SAFETY MODEL
// ============================================================================
