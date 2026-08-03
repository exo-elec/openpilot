#pragma once

#include "opendbc/safety/safety_declarations.h"

// Addresses named after the tc275_freertos/BYD_Atto3 CANape convention
// (A_0xNNN_..., B_0xNNN_...), not the shemps/byd-atto3-openpilot-port fork.
#define BYD_MPC_LATERAL_CMD 0x1E2U  // A_0x1E2_MPC_Lateral_Cmd_L8_20ms
#define BYD_MPC_STATE 0x316U        // A_0x316_MPC_MpcState_L8_20ms
#define BYD_SAS_SENSOR_STATE 0x11FU // B_0x11F_SAS_SensorState_L5_10ms
#define BYD_ESP_VEH_SPEED 0x1F0U    // B_0x1F0_VCU_ESP_VehSpeed_L8_20ms
#define BYD_VCU_DRIVE_STATE 0x242U  // B_0x242_VCU_DriveState_L8_20ms
#define BYD_HUD_ADAS_STATE 0x32DU   // B_0x32D_HUD_AdasState_L8_20ms
#define BYD_VCU_PEDAL_STATE 0x342U  // B_0x342_VCU_PedalState_L8_20ms

static bool byd_checksum_valid(const CANPacket_t *msg) {
  uint8_t checksum = 0U;
  for (int i = 0; i < 7; i++) {
    checksum += msg->data[i];
  }
  return msg->data[7] == (checksum ^ 0xFFU);
}

static void byd_rx_hook(const CANPacket_t *msg) {
  if (msg->bus == 0U) {
    if (msg->addr == BYD_VCU_PEDAL_STATE) {
      gas_pressed = msg->data[0] > 10U;
    }
    if (msg->addr == BYD_VCU_DRIVE_STATE) {
      brake_pressed = GET_BIT(msg, 37U);
    }
    if (msg->addr == BYD_ESP_VEH_SPEED) {
      const int speed_raw = (msg->data[1] << 8U) | msg->data[0];
      const float speed = speed_raw * 0.07142857f * KPH_TO_MS;
      vehicle_moving = speed > 0.1;
      UPDATE_VEHICLE_SPEED(speed);
    }
    if (msg->addr == BYD_SAS_SENSOR_STATE) {
      const int angle = to_signed((msg->data[1] << 8U) | msg->data[0], 16);
      update_sample(&angle_meas, angle);
    }
  }

  if ((msg->bus == 2U) && (msg->addr == BYD_HUD_ADAS_STATE)) {
    const int acc_state = (msg->data[2] >> 3U) & 0x7U;
    pcm_cruise_check((acc_state == 3) || (acc_state == 5));
  }
}

// Canonical 8-point grid, matching nagaspilot/speed_zones.py's
// STEER_ZONE_SPEEDS_MPS exactly (0/2/6/12/18/24/30/36 m/s: the union of the
// named CRAWL/WALK/CITY/URBAN/HIGHWAY/MAX zones plus two technical
// midpoints at 18/30, added so linear interpolation doesn't loosen the
// worst-case lateral accel bound between URBAN and HIGHWAY - verified:
// dropping to the 6 named points alone raises the 12-24 m/s peak from
// 1.35g to 1.61g). opendbc_repo has no dependency on nagaspilot/, so this
// hardcodes the same numbers rather than importing them.
//
// Backstop angle ceiling: the steering-wheel angle that implies ~1.3g
// lateral accel at each speed (steer_ratio=19.8, wheelbase=2.72, rounded for
// readability). Defense-in-depth, not the operating limit - the continuous
// ISO vm check below (~63/16/7 deg at 12/24/36 m/s, 0.3g) is tighter at
// every real speed, so this only matters if the vm check is ever bypassed
// or wrong. 1.3g is above dry tire grip (~1.0g) by design margin but still
// physically bounded, unlike a flat per-zone value that would exceed 2g+ at
// the top of its band.
static const float BYD_ATTO3_ZONE_ANGLE_BP_MS[8] = {0., 2., 6., 12., 18., 24., 30., 36.};
static const float BYD_ATTO3_ZONE_ANGLE_DEG[8] = {390., 390., 360., 240., 120., 60., 45., 30.};

// Backstop angle-rate ceiling, same 8-point grid as the angle ceiling above.
// 0/2/6/12 m/s hold at the real evidenced EPS mechanical ceiling (~4 deg/20ms;
// shemps/byd-atto3-openpilot-port's stock Veoneer measurement: "max=4.8,
// 5 caused 29deg spikes/shaky wheel") since the jerk formula alone would
// otherwise imply an unachievable rate near zero speed. 18/24/30/36 m/s
// follow the same 1.3g-equivalent jerk taper as the angle ceiling, each
// already below that mechanical ceiling so it's the binding term there.
static const float BYD_ATTO3_ZONE_RATE_BP_MS[8] = {0., 2., 6., 12., 18., 24., 30., 36.};
static const float BYD_ATTO3_ZONE_RATE_DEG_20MS[8] = {4., 4., 4., 4., 3.2, 2.4, 1.6, 1.2};

static float byd_zone_interp(float speed_ms, const float *bp, const float *vals) {
  float speed = speed_ms;
  if (speed < bp[0]) {
    speed = bp[0];
  }
  int i = 0;
  while ((i < 6) && (speed > bp[i + 1])) {
    i += 1;
  }
  const float x0 = bp[i];
  const float x1 = bp[i + 1];
  const float y0 = vals[i];
  const float y1 = vals[i + 1];
  const float frac = (x1 > x0) ? ((speed - x0) / (x1 - x0)) : 0.;
  return y0 + (frac * (y1 - y0));
}

static int byd_zone_max_angle_can(float speed_ms) {
  const float angle_deg = byd_zone_interp(speed_ms, BYD_ATTO3_ZONE_ANGLE_BP_MS, BYD_ATTO3_ZONE_ANGLE_DEG);
  return (int)(angle_deg * 10.);
}

static float byd_zone_max_rate_degps(float speed_ms) {
  const float rate_deg_20ms = byd_zone_interp(speed_ms, BYD_ATTO3_ZONE_RATE_BP_MS, BYD_ATTO3_ZONE_RATE_DEG_20MS);
  return rate_deg_20ms * 50.;
}

static bool byd_tx_hook(const CANPacket_t *msg) {
  bool violation = false;
  // Controller comfort anchors are CRAWL (0-2), WALK (2-6), CITY (6-12),
  // URBAN (12-24), and HIGHWAY (24-36 m/s).
  // max_angle = 390 deg matches shemps/byd-atto3-openpilot-port's
  // opendbc/safety/safety/safety_byd.h (route-driven reference, "max_angle
  // 390 deg matches the python-side ANGLE_LIMITS"), NOT TC275/TC375
  // firmware's 120 deg - that number was an uncited placeholder introduced
  // in TC275_BrownPanda commit 36c23c0bb5 ("update safety") with no source,
  // and is the one that should be treated as unverified going forward.
  static const AngleSteeringLimits BYD_STEERING_LIMITS = {
    .max_angle = 3900,
    .angle_deg_to_can = 10,
    // Legacy lookup storage is fixed at three points; the VM check below is
    // continuous and is the enforced ISO accel/jerk limit.
    .angle_rate_up_lookup = {{0., 12., 24.}, {4., 2., .5}},
    .angle_rate_down_lookup = {{0., 12., 24.}, {4., 3., 1.5}},
    .max_angle_error = 500,
    .angle_error_min_speed = 3.,
    .frequency = 50U,
    .enforce_angle_error = true,
  };

  const AngleSteeringParams BYD_STEERING_PARAMS = {
    .slip_factor = -0.0006166479,
    .steer_ratio = 19.8,
    .wheelbase = 2.72,
  };

  if (msg->addr == BYD_MPC_LATERAL_CMD) {
    const float speed_ms = vehicle_speed.values[0] / VEHICLE_SPEED_FACTOR;
    const int zone_max_angle_can = byd_zone_max_angle_can(speed_ms);
    const int zone_max_rate_can = (int)(byd_zone_max_rate_degps(speed_ms) * 10.);
    const bool steer_req = GET_BIT(msg, 21U);
    const bool steer_req_active_low = GET_BIT(msg, 20U);
    const int desired_angle = to_signed((msg->data[4] << 8U) | msg->data[3], 16);
    const int angle_rate_upper = to_signed(GET_BYTES(msg, 0, 2) & 0x3FFU, 10);
    const int angle_rate_lower = to_signed((GET_BYTES(msg, 1, 2) >> 2U) & 0x3FFU, 10);

    violation |= steer_req == steer_req_active_low;
    violation |= steer_req ? ((angle_rate_upper != 251) || (angle_rate_lower != -252))
                           : ((angle_rate_upper != 0) || (angle_rate_lower != 0));
    violation |= !GET_BIT(msg, 22U) || !GET_BIT(msg, 23U);
    violation |= (msg->data[5] != 0xFFU) || ((msg->data[6] & 0xFU) != 0xFU);
    violation |= max_limit_check(desired_angle, BYD_STEERING_LIMITS.max_angle,
                                 -BYD_STEERING_LIMITS.max_angle);
    violation |= max_limit_check(desired_angle, zone_max_angle_can, -zone_max_angle_can);
    // Zone-based rate backstop (BYD_ATTO3_ZONE_RATE_*): defense-in-depth
    // alongside the continuous VM jerk check below, same role as the angle
    // ceiling above.
    if (controls_allowed && steer_req) {
      violation |= max_limit_check(desired_angle, desired_angle_last + zone_max_rate_can,
                                   desired_angle_last - zone_max_rate_can);
    }
    violation |= steer_angle_cmd_checks_vm(desired_angle, steer_req, BYD_STEERING_LIMITS, BYD_STEERING_PARAMS);
    if (violation) {
      desired_angle_last = CLAMP(angle_meas.values[0], -BYD_STEERING_LIMITS.max_angle,
                                 BYD_STEERING_LIMITS.max_angle);
    }
  }

  if (msg->addr == BYD_MPC_STATE) {
    violation |= !controls_allowed;
  }

  violation |= !byd_checksum_valid(msg);
  return !violation;
}

static safety_config byd_init(uint16_t param) {
  UNUSED(param);
  static RxCheck byd_rx_checks[] = {
    {.msg = {{BYD_VCU_PEDAL_STATE, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    {.msg = {{BYD_VCU_DRIVE_STATE, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    {.msg = {{BYD_ESP_VEH_SPEED, 0, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    {.msg = {{BYD_SAS_SENSOR_STATE, 0, 5, 100U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
    {.msg = {{BYD_HUD_ADAS_STATE, 2, 8, 50U, .ignore_checksum = true, .ignore_counter = true, .ignore_quality_flag = true}, {0}, {0}}},
  };

  static const CanMsg BYD_TX_MSGS[] = {
    {BYD_MPC_LATERAL_CMD, 0, 8, .check_relay = true},
    {BYD_MPC_STATE, 0, 8, .check_relay = true},
  };
  return BUILD_SAFETY_CFG(byd_rx_checks, BYD_TX_MSGS);
}

const safety_hooks byd_hooks = {
  .init = byd_init,
  .rx = byd_rx_hook,
  .tx = byd_tx_hook,
};
