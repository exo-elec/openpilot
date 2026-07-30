from __future__ import annotations

import math
from dataclasses import dataclass
from collections.abc import Sequence

from openpilot.selfdrive.pathd import blindspot


@dataclass
class LaneChangeGateResult:
  signal_confirmed: bool
  requires_signal: bool
  torque_confirmed: bool
  requires_torque: bool
  safe: bool
  min_gap: float


def _has_side_camera_coverage(tracked_objects: Sequence, direction: str) -> bool:
  side_cameras = ['leftFront', 'leftRear'] if direction == 'left' else ['rightFront', 'rightRear']
  for obj in tracked_objects or []:
    sources = getattr(obj, 'cameraSources', None)
    if not sources:
      continue
    if any(cam in sources for cam in side_cameras):
      return True
  return False


def _driver_torque_confirmed(car_state, direction: str) -> bool:
  if car_state is None:
    return False
  if not getattr(car_state, 'steeringPressed', False):
    return False
  torque = getattr(car_state, 'steeringTorque', 0.0)
  return torque > 0.0 if direction == 'left' else torque < 0.0


def _signal_confirmed(direction: str, car_state) -> bool:
  if car_state is None:
    return False
  if direction == 'left':
    return bool(getattr(car_state, 'leftBlinker', False))
  if direction == 'right':
    return bool(getattr(car_state, 'rightBlinker', False))
  return False


def evaluate_lane_change_gate(direction: str,
                               car_state,
                               tracked_objects: Sequence,
                               predicted_objects: Sequence | None,
                               ego_velocity: float) -> LaneChangeGateResult:
  signal_confirmed = _signal_confirmed(direction, car_state)
  requires_signal = not signal_confirmed

  torque_confirmed = False
  requires_torque = False
  safe = False
  min_gap = math.inf

  if requires_signal:
    return LaneChangeGateResult(signal_confirmed, True, False, False, False, min_gap)

  sensor_available = _has_side_camera_coverage(tracked_objects, direction)
  if not sensor_available:
    requires_torque = True
    torque_confirmed = _driver_torque_confirmed(car_state, direction)
    if not torque_confirmed:
      return LaneChangeGateResult(True, False, torque_confirmed, True, False, min_gap)
  else:
    torque_confirmed = True

  blindspot_result = blindspot.check_lane_change_safety(tracked_objects, predicted_objects or [], direction, ego_velocity)
  safe = blindspot_result.safe
  min_gap = blindspot_result.min_gap

  return LaneChangeGateResult(
    signal_confirmed=signal_confirmed,
    requires_signal=False,
    torque_confirmed=torque_confirmed,
    requires_torque=requires_torque,
    safe=safe,
    min_gap=min_gap,
  )
