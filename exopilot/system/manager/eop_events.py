"""ExoPilot-specific onroad alerts, registered by the shared events module."""

from __future__ import annotations

from typing import Any


def camera_malfunction_alert(CP, CS, sm, metric, soft_disable_time, personality, *,
                              normal_permanent_alert):
  """Report the rear camera on EOP boards instead of comma's driver camera."""
  all_cams = ('roadCameraState', 'rearCameraState', 'wideRoadCameraState')
  bad_cams = [s.replace('State', '') for s in all_cams if s in sm.data.keys() and not sm.all_checks([s])]
  return normal_permanent_alert("Camera Malfunction", ', '.join(bad_cams))


def register_eop_events(events: dict[int, dict[str, Any]], *, EventName, ET, Alert,
                        AlertStatus, AlertSize, Priority, VisualAlert, AudibleAlert,
                        ImmediateDisableAlert, NoEntryAlert, NormalPermanentAlert,
                        EngagementAlert, soft_disable_alert) -> None:
  """Apply the EOP alert table over upstream's shared alert definitions."""
  events.pop(EventName.userBookmark, None)
  events.pop(EventName.audioFeedback, None)

  events[EventName.belowLaneChangeSpeed] = {
    ET.WARNING: Alert(
      "Lane Change Unavailable", "Speed Too Low",
      AlertStatus.userPrompt, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.prompt, .1),
  }
  events[EventName.cameraMalfunction][ET.PERMANENT] = (
    lambda CP, CS, sm, metric, soft_disable_time, personality:
      camera_malfunction_alert(
        CP, CS, sm, metric, soft_disable_time, personality,
        normal_permanent_alert=NormalPermanentAlert,
      )
  )
  events[EventName.preDriverUnresponsive] = {
    ET.PERMANENT: Alert(
      "Touch Steering Wheel: No Driver Detected", "",
      AlertStatus.normal, AlertSize.small, Priority.LOW,
      VisualAlert.steerRequired, AudibleAlert.none, .1),
  }

  events.update({
    EventName.stereoFault: {
      ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Stereo GPU Fault: Restart the Car"),
      ET.NO_ENTRY: NoEntryAlert("Stereo GPU Fault"),
      ET.PERMANENT: NormalPermanentAlert("Stereo GPU Fault", "Depth perception unavailable"),
    },
    EventName.inferenceFault: {
      ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Inference Backend Fault: Restart the Car"),
      ET.NO_ENTRY: NoEntryAlert("Inference Backend Fault"),
      ET.PERMANENT: NormalPermanentAlert("Inference Backend Fault", "NPU/GPU unavailable"),
    },
    EventName.monoFault: {
      ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Mono Camera Fault: Restart the Car"),
      ET.NO_ENTRY: NoEntryAlert("Mono Camera Fault"),
      ET.PERMANENT: NormalPermanentAlert("Mono Camera Fault", "Road perception unavailable"),
    },
    EventName.lowVisibility: {
      ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Road Not Visible: Severe Weather"),
      ET.NO_ENTRY: NoEntryAlert("Road Not Visible: Severe Weather"),
      ET.PERMANENT: NormalPermanentAlert("Road Not Visible", "Severe weather limits visibility"),
    },
    EventName.rgaFault: {
      ET.SOFT_DISABLE: soft_disable_alert("RGA Hardware Fault"),
      ET.NO_ENTRY: NoEntryAlert("RGA Hardware Fault"),
      ET.PERMANENT: NormalPermanentAlert("RGA Hardware Fault", "Image processing degraded"),
    },
    EventName.mppFault: {
      ET.PERMANENT: NormalPermanentAlert("MPP Encode Fault", "Recording unavailable"),
    },
    EventName.gridFault: {
      ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Grid Detection Fault: Restart the Car"),
      ET.NO_ENTRY: NoEntryAlert("Grid Detection Fault"),
      ET.PERMANENT: NormalPermanentAlert("Grid Detection Fault", "Object detection unavailable"),
    },
    EventName.pointcloudFault: {
      ET.PERMANENT: NormalPermanentAlert("Point Cloud Recording Fault", "Recording unavailable"),
    },
    EventName.healthWarning: {
      ET.PERMANENT: NormalPermanentAlert("System Stressed", "Consider taking over soon"),
    },
    EventName.healthDegradedStop: {
      ET.SOFT_DISABLE: soft_disable_alert("System Degraded"),
      ET.PERMANENT: NormalPermanentAlert("System Degraded", "Slowing down safely"),
      ET.NO_ENTRY: NoEntryAlert("System Degraded"),
    },
    EventName.healthCriticalStop: {
      ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("System Critical: Disengaging"),
      ET.PERMANENT: NormalPermanentAlert("System Critical", "Take control immediately"),
      ET.NO_ENTRY: NoEntryAlert("System Critical"),
    },
    EventName.lkasEnable: {
      ET.ENABLE: EngagementAlert(AudibleAlert.engage),
    },
    EventName.lkasDisable: {
      ET.USER_DISABLE: EngagementAlert(AudibleAlert.disengage),
    },
    EventName.manualSteeringRequired: {
      ET.WARNING: Alert("Manual Steering Required", "", AlertStatus.userPrompt, AlertSize.small,
                        Priority.LOW, VisualAlert.steerRequired, AudibleAlert.prompt, 1.),
    },
    EventName.controlsMismatchLateral: {
      ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Controls Mismatch Lateral"),
    },
    EventName.greenLightAlert: {
      ET.PERMANENT: Alert("Light Turned Green", "", AlertStatus.userPrompt, AlertSize.small,
                          Priority.LOW, VisualAlert.none, AudibleAlert.prompt, 3.),
    },
    EventName.leadDepartingAlert: {
      ET.PERMANENT: Alert("Lead Vehicle Departing", "", AlertStatus.userPrompt, AlertSize.small,
                          Priority.LOW, VisualAlert.none, AudibleAlert.prompt, 3.),
    },
    EventName.driverAttention: {
      ET.PERMANENT: Alert("Hand on Wheel", "Please Keep Hands on Steering Wheel",
                          AlertStatus.normal, AlertSize.small, Priority.LOW,
                          VisualAlert.none, AudibleAlert.none, .1),
    },
    EventName.driverWarning: {
      ET.PERMANENT: Alert("Hand on Wheel", "Hands Not Detected on Steering Wheel",
                          AlertStatus.userPrompt, AlertSize.mid, Priority.MID,
                          VisualAlert.steerRequired, AudibleAlert.promptDistracted, .1),
    },
    EventName.driverCritical: {
      ET.PERMANENT: Alert("ATTENTION REQUIRED", "Hands Not on Steering Wheel",
                          AlertStatus.critical, AlertSize.full, Priority.HIGH,
                          VisualAlert.steerRequired, AudibleAlert.warningImmediate, .1),
    },
  })
