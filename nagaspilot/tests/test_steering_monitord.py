from types import SimpleNamespace as NS

import cereal.messaging as messaging
from nagaspilot.controls.ngp_steering_monitor import CRITICAL, OK, PROMPT, SOFT
from nagaspilot.runtime.steering_monitord import SteeringMonitorD, driver_engaged, state_msg


def _cs(v=15.0, pressed=False, torque=0.0, standstill=False):
  return NS(vEgo=v, steeringPressed=pressed, steeringTorque=torque, standstill=standstill)


def _event_names(msg):
  from cereal import log
  names = {v: k for k, v in log.OnroadEvent.EventName.schema.enumerants.items()}
  return [names[e.name] for e in messaging.log_from_bytes(msg.to_bytes()).driverMonitoringState.events]


def test_only_the_cars_steering_pressed_counts_as_the_driver():
  assert driver_engaged(_cs(pressed=True))
  assert not driver_engaged(_cs(pressed=False, torque=3.0))  # a large torque alone (openpilot's own steering) is not a driver


def test_openpilots_own_steering_never_refills_awareness():
  d = SteeringMonitorD("strict")
  stages = []
  for _ in range(int(70.0 / 0.05)):
    stages.append(d.step(_cs(torque=2.5), True).stage)  # steering hard every tick, no press
  assert stages[0] == OK and stages[-1] == CRITICAL


def test_stages_publish_the_matching_events_and_awareness_status():
  d = SteeringMonitorD("strict")
  seen = {}
  for _ in range(int(61.0 / 0.05)):
    status = d.step(_cs(), True)
    if status.stage not in seen:
      seen[status.stage] = (status, state_msg(status, True))
  assert _event_names(seen[OK][1]) == []
  assert _event_names(seen[SOFT][1]) == ["preDriverUnresponsive"]
  assert _event_names(seen[PROMPT][1]) == ["promptDriverUnresponsive"]
  assert _event_names(seen[CRITICAL][1]) == ["driverUnresponsive"]
  critical = messaging.log_from_bytes(seen[CRITICAL][1].to_bytes()).driverMonitoringState
  assert critical.awarenessStatus < 0.0  # controlsd forces deceleration on this
  assert not critical.faceDetected and not critical.isActiveMode


def test_valid_flag_is_passed_through():
  d = SteeringMonitorD("strict")
  assert not state_msg(d.step(_cs(), True), False).valid
