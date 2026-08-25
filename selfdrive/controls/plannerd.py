#!/usr/bin/env python3
from cereal import car
from openpilot.common.params import Params
from openpilot.common.realtime import Priority, config_realtime_process
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.controls.lib.ldw import LaneDepartureWarning
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner, NGPFlags
import cereal.messaging as messaging


def main():
  config_realtime_process(5, Priority.CTRL_LOW)

  cloudlog.info("plannerd is waiting for CarParams")
  params = Params()
  CP = messaging.log_from_bytes(params.get("CarParams", block=True), car.CarParams)
  cloudlog.info("plannerd got CarParams: %s", CP.brand)

  ldw = LaneDepartureWarning()
  longitudinal_planner = LongitudinalPlanner(CP)
  pm = messaging.PubMaster(['longitudinalPlan', 'driverAssistance'])
  # NOTE: 'mapData' is deliberately NOT subscribed here. NGP10 has no MapData
  # struct/Event field in cereal/log.capnp, no 'mapData' entry in
  # cereal/services.py, and no process publishes it -- subscribing crashed
  # SubMaster.__init__ with KeyError('mapData') on cereal.services.SERVICE_LIST
  # (a prior session ported this from EOP10, which does have the service).
  # See ngp_dlon.py::detect_speed_limit_trigger()'s docstring.
  sm = messaging.SubMaster(['carControl', 'carState', 'controlsState', 'liveParameters', 'radarState', 'modelV2', 'selfdriveState',
                            'navInstruction', 'accelerometer'],
                           poll='modelV2',
                           ignore_alive=['navInstruction', 'accelerometer'])

  # DLON runs unconditionally -- a default, always-on behavior of this
  # branch, not a user-selectable feature.
  ngp_flags = 0
  if params.get_bool("ngp_lon_brsc"):
    ngp_flags |= NGPFlags.BRSC
  if params.get_bool("ngp_lon_lc_lead_handoff"):
    ngp_flags |= NGPFlags.LC_LEAD_HANDOFF
  if params.get_bool("ngp_lon_vtsc"):
    ngp_flags |= NGPFlags.VTSC

  while True:
    sm.update()
    if sm.updated['modelV2']:
      longitudinal_planner.update(sm, ngp_flags)
      longitudinal_planner.publish(sm, pm)

      ldw.update(sm.frame, sm['modelV2'], sm['carState'], sm['carControl'])
      msg = messaging.new_message('driverAssistance')
      msg.valid = sm.all_checks(['carState', 'carControl', 'modelV2', 'liveParameters'])
      msg.driverAssistance.leftLaneDeparture = ldw.left
      msg.driverAssistance.rightLaneDeparture = ldw.right
      pm.send('driverAssistance', msg)


if __name__ == "__main__":
  main()
