#!/usr/bin/env python3
"""Vehicle controller (Tesla-format protocol).

Generates CAN messages for actuation based on control commands.
"""
from __future__ import annotations

import numpy as np

from cereal import car
# Note: CANPacker removed - using minimal teslacan directly
from openpilot.selfdrive.vehicled.tesla.values import (
  CarControllerParams
)
from openpilot.selfdrive.vehicled.tesla.teslacan import TeslaCAN


class CarController:
  """Vehicle controller.

  Generates CAN messages for actuation based on control commands.
  This replaces the generic CarController from opendbc with a
  protocol-specific implementation for Tesla-format CAN.
  """

  def __init__(self, CP: car.CarParams):
    self.CP = CP
    self.frame = 0
    self.apply_angle_last = 0.0

    # CAN packer for creating messages (Tesla-format protocol)
    self.can_interface = TeslaCAN()

    # Vehicle model for lateral limiting (using Model Y as reference)
    self.VM = self._create_vehicle_model()

  def _create_vehicle_model(self):
    """Create vehicle model for lateral limiting."""
    # Simplified vehicle model - returns curvature factor based on speed
    class SimpleVM:
      def curvature_factor(self, v_ego: float) -> float:
        # Approximate curvature factor for this platform
        # At low speed: higher curvature (tighter turns possible)
        # At high speed: lower curvature (gentler turns)
        return max(0.1, 1.0 - (v_ego / 40.0))
    return SimpleVM()

  def _apply_steer_angle_limits(
    self,
    desired_angle: float,
    last_angle: float,
    v_ego: float,
    current_angle: float,
    lat_active: bool
  ) -> float:
    """Apply steering angle rate limits.

    Uses vehicle model for lateral acceleration limiting.
    """
    if not lat_active:
      return current_angle

    # Maximum angle rate per frame (at 50Hz, 20ms per frame)
    max_rate = CarControllerParams.MAX_ANGLE_RATE  # deg per 20ms

    # Apply curvature factor from vehicle model
    curvature_factor = self.VM.curvature_factor(v_ego)
    max_angle = CarControllerParams.MAX_STEER_ANGLE * curvature_factor

    # Limit desired angle
    desired_angle = np.clip(desired_angle, -max_angle, max_angle)

    # Rate limit
    angle_diff = desired_angle - last_angle
    angle_diff = np.clip(angle_diff, -max_rate, max_rate)

    return last_angle + angle_diff

  def update(
    self,
    CC: car.CarControl,
    CS: car.CarState,
    now_nanos: int
  ) -> tuple[car.CarControl.Actuators, list[tuple[int, bytes, int]]]:
    """Update controller and generate CAN messages.

    Args:
      CC: CarControl command
      CS: Current VehicleState
      now_nanos: Current time in nanoseconds

    Returns:
      Tuple of (actuators output, list of CAN messages)
    """
    actuators = CC.actuators
    can_sends: list[tuple[int, bytes, int]] = []

    # EPS enforces disabling steering on heavy lateral override force.
    # When enabling in a tight curve, we wait until user reduces steering force.
    lat_active = CC.latActive and CS.hands_on_level < 3

    # Steering control at 50 Hz (every 2nd frame at 100Hz)
    if self.frame % CarControllerParams.STEER_STEP == 0:
      # Apply angle limits
      self.apply_angle_last = self._apply_steer_angle_limits(
        actuators.steeringAngleDeg,
        self.apply_angle_last,
        CS.out.vEgoRaw,
        CS.out.steeringAngleDeg,
        lat_active
      )

      can_sends.append(self.can_interface.create_steering_control(
        self.apply_angle_last, lat_active
      ))

    # Steering heartbeat at 10 Hz
    if self.frame % 10 == 0:
      can_sends.append(self.can_interface.create_steering_allowed())

    # Longitudinal control
    if self.CP.openpilotLongitudinalControl:
      # Send at 25 Hz (every 4th frame at 100Hz)
      if self.frame % 4 == 0:
        # State: 4=ACC_ON, 13=ACC_CANCEL_GENERIC_SILENT
        state = 13 if CC.cruiseControl.cancel else 4

        accel = float(np.clip(
          actuators.accel,
          CarControllerParams.ACCEL_MIN,
          CarControllerParams.ACCEL_MAX
        ))

        cntr = (self.frame // 4) % 8

        can_sends.append(self.can_interface.create_longitudinal_command(
          state, accel, cntr, CS.out.vEgo
        ))
    else:
      # Even without longitudinal control, we need to send cancel commands
      if CC.cruiseControl.cancel:
        cntr = ((CS.das_control or {}).get("DAS_controlCounter", 0) + 1) % 8
        can_sends.append(self.can_interface.create_longitudinal_command(
          13, 0, cntr, CS.out.vEgo
        ))

    # Build output actuators
    new_actuators = actuators.as_builder()
    new_actuators.steeringAngleDeg = self.apply_angle_last

    self.frame += 1
    return new_actuators, can_sends
