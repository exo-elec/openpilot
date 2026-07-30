#!/usr/bin/env python3
"""SteamD carControl publisher."""

import cereal.messaging as messaging

from openpilot.common.swaglog import cloudlog

from openpilot.selfdrive.steamd.config import SteamDConfig


class CarControlPublisher:
  """Builds and publishes carControl messages on behalf of SteamD.

  Uses lazy PubMaster initialization so SteamD does not register as a
  carControl publisher while in monitoring-only mode. This eliminates
  the publisher race with controlsd.
  """

  def __init__(self, config: SteamDConfig):
    self.config = config
    self.pm = None
    self._v_cruise = 0.0

  def _ensure_pubmaster(self):
    if self.pm is None:
      self.pm = messaging.PubMaster(["carControl", "controlsState"])
      cloudlog.info("SteamD publisher: acquired carControl + controlsState PubMaster")

  def drop_pubmaster(self):
    """Release the PubMaster so we stop competing on the carControl socket."""
    if self.pm is not None:
      # Send one final zero frame to clear any lingering actuators
      self.send_zero()
      self.pm = None
      cloudlog.info("SteamD publisher: dropped carControl PubMaster")

  def send_zero(self):
    """Publish a disabled, zeroed carControl frame."""
    if self.pm is None:
      return
    cc = messaging.new_message("carControl")
    cc.carControl.enabled = False
    cc.carControl.latActive = False
    cc.carControl.longActive = False
    cc.carControl.actuators.steer = 0.0
    cc.carControl.actuators.accel = 0.0
    cc.carControl.actuators.gas = 0.0
    cc.carControl.actuators.brake = 0.0
    cc.carControl.actuators.steeringAngleDeg = 0.0
    self.pm.send("carControl", cc)

  def send(self, steer: float, accel: float, enabled: bool = True):
    """Publish a carControl frame with the given actuators."""
    self._ensure_pubmaster()
    cc = messaging.new_message("carControl")
    cc.carControl.enabled = enabled
    cc.carControl.latActive = enabled
    cc.carControl.longActive = enabled

    cc.carControl.actuators.steer = float(steer)
    cc.carControl.actuators.accel = float(accel)
    cc.carControl.actuators.gas = 0.0
    cc.carControl.actuators.brake = 0.0
    cc.carControl.actuators.steeringAngleDeg = float(steer * self.config.max_steering_angle)

    self.pm.send("carControl", cc)

    # Also publish a minimal controlsState so the in-car UI doesn't go blank
    self._send_controls_state(steer=steer, accel=accel, enabled=enabled)

  def send_safe_stop(self, elapsed_ms: float):
    """Publish a safe-stop deceleration ramp based on link-loss duration."""
    # Ramp: 0.5s -> -1.0, 1.0s -> -2.0, >1.5s -> -3.5
    if elapsed_ms < 500:
      accel = 0.0
    elif elapsed_ms < 1000:
      accel = -1.0
    elif elapsed_ms < 1500:
      accel = -2.0
    else:
      accel = -3.5

    self.send(steer=0.0, accel=accel, enabled=True)
