#!/usr/bin/env python3
from cereal import car
from openpilot.common.params import Params
from openpilot.common.realtime import Priority, config_realtime_process
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.controls.lib.ldw import LaneDepartureWarning
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner, DPFlags
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
  sm = messaging.SubMaster(['carControl', 'carState', 'controlsState', 'liveParameters', 'radarState', 'modelV2', 'selfdriveState',
                            'accelerometer', 'mapdOut', 'mapdExtendedOut'],
                           poll='modelV2',
                           ignore_alive=['accelerometer', 'mapdOut', 'mapdExtendedOut'])

  dp_flags = 0
  if params.get_bool("dp_lon_acm"):
    dp_flags |= DPFlags.ACM
    if params.get_bool("dp_lon_acm_downhill"):
      dp_flags |= DPFlags.ACM_DOWNHILL
  if params.get_bool("dp_lon_aem"):
    dp_flags |= DPFlags.AEM
  # BRSC: Bumpy Road Speed Controller — NGP-prefixed since it's shared verbatim
  # across EOP10/NGP10/EDP10, unlike this branch's own dp_* toggles.
  if params.get_bool("ngp_lon_brsc"):
    dp_flags |= DPFlags.BRSC
  if params.get_bool("VTSCEnabled"):
    dp_flags |= DPFlags.VTSC
  if params.get_bool("MTSCEnabled"):
    dp_flags |= DPFlags.MTSC
  if params.get_bool("MSLCEnabled"):
    dp_flags |= DPFlags.MSLC
  if params.get_bool("LCAdjacentLeadHandoff"):
    dp_flags |= DPFlags.LC_LEAD_HANDOFF
  while True:
    sm.update()
    if sm.updated['modelV2']:
      longitudinal_planner.update(sm, dp_flags)
      longitudinal_planner.publish(sm, pm)

      ldw.update(sm.frame, sm['modelV2'], sm['carState'], sm['carControl'])
      msg = messaging.new_message('driverAssistance')
      msg.valid = sm.all_checks(['carState', 'carControl', 'modelV2', 'liveParameters'])
      msg.driverAssistance.leftLaneDeparture = ldw.left
      msg.driverAssistance.rightLaneDeparture = ldw.right
      pm.send('driverAssistance', msg)


if __name__ == "__main__":
  main()
