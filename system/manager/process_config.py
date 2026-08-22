import time

from cereal import car
from openpilot.common.params import Params
from openpilot.system.manager.process import PythonProcess, NativeProcess


# EOP-CLEANUP: TTL-cached param wrapper for process conditions.
# Manager calls should_run() every second for every process — uncached
# params.get_bool() causes 15+ file reads per second.
_PARAM_TTL_S = 5.0
_param_cache: dict[str, tuple[float, bool]] = {}


def _cached_param_bool(params: Params, key: str) -> bool:
  """Get boolean param with TTL caching (shared across all conditions)."""
  now = time.monotonic()
  if key in _param_cache:
    cached_at, value = _param_cache[key]
    if now - cached_at < _PARAM_TTL_S:
      return value
  value = params.get_bool(key)
  _param_cache[key] = (now, value)
  return value

def camera_on(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started

def notcar(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and CP.notCar

def iscar(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and not CP.notCar

def logging(started: bool, params: Params, CP: car.CarParams) -> bool:
  run = (not CP.notCar) or not _cached_param_bool(params, "DisableLogging")
  return started and run

def always_run(started: bool, params: Params, CP: car.CarParams) -> bool:
  return True

def only_onroad(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started

def only_offroad(started: bool, params: Params, CP: car.CarParams) -> bool:
  return not started

def or_(*fns):
  # operator.or_ is strictly binary; EOP composes 3-4 conditions
  return lambda *args: any(fn(*args) for fn in fns)

def and_(*fns):
  return lambda *args: all(fn(*args) for fn in fns)

# Ignition-aware process control (EXO platforms)
def ignition_on(started: bool, params: Params, CP: car.CarParams) -> bool:
  """Process runs when ignition is ON (GPIO low)."""
  return _cached_param_bool(params, "EOPIgnitionOn")

def ignition_or_parking(started: bool, params: Params, CP: car.CarParams) -> bool:
  """Process runs when ignition ON or in parking mode (always for DVR/crash detection)."""
  return _cached_param_bool(params, "EOPIgnitionOn") or _cached_param_bool(params, "EOPParkingMode")

def parking_mode_only(started: bool, params: Params, CP: car.CarParams) -> bool:
  """Process runs only in parking mode (ignition OFF)."""
  return _cached_param_bool(params, "EOPParkingMode")

# Optional condition functions
def not_joystick(started: bool, params: Params, CP: car.CarParams) -> bool:
  return not _cached_param_bool(params, "JoystickDebugMode")

def joystick(started: bool, params: Params, CP: car.CarParams) -> bool:
  return _cached_param_bool(params, "JoystickDebugMode")

def not_remote_control(started: bool, params: Params, CP: car.CarParams) -> bool:
  return not _cached_param_bool(params, "SteamDRemoteControl")


procs = [
  # Foundational Daemons
  PythonProcess("logmessaged", "system.logmessaged", always_run),
  PythonProcess("stated", "system.stated.stated", always_run),     # Vehicle state/ignition + parking mode
  PythonProcess("thermald", "system.thermald.thermald", always_run),  # Thermal/fan control + active protection
  PythonProcess("hardwared", "system.hardware.hardwared", always_run),  # Hardware/power management
  PythonProcess("wdgd", "system.wdgd.wdgd", always_run),  # Hardware watchdog

  # I/O Daemons
  PythonProcess("v4l2d", "system.v4l2d.v4l2d", camera_on),
  # Device Daemons (I2S/I2C sensors)
  PythonProcess("imud", "system.imud.imud", always_run),     # IMU (LSM6DS3)
  PythonProcess("micd", "system.micd.micd", always_run),     # Microphone (I2S)
  PythonProcess("spkd", "system.spkd.spkd", always_run),     # Speaker (I2S)
  PythonProcess("rtcd", "system.rtcd.rtcd", always_run),     # RTC time sync
  PythonProcess("socketd", "system.socketd.socketd", always_run),
  PythonProcess("pigeond", "system.ubloxd.pigeond", always_run),
  PythonProcess("inferenced", "system.inferenced.inferenced", always_run),  # Centralized compute HAL
  PythonProcess("subscribed", "system.subscribed.subscribed", always_run),  # EOP: NavPilot subscription & hardware auth
  PythonProcess("bluetoothd", "system.bluetoothd.bluetoothd", always_run),
  PythonProcess("obd2d", "selfdrive.obd2d.obd2d", always_run),
  PythonProcess("adaptd", "selfdrive.adaptd.adaptd", always_run),

  # Audio output (local alert tones only; language voice handled by Azure server)
  PythonProcess("soundd", "selfdrive.soundd.soundd", only_onroad),

  # UI
  NativeProcess("ui", "selfdrive/ui", ["./ui"], always_run, watchdog_max_dt=5),

  # Map and Navigation
  PythonProcess("mapd", "selfdrive.mapd.mapd", always_run),
  PythonProcess("navd", "selfdrive.navd.navd", always_run),

  # ADAS Pipeline
  PythonProcess("modeld", "selfdrive.modeld.modeld", ignition_on),
  PythonProcess("gridd", "selfdrive.gridd.gridd", ignition_on),
  PythonProcess("pathd", "selfdrive.pathd.pathd", ignition_on),
  PythonProcess("recordd", "selfdrive.recordd.recordd", ignition_or_parking),
  PythonProcess("tripd", "selfdrive.tripd.tripd", only_onroad),

  # Control Loop
  # controlsd is load-bearing: it is the ONLY onroad publisher of carControl
  # (socketd actuates it) and controlsState (selfdrived/modeld/plannerd/UI
  # consume it).  selfdrived owns the state machine + events only.  Do not
  # remove controlsd until carControl/controlsState move elsewhere.
  PythonProcess("controlsd", "selfdrive.controls.controlsd", and_(ignition_on, not_joystick, not_remote_control, iscar)),
  PythonProcess("selfdrived", "selfdrive.selfdrived.selfdrived", ignition_on),
  # socketd owns SocketCAN transport and the OpenDBC vehicle adapter loop.
  # radar3d: long-range UART radar producer (car.RadarData -> 'radar3d' socket).
  # Not gated on a presence param — the vehicle's forward-radar hardware is a
  # fixed part of this build.
  PythonProcess("radar3d", "selfdrive.controls.radar3d", ignition_on),
  # radard: fuses camera (modelV2) leads with 'radar3d' points -> radarState -> ACC.
  # Renamed from this repo's old radar3d.py to match upstream openpilot's name,
  # now that radar3d.py itself is the sensor producer, not the fusion daemon.
  PythonProcess("radard", "selfdrive.controls.radard", ignition_on),
  PythonProcess("plannerd", "selfdrive.controls.plannerd", ignition_on),
  PythonProcess("camera_calibrationd", "selfdrive.locationd.camera_calibrationd", ignition_on),  # EOP: Multi-camera calibration
  PythonProcess("locationd", "selfdrive.locationd.locationd", ignition_on),
  PythonProcess("lagd", "selfdrive.locationd.lagd", only_onroad),
  PythonProcess("torqued", "selfdrive.locationd.torqued", ignition_on),
  PythonProcess("paramsd", "selfdrive.locationd.paramsd", ignition_on),

  # Logging
  NativeProcess("loggerd", "system/loggerd", ["./loggerd"], logging),
  PythonProcess("mcapd", "system.mcapd.mcapd", logging),  # EOP: Parallel MCAP logging for Foxglove
  PythonProcess("deleter", "system.loggerd.deleter", always_run),
  # EOP: background Git update daemon (overlay-staged; no AGNOS/NEOS flashing).
  PythonProcess("updated", "system.updated", always_run),
  # EOP: No cloud uploader. Logs stay local only.
  # PythonProcess("uploader", "system.loggerd.uploader", always_run),

  # Stereo Depth
  PythonProcess("stereod", "selfdrive.stereod.stereod", ignition_on,
                enabled_callback=lambda started, params, CP:
                  ignition_on(started, params, CP) and _cached_param_bool(params, "EOPStereoEnabled")),

  # Surface Perception
  PythonProcess("surfaced", "selfdrive.surfaced.surfaced", ignition_on,
                enabled_callback=lambda started, params, CP:
                  ignition_on(started, params, CP) and _cached_param_bool(params, "EOPSurfaceEnabled")),

  # Coordination (sensor fusion: odometry + GPS + OSM/SGM constraints)
  PythonProcess("coordinationd", "selfdrive.coordinationd.coordinationd", ignition_on,
                enabled_callback=lambda started, params, CP:
                  ignition_on(started, params, CP) and _cached_param_bool(params, "EOPGlobaldEnabled")),

  # Point cloud recording
  PythonProcess("pointcloudd", "selfdrive.pointcloudd.pointcloudd", ignition_on,
                enabled_callback=lambda started, params, CP:
                  ignition_on(started, params, CP) and _cached_param_bool(params, "EOPPointcloudEnabled")),

  # Mono Detection (RKNN NPU)
  PythonProcess("monod", "selfdrive.monod.monod", ignition_on,
                enabled_callback=lambda started, params, CP:
                  ignition_on(started, params, CP) and _cached_param_bool(params, "EOPMonoDEnabled")),

  # Side / Rear Cameras (UVC via USB 3.0 hub RTS5411S)
  PythonProcess("uvcd", "system.uvcd.uvcd", camera_on,
                enabled_callback=lambda started, params, CP:
                  camera_on(started, params, CP) and (
                    _cached_param_bool(params, "EOPSideCamerasEnabled") or
                    _cached_param_bool(params, "EOPRearCameraEnabled")
                  )),
  PythonProcess("sided", "selfdrive.sided.sided", ignition_on,
                enabled_callback=lambda started, params, CP:
                  ignition_on(started, params, CP) and _cached_param_bool(params, "EOPSideCamerasEnabled")),
  PythonProcess("reard", "selfdrive.reard.reard", ignition_on,
                enabled_callback=lambda started, params, CP:
                  ignition_on(started, params, CP) and _cached_param_bool(params, "EOPRearCameraEnabled")),


  # VR Teleoperation (SteamD replaces teleoprtc + webrtcd)
  PythonProcess("steamd", "selfdrive.steamd.steamd", always_run,
                enabled_callback=lambda started, params, CP:
                  always_run(started, params, CP) and _cached_param_bool(params, "SteamDEnabled")),


]

managed_processes = {p.name: p for p in procs}
