#!/usr/bin/env python3
from cereal import car
from openpilot.common.params import Params
from openpilot.common.realtime import Priority, config_realtime_process, DT_MDL
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.controls.lib.ldw import LaneDepartureWarning
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
import cereal.messaging as messaging


def main() -> int:
  try:
    set_daemon_affinity("plannerd")
    config_realtime_process(DT_MDL, Priority.CTRL_LOW)

    cloudlog.info("plannerd is waiting for CarParams")
    params = Params()
    _car_params = params.get("CarParams")
    assert _car_params is not None, "CarParams not available"
    CP = messaging.log_from_bytes(_car_params, car.CarParams)
    cloudlog.info("plannerd got CarParams: %s", CP.brand)

    ldw = LaneDepartureWarning()
    longitudinal_planner = LongitudinalPlanner(CP)
    pm = messaging.PubMaster(['longitudinalPlan', 'driverAssistance', 'speedLimitState', 'ttsRequest'])
    sm = messaging.SubMaster(['carControl', 'carState', 'controlsState', 'liveParameters', 'radarState', 'modelV2', 'selfdriveState',
                              'mapData', 'navInstruction', 'stereoObjects', 'surfaceStatus', 'liveLocationKalman',
                              'enhancedTrajectory', 'radar4d', 'accelerometer'],
                             poll='modelV2',
                             ignore_alive=['mapData', 'navInstruction', 'stereoObjects',
                                           'surfaceStatus', 'liveLocationKalman', 'enhancedTrajectory', 'radar4d',
                                           'accelerometer'])

    while True:
      sm.update()
      if sm.updated['modelV2']:
        longitudinal_planner.update(sm)
        longitudinal_planner.publish(sm, pm)

        ldw.update(sm.frame, sm['modelV2'], sm['carState'], sm['carControl'])
        msg = messaging.new_message('driverAssistance')
        msg.valid = sm.all_checks(['carState', 'carControl', 'modelV2', 'liveParameters'])
        msg.driverAssistance.leftLaneDeparture = ldw.left
        msg.driverAssistance.rightLaneDeparture = ldw.right
        pm.send('driverAssistance', msg)
  except Exception as e:
    cloudlog.exception(f"PlannerId fatal error: {e}")
    raise


if __name__ == "__main__":
  exit(main())
