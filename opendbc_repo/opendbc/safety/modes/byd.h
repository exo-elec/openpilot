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

static bool byd_tx_hook(const CANPacket_t *msg) {
  bool violation = false;
  static const AngleSteeringLimits BYD_STEERING_LIMITS = {
    .max_angle = 3900,
    .angle_deg_to_can = 10,
    .angle_rate_up_lookup = {{0., 5., 15.}, {4., 4., 4.}},
    .angle_rate_down_lookup = {{0., 5., 15.}, {4., 4., 4.}},
    .max_angle_error = 500,
    .angle_error_min_speed = 3.,
    .frequency = 50U,
    .enforce_angle_error = true,
  };

  if (msg->addr == BYD_MPC_LATERAL_CMD) {
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
    violation |= steer_angle_cmd_checks(desired_angle, steer_req, BYD_STEERING_LIMITS);
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
