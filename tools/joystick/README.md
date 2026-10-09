# Joystick (Deprecated)

**This tool is deprecated.** External vehicle control has been unified into **SteamD** (`selfdrive/steamd/`).  
SteamD is the single source of external control and subscribes to `testJoystick` directly.

## Migration

To use joystick control with SteamD:

```shell
# 1. Enable SteamD
python3 -c "from openpilot.common.params import Params; Params().put_bool('SteamDEnabled', True)"

# 2. Enable remote control session (driver must approve on-device, or set manually for offroad debug)
echo -n "1" > /data/params/d/SteamDRemoteControl

# 3. Run joystick control (publishes testJoystick for SteamD to consume)
tools/joystick/joystick_control.py --keyboard
```

## Legacy Notes

The original `joystickd` daemon has been removed from the process manager.  
`joystick_control.py` now sets `SteamDRemoteControl` instead of `JoystickDebugMode`.
