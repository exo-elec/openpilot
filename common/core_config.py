#!/usr/bin/env python3
"""
Core CPU Affinity Configuration for EnhancedOpenPilot

Centralized configuration for CPU core allocation across OpenPilot daemons.
This provides a single source of truth for core assignments.

Usage:
    from openpilot.common.core_config import set_daemon_affinity

    # In daemon __init__:
    set_daemon_affinity("soundd")

Or manually:
    from openpilot.common.core_config import DAEMON_CORE_TYPE, CORE_BIG, CORE_LITTLE
    from openpilot.common.realtime import set_core_type

    set_core_type(DAEMON_CORE_TYPE["soundd"])  # or use CORE_BIG/CORE_LITTLE directly

Platform Support:
    - RK3588: Full OpenPilot support (ExoPilot 01M, the only supported platform)

Note: ExoPilot 02M (RK3576) and 03M (RK3688) are NOT supported by OpenPilot —
see VisionPilot / DoraPilot respectively.
"""

from openpilot.common.realtime import CORE_BIG, CORE_LITTLE, set_core_type
from openpilot.common.swaglog import cloudlog

# =============================================================================
# Daemon CPU Allocation - OpenPilot Only
# =============================================================================
# 
# CORE_BIG (A76 0-3): Safety-Critical ADAS
#   - Direct vehicle control
#   - Perception and planning
#   - Real-time sensor input (camera, IMU, CAN)
#
# CORE_LITTLE (A55 4-7): Support Functions
#   - I/O and networking
#   - User interface
#   - Non-critical services

DAEMON_CORE_TYPE = {
    # -------------------------------------------------------------------------
    # CORE_BIG (A76) - Safety Critical ADAS
    # -------------------------------------------------------------------------
    # Core ADAS Pipeline
    "modeld": CORE_BIG,         # Driving model - perception
    "controlsd": CORE_BIG,      # Vehicle control - steering/braking
    "gridd": CORE_BIG,          # Stereo depth - obstacle detection
    "pathd": CORE_BIG,          # Path planning - trajectory
    "plannerd": CORE_BIG,       # Longitudinal planning
    
    # Safety-Critical I/O
    "v4l2d": CORE_BIG,          # Camera capture - ADAS input
    "uvcd": CORE_BIG,           # USB camera capture (UVC via RTS5411S hub)
    "socketd": CORE_BIG,        # CAN bridge - vehicle communication (replaces pandad)
    "imud": CORE_BIG,           # IMU/accel - motion sensing (replaces sensord)
    "recordd": CORE_BIG,        # Recording daemon (DVR + impact + snap)
    "surfaced": CORE_BIG,       # Road surface monitoring (3D reconstruction + drivable area)
    "mapd": CORE_LITTLE,        # OSM map data provider (1Hz, not safety critical)
    "coordinationd": CORE_LITTLE,     # OSM/SGM localization + fusion (5Hz, not safety critical)
    "radard": CORE_BIG,         # Radar processing (if equipped)
    "stereod": CORE_BIG,        # Stereo depth computation (SGBM)
    "monod": CORE_BIG,          # Mono detection (RKNN NPU)
    "sided": CORE_BIG,          # Side camera perception
    "reard": CORE_BIG,          # Rear camera perception
    "pointcloudd": CORE_BIG,    # Point cloud recording
    
    # Data Pipeline
    "loggerd": CORE_BIG,        # Data logging
    "encoderd": CORE_BIG,       # Video encoding
    
    # -------------------------------------------------------------------------
    # CORE_LITTLE (A55) - Support Functions
    # -------------------------------------------------------------------------
    # Audio (Piper TTS navigation + adaptive loudness)
    "spkd": CORE_LITTLE,        # Speaker output via I2S DAC
    "soundd": CORE_LITTLE,      # TTS synthesis + alert tones
    "micd": CORE_LITTLE,        # Microphone input + SPL metering (standby — no mic on RK3588)
    
    # Positioning/Navigation (Accuracy - not safety critical)
    "pigeond": CORE_LITTLE,     # GPS driver
    "rtcd": CORE_LITTLE,        # RTC time sync
    "stated": CORE_LITTLE,      # System state machine
    "navd": CORE_LITTLE,        # Navigation
    "locationd": CORE_BIG,      # Localization - sensor fusion critical
    "lagd": CORE_BIG,           # Latency measurement daemon
    "camera_calibrationd": CORE_BIG,  # Multi-camera calibration
    # Navigation support
    "tile_autod": CORE_LITTLE,  # Auto tile download manager

    # System Management
    "hardwared": CORE_LITTLE,   # Thermal/power management
    "ignitiond": CORE_LITTLE,   # Power/ignition management

    # Communication / Subscription
    "bluetoothd": CORE_LITTLE,  # BLE/Phone
    "subscribed": CORE_LITTLE,  # NavPilot subscription & hardware auth
    "obd2d": CORE_LITTLE,       # OBD2 bridge for bluetoothd

    # UI
    "ui": CORE_LITTLE,          # Display UI

    # Logging/Maintenance
    "logmessaged": CORE_LITTLE, # Log aggregation
    "deleter": CORE_LITTLE,     # Log deletion
    "uploader": CORE_LITTLE,    # Log upload
    
    # Inference
    "inferenced": CORE_BIG,     # Inference backend (GPU/NPU pre-warm)
    "mcapd": CORE_LITTLE,       # MCAP parallel logging for Foxglove
    
    # Optional/External
    "valhalla_service": CORE_LITTLE,  # Valhalla routing service (offline nav)
    "steamd": CORE_LITTLE,      # VR teleoperation + external control (subsumes joystickd)
    
    # Misc
    "tripd": CORE_LITTLE,       # Trip statistics
    "calibrationd": CORE_BIG,   # Calibration - camera geometry critical
    "torqued": CORE_BIG,        # Torque estimation - vehicle dynamics critical
    "paramsd": CORE_BIG,        # Parameter learning - vehicle model critical
    "selfdrived": CORE_BIG,     # Self-drive state machine - safety critical
    "card": CORE_LITTLE,        # Car interface
}


def get_daemon_core_type(daemon_name: str) -> str:
    """
    Get CPU core type for a daemon.
    
    Args:
        daemon_name: Name of the daemon (e.g., "soundd", "modeld")
        
    Returns:
        Core type: CORE_BIG or CORE_LITTLE
        
    Raises:
        KeyError: If daemon not found in configuration
    """
    if daemon_name not in DAEMON_CORE_TYPE:
        # A missing affinity entry must never kill a daemon — default to the
        # efficiency cores and log so the table gets updated.
        cloudlog.warning(f"core_config: no entry for daemon '{daemon_name}', defaulting to CORE_LITTLE")
        return CORE_LITTLE
    return DAEMON_CORE_TYPE[daemon_name]


def set_daemon_affinity(daemon_name: str) -> None:
    """
    Set CPU affinity for the current process based on daemon name.
    
    Args:
        daemon_name: Name of the daemon
        
    Example:
        # In soundd.py __init__:
        from openpilot.common.core_config import set_daemon_affinity
        set_daemon_affinity("soundd")  # Sets to CORE_LITTLE (A55)
    """
    core_type = get_daemon_core_type(daemon_name)
    set_core_type(core_type)


def get_allocation_summary() -> dict:
    """
    Get summary of core allocation for reporting.
    
    Returns:
        Dict with 'big' and 'little' daemon lists
    """
    big = [name for name, core_type in DAEMON_CORE_TYPE.items() if core_type == CORE_BIG]
    little = [name for name, core_type in DAEMON_CORE_TYPE.items() if core_type == CORE_LITTLE]
    
    return {
        "big": sorted(big),
        "little": sorted(little),
        "total": len(DAEMON_CORE_TYPE),
        "big_count": len(big),
        "little_count": len(little),
    }


# =============================================================================
# Verification
# =============================================================================
if __name__ == "__main__":
    summary = get_allocation_summary()
    
    print("=" * 60)
    print("OpenPilot CPU Core Allocation Summary")
    print("=" * 60)
    print(f"\nCORE_BIG (A76 - Safety Critical): {summary['big_count']} daemons")
    for name in summary["big"]:
        print(f"  • {name}")
        
    print(f"\nCORE_LITTLE (A55 - Support Functions): {summary['little_count']} daemons")
    for name in summary["little"]:
        print(f"  • {name}")
        
    print(f"\nTotal: {summary['total']} daemons configured")
    print("=" * 60)
