# Feature parity and switches audit

Static audit of EOP10 → 01M → 02M. An inherited file or parameter reader does not prove vehicle behavior or hardware readiness. No controller or daemon is removed by this UI refactor. Hardware-specific RK3588/RK3576 implementations remain separate.

Added 64 persistent on/off controls with direct runtime readers. Existing controls and defaults are preserved. Settings lock while driving. Some readers cache values at startup; changing the persisted value may require a parked process restart.

## Persistent boolean settings

| Feature ID | UI switch | Direct runtime reader |
|---|---|---|
| `EgpuDrivingEnabled` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `QuietMode` | Yes | `selfdrive/soundd/soundd.py:37` |
| `AEBEnabled` | Yes | `nagaspilot/daemons/pathd/pathd.py:525` |
| `ALCCAllowAlways` | Yes | `selfdrive/controls/lib/alcc.py:68` |
| `ALCCHoldAtStandstill` | Yes | `selfdrive/controls/lib/alcc.py:69` |
| `ALCCUnifiedEngagement` | Yes | `selfdrive/controls/lib/alcc.py:70` |
| `AdaptdEnabled` | Yes | `nagaspilot/daemons/adaptd/adaptd.py:293` |
| `AdaptiveGapEnabled` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `AutoLaneChange` | Yes | `selfdrive/controls/lib/desire_helper.py:85` |
| `AutoTileEnabled` | Yes | `selfdrive/navd/tile_auto_manager.py:157` |
| `AutoTileWifiOnly` | Yes | `selfdrive/navd/tile_auto_manager.py:172` |
| `BlindSpotIndicator` | Yes | No direct reader found; review dynamic reads and integration before advertising support |
| `BSDChimeEnabled` | Yes | `selfdrive/controls/controlsd.py:180` |
| `BSDEnabled` | Yes | `selfdrive/controls/controlsd.py:178` |
| `BluetoothEnabled` | Yes | `system/bluetoothd/bluetoothd.py:60` |
| `BluetoothRadarEnabled` | Yes | `system/bluetoothd/ble_central.py:478` |
| `BLERadarPairingOpen` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `CATEnabled` | Yes | `selfdrive/controls/lib/cat.py:58` |
| `CATManualSREnabled` | Yes | `selfdrive/controls/lib/cat.py:101` |
| `CurveSpeedLearnEnabled` | Yes | `selfdrive/controls/lib/mtsc.py:78` |
| `DLONCurvesEnabled` | Yes | `selfdrive/controls/lib/dlon.py:177` |
| `DLONForceStopsEnabled` | Yes | `selfdrive/controls/lib/dlon.py:185` |
| `DLONLaneConfidenceEnabled` | Yes | `selfdrive/controls/lib/dlon.py:178` |
| `DLONLowSpeedEnabled` | Yes | `selfdrive/controls/lib/dlon.py:180` |
| `DLONNavigationEnabled` | Yes | `selfdrive/controls/lib/dlon.py:182` |
| `DLONSignalEnabled` | Yes | `selfdrive/controls/lib/dlon.py:183` |
| `DLONSlowLeadEnabled` | Yes | `selfdrive/controls/lib/dlon.py:179` |
| `DLONSpeedLimitEnabled` | Yes | `selfdrive/controls/lib/dlon.py:184` |
| `DLONStopPredictionEnabled` | Yes | `selfdrive/controls/lib/dlon.py:181` |
| `DLPCurvesEnabled` | Yes | `selfdrive/controls/lib/dlat.py:83` |
| `DeviceBeep` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `DeviceGoOffRoad` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `DeviceIsRhd` | Yes | `selfdrive/modeld/modeld.py:616` |
| `FactoryCalibrated` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `GlobaldEnabled` | Yes | `nagaspilot/daemons/coordinationd/coordinationd.py:96` |
| `GridEnabled` | Yes | `nagaspilot/daemons/gridd/gridd.py:897` |
| `GroundEnabled` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `HealthMonitorEnabled` | Yes | `selfdrive/selfdrived/selfdrived.py:161` |
| `HybridPlannerEnabled` | Yes | `nagaspilot/daemons/pathd/pathd.py:546` |
| `LCABSMEnabled` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `LCAConfirmRequired` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `LCAControllerEnabled` | Yes | `selfdrive/controls/lib/desire_helper.py:84` |
| `LCAGapEvalEnabled` | Yes | `selfdrive/controls/lib/desire_helper.py:87` |
| `LCALaneWidthEnabled` | Yes | `selfdrive/controls/lib/desire_helper.py:88` |
| `LCASurroundOverride` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `LCAdjacentLeadHandoff` | Yes | `selfdrive/controls/lib/lc_lead_handoff.py:15` |
| `LatALCC` | Yes | `selfdrive/controls/lib/alcc.py:67` |
| `LatRoadEdgeDetection` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `LeftCameraEnabled` | Yes | `nagaspilot/daemons/sided/sided.py:329` |
| `LonExtRadar` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `MSLCEnabled` | Yes | `selfdrive/controls/lib/mslc.py:44` |
| `MTSCEnabled` | Yes | `selfdrive/controls/lib/mtsc.py:76` |
| `MapdEnabled` | Yes | `selfdrive/controls/lib/mtsc.py:77` |
| `MicAdaptiveLoudness` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `MonoDEnabled` | Yes | `nagaspilot/daemons/monod/monod.py:303` |
| `MonoDSceneSegEnabled` | Yes | No direct reader found; review dynamic reads and integration before advertising support |
| `MonoDTeleEnabled` | Yes | No direct reader found; review dynamic reads and integration before advertising support |
| `MonoDWideEnabled` | Yes | No direct reader found; review dynamic reads and integration before advertising support |
| `MultiCameraCalibEnabled` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `NSLCEnabled` | Yes | `selfdrive/controls/lib/nslc.py:24` |
| `NTRIPEnabled` | Yes | No direct reader found; review dynamic reads and integration before advertising support |
| `NavBleEnabled` | Yes | No direct reader found; review dynamic reads and integration before advertising support |
| `NavEnabled` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `NavPilotPaired` | Not exposed | `system/subscribed/subscribed.py:275` |
| `NavVoiceEnabled` | Yes | `selfdrive/navd/navd.py:82` |
| `NudgeEnabled` | Yes | `nagaspilot/daemons/pathd/pathd.py:528` |
| `OneLaneChange` | Yes | `selfdrive/controls/lib/desire_helper.py:86` |
| `OsmLocalizerEnabled` | Yes | `nagaspilot/daemons/coordinationd/coordinationd.py:97` |
| `PointcloudEnabled` | Yes | `nagaspilot/daemons/pointcloudd/pointcloudd.py:613` |
| `PointcloudUseGPU` | Yes | `nagaspilot/daemons/pointcloudd/pointcloudd.py:646` |
| `PredictEnabled` | Yes | `nagaspilot/daemons/pathd/pathd.py:520` |
| `RCDEnabled` | Yes | No direct reader found; review dynamic reads and integration before advertising support |
| `RTKEnabled` | Yes | No direct reader found; review dynamic reads and integration before advertising support |
| `RearCameraEnabled` | Yes | `nagaspilot/daemons/reard/reard.py:171` |
| `RecordEnabled` | Yes | No direct reader found; review dynamic reads and integration before advertising support |
| `RecorddParkingEnabled` | Yes | `nagaspilot/daemons/recordd/recordd.py:888` |
| `RedControllerEnabled` | Yes | `selfdrive/controls/lib/red.py:221` |
| `RightCameraEnabled` | Yes | `nagaspilot/daemons/sided/sided.py:330` |
| `SGMLocalizerEnabled` | Yes | `nagaspilot/daemons/coordinationd/coordinationd.py:98` |
| `SLCConfirmHigher` | Yes | `nagaspilot/runtime/eop_utils.py:186` |
| `SLCConfirmLower` | Yes | `nagaspilot/runtime/eop_utils.py:185` |
| `SOCControllerEnabled` | Yes | `nagaspilot/daemons/pathd/pathd.py:526` |
| `SPPAutoReconnect` | Yes | `system/bluetoothd/spp.py:151` |
| `SPPEnabled` | Yes | `system/bluetoothd/spp.py:150` |
| `SQSCEnabled` | Yes | `selfdrive/controls/lib/sqsc.py:212` |
| `SQSCLookaheadEnabled` | Yes | `selfdrive/controls/lib/sqsc.py:213` |
| `SQSCUseLearnedSpeed` | Yes | `selfdrive/controls/lib/sqsc.py:214` |
| `SafetyEventLogEnabled` | Yes | `system/socketd/safety/safety_manager.py:45` |
| `ShockDetection` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `SideCamerasEnabled` | Yes | `nagaspilot/daemons/sided/sided.py:326` |
| `SideCamerasSwapped` | Yes | `nagaspilot/daemons/sided/sided.py:331` |
| `StereoEnabled` | Yes | `nagaspilot/daemons/pathd/pathd.py:527` |
| `SubscriptionSkipVerify` | Not exposed | `system/subscribed/subscribed.py:230` |
| `SurfaceEnabled` | Yes | `nagaspilot/daemons/surfaced/surfaced.py:530` |
| `SurfaceLongHorizon` | Yes | `nagaspilot/daemons/surfaced/surfaced.py:564` |
| `TJAEnabled` | Yes | `selfdrive/controls/lib/longcontrol.py:86` |
| `TLSCEnabled` | Yes | `selfdrive/controls/lib/tlsc.py:50` |
| `TTSAlertsEnabled` | Yes | `selfdrive/controls/controlsd.py:181` |
| `TeleEnabled` | Yes | `nagaspilot/daemons/monod/monod.py:306` |
| `TrackEnabled` | Yes | `nagaspilot/daemons/pathd/pathd.py:519` |
| `UIDisplayMode` | Yes | `selfdrive/ui/state.py` |
| `UIRadarTracks` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `UIRainbow` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `VTSCEnabled` | Yes | `selfdrive/controls/lib/vtsc.py:131` |
| `ChestnutDrivingEnabled` | Not exposed | No direct reader found; review dynamic reads and integration before advertising support |
| `WakeEnabled` | Yes | `nagaspilot/manager/process_config.py:77` |
| `CloudVoiceEnabled` | Yes | `nagaspilot/manager/process_config.py:73` |
| `VoiceEnabled` | Yes | `nagaspilot/manager/process_config.py:70` |
| `NeuralNetworkLateralControl` | Yes | `selfdrive/controls/lib/latcontrol_torque.py:85` |
| `SteamDEnabled` | Yes | `nagaspilot/manager/process_config.py:213` |
| `SteamDJoystickInput` | Yes | `nagaspilot/daemons/steamd/steamd.py:53` |

## Known limitations

RTK/NTRIP transport remains incomplete; existing flags do not establish a working correction client. New controls are not added for unverified flags. Subscription entitlements, provisioning credentials, transient status and one-shot actions are not feature switches. Numeric tuning values retain their existing controls; untriaged parameters remain visible in the settings coverage report.

School/work-zone/sign features remain outside the requested port scope. VisionPilot cloud account linking, voice vehicle-action execution and staged MRM remain separate gaps documented in the port audit.

## Shared UI and icons

All PyQt5 view, component, navigation-state, styling and UI-test files are shared between branches after this refactor, except display geometry in `components/theme.py`. Wide floating panels load only when the viewport is at least 1280 pixels; the normal 1024 × 600 layout creates none. Map, DVR and navigation components are reusable modules, not extra always-running daemons.

Current HUD/navigation icons are drawn primitives and warning glyphs; offroad navigation is text tabs. These definitions and resource paths are shared. Full visual icon parity with Nagasware is not claimed by code-sharing alone.

## EOP10 registry preservation

All 259 distinct EOP10 parameter keys remain in the shared registry. `EOPBEVWidgetEnabled` has been restored with a PyQt5 BEV renderer for lanes, road edges, radar leads and blind-spot severity. BEV Display Mode is optional; the normal 01M view remains a full-screen camera without floating panels.

Every persistent boolean with a direct runtime reader is now exposed except `EOPNavPilotPaired` (provisioning state) and `EOPSubscriptionSkipVerify` (internal entitlement verification bypass). No-direct-reader results are static findings, not proof that dynamic readers do not exist.

Restored switch: `BEVWidget` → `selfdrive/ui/state.py`, with live drawing in `components/bev.py`. Shared UI settings add 64 controls total.
