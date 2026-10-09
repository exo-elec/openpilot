# VisionPilot → OpenPilot 02M port audit

Audited 2026-10-08. Source: `exo-elec/visionpilot` `dev/02M` at
`3240a024080e17bc903bea46bca673e53c860234`. Target: `exo-elec/openpilot`
`dev/02M` at `7fdd1072c39ad8bb3657b9f25e05752c1abd4faf`.
The requested sibling `../visionpilot` checkout is absent from this workspace;
the source was fetched over SSH into `/tmp/visionpilot-port-audit` without
modifying its branches. VisionPilot's default `EVP09` at `edbe307` differs from
this source in four `src/` files, all radar-related; the voice/safety findings
below apply to both inspected tips. This is a source and startup-wiring audit,
not a vehicle test or certification of either implementation.

## Voice follow-up implemented

The subsequent 02M [cloudd implementation](CLOUDD_VOICE_PIPELINE.md) adds
explicit hold-to-speak capture, compressed online Google speech/Gemini replies,
navigation speech delivery and alert-priority playback. The table below records
the original audit baseline. NCP credential linking/vehicle voice actions, MRM,
AEC/noise suppression and source-only specialized perception are not implemented
by that follow-up. School/work-zone and sign ports are explicitly out of current
scope by user decision.

## Original missing or incomplete runtime paths

| Area | VisionPilot source evidence | OpenPilot 02M finding |
| --- | --- | --- |
| Account-linked cloud voice | `src/voice/cloud_assistant/cloud_assistant/cloud_assistant_node.py`; `src/launch/visionpilot_launch/launch/voice.launch.py` | `system/voiced/voiced.py` explicitly implements beamforming/VAD only. No wake-word/STT/cloud-assistant process or touch-confirm action path is registered. VisionPilot itself documents incomplete audio codec and confirmation UI, so it is a reference rather than a finished feature to copy wholesale. |
| Device credential bootstrap | `src/system/spp_navpilot_bridge/spp_navpilot_bridge/spp_navpilot_bridge_node.py` dispatches `CMD_DEVICE_CREDENTIAL` (`0x64`) and publishes credentials | Target `system/bluetoothd/ncp_session.py` has no `0x64` handler. Legacy auth/OAuth handlers ACK only. Capability payload omits `voiceCompanion`. |
| Voice command execution | `src/voice/command_routers/command_routers/command_routers_node.py` subscribes to intents and routes actuator commands | Target dispatches `CMD_VOICE_INTENT` (`0x28`) and publishes `voiceCommandRequest`, but no runtime Python subscriber was found across `system`, `selfdrive` and `nagaspilot`. Receipt/ACK does not mean execution. Mission guidance uses the same unconsumed stream. |
| Spoken navigation and driver messages | `src/navigation/navi_tts/navi_tts/navi_tts_node.py` supplies multilingual text to the voice output path | Target `selfdrive/navd/navd.py` already generates turn/arrival/recalculation `ttsRequest` messages. `selfdrive/soundd/soundd.py` subscribes only to `selfdriveState` and plays local tones; no runtime `ttsRequest` subscriber was found. Speech synthesis/transport/playback is missing, not route announcement generation. Comments claiming Azure/Piper service ownership are not implementation evidence. |
| Audio processing beyond VAD/beamforming | `src/voice/aec`, `noise_suppress`, `barge_in`, `wake_word` | No corresponding managed 02M audio pipeline. Mic and speaker transport exist, but echo cancellation, noise suppression, wake detection and interruption policy have not been connected. |
| Minimum-risk maneuver chain | `src/safety/mrm_handler`, `mrm_comfortable_stop`, `mrm_emergency_stop`, wired by `launch/safety.launch.py` | No equivalent staged MRM handler/operators in the target process registry. `selfdrived.py` emits health events; `nagaspilot/manager/eop_events.py` maps them to soft/immediate disable. That is takeover/disengagement, not a complete controlled-stop chain. SteamD link-loss stopping is a separate teleoperation behavior. |
| Specialized scene semantics | `src/perception/school_zone_detector`, `work_zone_detector`, `traffic_sign_detector`, `traffic_light_occlusion_predictor` | No equivalent dedicated semantic producers/consumers found in target runtime. Road/telephoto YOLO, traffic-light color classification and TLSC exist; they do not establish parity for school-zone schedules, construction-zone context, sign interpretation or predicted hidden-light state. Treat these as candidates: source school/work-zone implementations use simple CV heuristics and fixed geometry, not validated production detectors. |

The process registry used for this audit is
`nagaspilot/manager/process_config.py`; `system/manager/process_config.py` is a
compatibility import hook. Likewise many `selfdrive/` files are import hooks
for `nagaspilot/` implementations. A missing old filename alone is not a gap.

## Implemented or replaced, not missing ports

| Feature family | Active target implementation |
| --- | --- |
| RK3576 identity, five MIPI roles, 160 mm stereo geometry | `system/hardware/rk3576/`, `system/v4l2d/v4l2d.py`; HAL board data |
| Telephoto capture/detection/fusion | `CAMERAS_02M`, `teleRoadCameraState`, `nagaspilot/daemons/monod/monod.py` road/tele fusion, `EOPTeleEnabled` |
| ISP, AE/AWB and HDR plumbing | `system/v4l2d/isp/` and V4L2 capture; ROS register-driver packages intentionally replaced by BSP/V4L2/ISP |
| Stereo, occupancy, tracking and path planning | `nagaspilot/daemons/stereod`, `gridd`, `pathd`; includes Hybrid A* helpers |
| Surface detection/history and localization constraints | `nagaspilot/daemons/surfaced`, `coordinationd/{osm_localizer,sgm_localizer}.py` |
| Corner radar | `system/bluetoothd/ble_central.py` → `radar2d` → gridd for BLE TR13; `nagaspilot/daemons/radar4d` is the separate ATR24 WiFi point-cloud add-on, not a requirement for TR13 |
| Routing and phone convoy | `selfdrive/navd`, `system/bluetoothd/ncp_session.py`; moving destination/cancel handlers exist |
| Recording and teleoperation | `nagaspilot/daemons/recordd`, `system/mcapd`, `nagaspilot/daemons/steamd` |
| Floating panels and real-time map | `selfdrive/ui/components/panels.py`, `map_panel.py`, `osm_tiles.py`; OSM PiP added on this target branch |
| Driver monitoring | Steering/pedal activity is registered as `driveractivityd`. Face-camera monitoring is intentionally not equivalent: target registry states there is no driver camera. |
| ROS launch, topics and service-based inference | Replaced by manager processes, cereal/VisionIPC and target inference backends; do not import ROS packages merely to match names. |

These entries establish code/wiring presence, not complete numerical behavior
parity or hardware operation.

## Still incomplete, but not a completed VisionPilot feature lost in the port

- **RTK/NTRIP:** target exposes `EOPRTKEnabled`/`EOPNTRIPEnabled` UI controls,
  but no correction client or RTCM consumer exists. ExoPilot HAL has a
  `send_rtcm()` primitive with no caller. Inspected VisionPilot `src/` also
  has no NTRIP/RTCM implementation; `system/gnss/gnss_node.py` says it
  requires an external `rtk_daemon`. This is unfinished on both sides.
- **Camera bring-up:** the five-role target capture code exists, but ExoPilot
  `hal/hal/platform/rk3576_camera_paths.py` has no verified node mapping.
  Capture deliberately fails closed until real-unit role discovery. See
  [02M support](RK3576_02M_SUPPORT.md).
- **Board validation:** IMU identity/orientation, thermal policy, modem timing,
  audio geometry and NPU throughput remain hardware-validation items. Copying
  an old ROS driver cannot verify them.
- **Source cloud voice:** VisionPilot itself leaves codec and touch-confirm UI
  boundaries unfinished. Restore this through a coordinated app/backend/host
  design, not by enabling its unfinished node unchanged.

## Suggested next implementation order

1. Complete navigation speech delivery: one `ttsRequest` consumer, configured
   backend/provider, audio output with alert priority and interruption; preserve
   existing route text generation. This gives useful parity without accepting
   remote vehicle actions.
2. Add negotiated credential/voice support only with a real host consumer,
   secure credential lifecycle and reviewable touch confirmation. Keep
   `voiceCompanion` disabled until the end-to-end path works.
3. Separately design and validate MRM transitions against the target control
   state machine, longitudinal authority and stale-data behavior; do not bolt
   ROS brake operators onto OpenPilot's actuator loop.
4. Evaluate specialized perception individually with calibration, measured
   performance and a documented consumer before adding school/work-zone or
   occlusion claims. Do hardware bring-up in parallel when a unit is available.

No vehicle-control implementation was changed by this audit. Search results
cover checked-in runtime Python and the managed process registry; dynamic
external services not present in these trees cannot be verified here.
