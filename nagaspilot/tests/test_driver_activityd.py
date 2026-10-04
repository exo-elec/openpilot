from types import SimpleNamespace as NS

import cereal.messaging as messaging
from nagaspilot.controls.ngp_driver_activity import CRITICAL, OK, PROMPT, SOFT
from nagaspilot.runtime.driver_activityd import DriverActivityD, driver_engaged, state_msg


def _cs(v=15.0, pressed=False, torque=0.0, standstill=False, brake=False, gas=False):
  return NS(vEgo=v, steeringPressed=pressed, steeringTorque=torque, standstill=standstill, brakePressed=brake, gasPressed=gas)


def _event_names(data):
  from cereal import log
  names = {v: k for k, v in log.OnroadEvent.EventName.schema.enumerants.items()}
  return [names[int(e.name.raw)] for e in messaging.log_from_bytes(data).driverMonitoringState.events]  # _DynamicEnum hashes unlike int


def test_wheel_press_brake_and_gas_count_as_the_driver_but_not_openpilots_own_steering():
  assert driver_engaged(_cs(pressed=True))
  assert driver_engaged(_cs(brake=True))
  assert driver_engaged(_cs(gas=True))
  assert not driver_engaged(_cs())
  assert not driver_engaged(_cs(torque=3.0))  # a large torque alone (openpilot's own steering) is not a driver


def test_a_pedal_press_refills_awareness_like_a_wheel_press():
  d = DriverActivityD("strict")
  for _ in range(int(45.0 / 0.05)):
    d.step(_cs(), True)
  assert d.step(_cs(), True).stage != "ok"
  assert d.step(_cs(brake=True), True).awareness == 1.0
  for _ in range(int(45.0 / 0.05)):
    d.step(_cs(), True)
  assert d.step(_cs(gas=True), True).awareness == 1.0


def test_openpilots_own_steering_never_refills_awareness():
  d = DriverActivityD("strict")
  stages = []
  for _ in range(int(70.0 / 0.05)):
    stages.append(d.step(_cs(torque=2.5), True).stage)  # steering hard every tick, no press
  assert stages[0] == OK and stages[-1] == CRITICAL


def test_stages_publish_the_matching_events_and_awareness_status():
  d = DriverActivityD("strict")
  seen = {}
  for _ in range(int(61.0 / 0.05)):
    status = d.step(_cs(), True)
    if status.stage not in seen:
      seen[status.stage] = (status, state_msg(status, True).to_bytes())  # serialise once: capnp warns on a second write
  assert _event_names(seen[OK][1]) == []
  assert _event_names(seen[SOFT][1]) == ["preDriverUnresponsive"]
  assert _event_names(seen[PROMPT][1]) == ["promptDriverUnresponsive"]
  assert _event_names(seen[CRITICAL][1]) == ["driverUnresponsive"]
  critical = messaging.log_from_bytes(seen[CRITICAL][1]).driverMonitoringState
  assert critical.awarenessStatus < 0.0  # controlsd forces deceleration on this
  assert not critical.faceDetected and not critical.isActiveMode


def test_valid_flag_is_passed_through():
  d = DriverActivityD("strict")
  assert not state_msg(d.step(_cs(), True), False).valid
