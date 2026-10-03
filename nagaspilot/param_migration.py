"""One-time migration of fork-added params to the EOP<Feature><Param> naming.

Params are plain files under Params().get_param_path(); renamed keys are no longer registered,
so they cannot be read through the Params API. The migration therefore renames the stored
files directly, before the manager reads anything. Values (OAuth tokens, geofences, settings)
carry over; if the new key already exists the stale old file is dropped.
"""
import os

RENAMES = {
  "BLECornerPairs": "EOPBLECornerPairs",
  "BLERadarPairingOpen": "EOPBLERadarPairingOpen",
  "BLERadarRoster": "EOPBLERadarRoster",
  "BluetoothPairingActive": "EOPBluetoothPairingActive",
  "BluetoothPairingAddr": "EOPBluetoothPairingAddr",
  "BluetoothPairingPin": "EOPBluetoothPairingPin",
  "CameraCalibrationParams": "EOPCameraCalibrationParams",
  "CarMake": "EOPCarMake",
  "CarType": "EOPCarType",
  "CarVin": "EOPCarVin",
  "ChestnutDrivingActive": "EOPChestnutDrivingActive",
  "ChestnutDrivingEnabled": "EOPChestnutDrivingEnabled",
  "ChestnutDrivingLoading": "EOPChestnutDrivingLoading",
  "CurrentMCAPRoute": "EOPCurrentMCAPRoute",
  "EgpuDrivingActive": "EOPEgpuDrivingActive",
  "EgpuDrivingEnabled": "EOPEgpuDrivingEnabled",
  "EgpuDrivingLoading": "EOPEgpuDrivingLoading",
  "FactoryCalibrationParams": "EOPFactoryCalibrationParams",
  "LastGPSAltitude": "EOPLastGPSAltitude",
  "LastGPSLatitude": "EOPLastGPSLatitude",
  "LastGPSLongitude": "EOPLastGPSLongitude",
  "NavDestination": "EOPNavDestination",
  "NavDestinationWaypoints": "EOPNavDestinationWaypoints",
  "NavPilotOAuthEmail": "EOPNavPilotOAuthEmail",
  "NavPilotOAuthToken": "EOPNavPilotOAuthToken",
  "NeuralNetworkLateralControl": "EOPNeuralNetworkLateralControl",
  "OpenBLTFirmwareVersion": "EOPOpenBLTFirmwareVersion",
  "OpenBLTState": "EOPOpenBLTState",
  "OpenBLTUpdateAvailable": "EOPOpenBLTUpdateAvailable",
  "PowerSaverEntryDuration": "EOPPowerSaverEntryDuration",
  "QuietMode": "EOPQuietMode",
  "SpeedLimitPolicy": "EOPSpeedLimitPolicy",
  "SteamDAuthToken": "EOPSteamDAuthToken",
  "SteamDControlRequest": "EOPSteamDControlRequest",
  "SteamDEnabled": "EOPSteamDEnabled",
  "SteamDGeofencePolygon": "EOPSteamDGeofencePolygon",
  "SteamDJoystickInput": "EOPSteamDJoystickInput",
  "SteamDRemoteControl": "EOPSteamDRemoteControl",
  "UbloxAssistNowToken": "EOPUbloxAssistNowToken",
  "UpdateStatus": "EOPUpdateStatus",
}


def migrate_renamed_params(param_dir: str) -> list[str]:
  """Rename stored old-name params to their new names. Returns the old names that were migrated."""
  migrated = []
  for old, new in RENAMES.items():
    src, dst = os.path.join(param_dir, old), os.path.join(param_dir, new)
    if not os.path.lexists(src):
      continue
    if os.path.lexists(dst):
      os.remove(src)
    else:
      os.replace(src, dst)
      migrated.append(old)
  return migrated
