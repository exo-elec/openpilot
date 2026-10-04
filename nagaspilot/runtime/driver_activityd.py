#!/usr/bin/env python3
"""Publishes `driverMonitoringState` from driver activity (wheel, brake, gas), for devices with no driver camera.

Drop-in for `dmonitoringd`: same message, same 20 Hz, so `selfdrived` (events), `controlsd` (forced deceleration when
`awarenessStatus < 0`) and the UI are unchanged. Only the driver's own inputs count (`steeringPressed`, `brakePressed`,
`gasPressed`); openpilot's own steering torque never does.
"""
import cereal.messaging as messaging
from cereal import log
from openpilot.common.realtime import DT_DMON, Ratekeeper
from openpilot.selfdrive.selfdrived.events import Events
from nagaspilot.controls.ngp_driver_activity import DEFAULT_POLICY, DriverActivityMonitor, MonitorStatus

EventName = log.OnroadEvent.EventName


def driver_engaged(CS) -> bool:
  """Hands on the wheel or a foot on a pedal, as the car reports it. openpilot's own steering torque never counts."""
  return bool(CS.steeringPressed or CS.brakePressed or CS.gasPressed)


def state_msg(status: MonitorStatus, valid: bool):
  events = Events()
  if status.event is not None:
    events.add(getattr(EventName, status.event))
  msg = messaging.new_message('driverMonitoringState', valid=valid)
  msg.driverMonitoringState = {
    "events": events.to_msg(),
    "faceDetected": False,
    "isDistracted": False,
    "distractedType": 0,
    "awarenessStatus": float(status.awareness),
    "awarenessActive": float(status.awareness),
    "awarenessPassive": float(status.awareness),
    "stepChange": 0.0,
    "isLowStd": True,
    "hiStdCount": 0,
    "isActiveMode": False,
    "isRHD": False,
  }
  return msg


class DriverActivityD:
  def __init__(self, policy: str = DEFAULT_POLICY):
    self.monitor = DriverActivityMonitor(policy, DT_DMON)

  def step(self, CS, enabled: bool) -> MonitorStatus:
    return self.monitor.update(CS.vEgo, enabled, bool(CS.standstill), driver_engaged(CS))


def main():
  from openpilot.common.params import Params
  policy = (Params().get("ngp_dm_policy", return_default=True) or DEFAULT_POLICY)
  policy = policy.decode() if isinstance(policy, bytes) else str(policy)
  daemon = DriverActivityD(policy)
  sm = messaging.SubMaster(['carState', 'selfdriveState'], poll='carState')
  pm = messaging.PubMaster(['driverMonitoringState'])
  rk = Ratekeeper(1.0 / DT_DMON, print_delay_threshold=None)
  while True:
    sm.update(0)
    status = daemon.step(sm['carState'], bool(sm['selfdriveState'].enabled))
    pm.send('driverMonitoringState', state_msg(status, sm.all_checks(['carState', 'selfdriveState'])))
    rk.keep_time()


if __name__ == '__main__':
  main()
