#!/usr/bin/env python3
"""joystickd has been merged into SteamD."""

print("=" * 60)
print("ERROR: joystickd has been merged into SteamD.")
print("SteamD is now the single source of external vehicle control.")
print("")
print("To use joystick control:")
print("  1. Enable SteamD:  echo -n '1' > /data/params/d/SteamDEnabled")
print("  2. Enable remote:  echo -n '1' > /data/params/d/SteamDRemoteControl")
print("  3. Run:  tools/joystick/joystick_control.py")
print("")
print("See selfdrive/steamd/DRONE_ROADMAP.md for details.")
print("=" * 60)
exit(1)
