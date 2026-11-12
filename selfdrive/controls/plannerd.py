#!/usr/bin/env python3
from cereal import car
from openpilot.common.params import Params
from openpilot.common.realtime import Priority, config_realtime_process
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.controls.lib.ldw import LaneDepartureWarning
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner, FeatureFlags
from exopilot.runtime.drive_mode import DriveModeApplier
import cereal.messaging as messaging


def main():
  config_realtime_process(5, Priority.CTRL_LOW)

  cloudlog.info("plannerd is waiting for CarParams")
  params = Params()
  CP = messaging.log_from_bytes(params.get("CarParams", block=True), car.CarParams)
  cloudlog.info("plannerd got CarParams: %s", CP.brand)

  ldw = LaneDepartureWarning()
  # Driver preference: constant kph offset applied to the final v_cruise,
  # matching EOP10's EOPSpeedLimitOffset. Default 0 is a no-op; read once,
  # matching this branch's other panel/non-panel toggles (see
  # NGP10_FEATURE_MATRIX.md's "Live vs. next-drive toggles").
  speed_offset_kph = int(params.get("SpeedLimitOffset", return_default=True))
  longitudinal_planner = LongitudinalPlanner(CP, speed_offset_kph=speed_offset_kph)
  drive_mode = DriveModeApplier(params)  # DriveMode: eco / normal / sport write the style params; custom writes nothing
  pm = messaging.PubMaster(['longitudinalPlan', 'driverAssistance'])
  # 'mapData' (mapd), 'stereoObjects' (gridd: traffic lights) and 'driverMonitoringState' feed the map/light/distraction speed
  # caps (exopilot/runtime/map_speed.py). 'mapData' used to be left out because NGP10 had no MapData struct and no entry in
  # cereal/services.py, which crashed SubMaster.__init__ with KeyError('mapData'); both now exist (see cereal/log.capnp).
  sm = messaging.SubMaster(['carControl', 'carState', 'controlsState', 'liveParameters', 'radarState', 'modelV2', 'selfdriveState',
                            'navInstruction', 'accelerometer', 'monoDetections', 'pathAdjust', 'mapData', 'stereoObjects', 'driverMonitoringState'],
                           poll='modelV2',
                           ignore_alive=['navInstruction', 'accelerometer', 'monoDetections', 'pathAdjust', 'mapData', 'stereoObjects',
                                         'driverMonitoringState'])

  # DLON runs unconditionally -- a default, always-on behavior of this
  # branch, not a user-selectable feature.
  feature_flags = 0
  if params.get_bool("BRSCEnabled"):
    feature_flags |= FeatureFlags.BRSC
  if params.get_bool("LCAdjacentLeadHandoff"):
    feature_flags |= FeatureFlags.LC_LEAD_HANDOFF
  if params.get_bool("VTSCEnabled"):
    feature_flags |= FeatureFlags.VTSC
  if params.get_bool("NSLCEnabled"):
    feature_flags |= FeatureFlags.NSLC
  if params.get_bool("CutInSpeedEnabled"):
    feature_flags |= FeatureFlags.CUTIN
  if params.get_bool("PathdLongitudinal"):
    feature_flags |= FeatureFlags.PATHD

  while True:
    sm.update()
    drive_mode.update()
    if sm.updated['modelV2']:
      longitudinal_planner.update(sm, feature_flags)
      longitudinal_planner.publish(sm, pm)

      ldw.update(sm.frame, sm['modelV2'], sm['carState'], sm['carControl'])
      msg = messaging.new_message('driverAssistance')
      msg.valid = sm.all_checks(['carState', 'carControl', 'modelV2', 'liveParameters'])
      msg.driverAssistance.leftLaneDeparture = ldw.left
      msg.driverAssistance.rightLaneDeparture = ldw.right
      pm.send('driverAssistance', msg)


if __name__ == "__main__":
  main()
