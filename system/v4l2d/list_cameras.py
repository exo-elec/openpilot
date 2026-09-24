#!/usr/bin/env python3
"""Print what is needed to record ExoPilot 02M's camera device paths.

Run on a real 02M unit (read-only):
  python3 -m openpilot.system.v4l2d.list_cameras

v4l2d opens a MIPI camera only from a path confirmed on hardware (hal
rk3576_camera_paths.DEFAULT_MIPI_CAMERA_PATHS), because three cameras share
the OX03C10 sensor. This lists every V4L2 node with its driver name, every
sensor sub-device with its I2C bus-address (which identifies the role, see
v4l2d.CAMERAS_02M), and each media graph (`media-ctl -p`, when installed)
that links a sensor to its capture node. Record the result by role.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

SYSFS = "/sys/class/video4linux"
# Rockchip sensor sub-device names look like "m00_b_ox03c10 3-0036".
_SENSOR_RE = re.compile(r"(?P<sensor>ox03c10|gc4653)\S*\s+(?P<bus>\d+)-(?P<addr>[0-9a-fA-F]{4})")


def parse_sensor_name(name: str) -> tuple[str, int, int] | None:
  """'m00_b_ox03c10 3-0036' -> ('ox03c10', 3, 0x36)."""
  m = _SENSOR_RE.search(name.lower())
  if not m:
    return None
  return m["sensor"], int(m["bus"]), int(m["addr"], 16)


def role_for(sensor: str, bus: int, addr: int, cameras) -> str | None:
  for cam in cameras:
    if (cam.sensor_name, cam.i2c_bus, cam.i2c_addr) == (sensor, bus, addr):
      return cam.role
  return None


def v4l2_nodes(sysfs: str = SYSFS) -> list[tuple[str, str]]:
  out = []
  if not os.path.isdir(sysfs):
    return out
  for node in sorted(os.listdir(sysfs)):
    try:
      with open(os.path.join(sysfs, node, "name")) as f:
        out.append((node, f.read().strip()))
    except OSError:
      out.append((node, "?"))
  return out


def main() -> int:
  from openpilot.system.v4l2d.v4l2d import CAMERAS_02M

  print("Expected roles (hal boards.py, kernel/dts/rk3576-rpdzkj-exp02.dts):")
  for c in CAMERAS_02M:
    print(f"  {c.role:13s} {c.sensor_name:8s} I2C {c.i2c_bus}-{c.i2c_addr:04x}  -> stream {c.stream}")

  print("\nV4L2 nodes:")
  nodes = v4l2_nodes()
  if not nodes:
    print(f"  none ({SYSFS} missing) -- run this on the 02M unit")
  for node, name in nodes:
    ident = parse_sensor_name(name)
    role = role_for(*ident, CAMERAS_02M) if ident else None
    tag = f"   <- sensor for {role}" if role else ("   <- sensor, no matching role" if ident else "")
    print(f"  /dev/{node:14s} {name}{tag}")

  if shutil.which("media-ctl"):
    for dev in sorted(d for d in os.listdir("/dev") if d.startswith("media")):
      print(f"\n=== media-ctl -p -d /dev/{dev} ===")
      r = subprocess.run(["media-ctl", "-p", "-d", f"/dev/{dev}"], capture_output=True, text=True, check=False)
      print(r.stdout or r.stderr)
  else:
    print("\nmedia-ctl not installed (v4l-utils): install it to see sensor -> capture links.")

  print("\nFollow each sensor's links to its capture node (mainpath/video), check each")
  print("role with a live image, then record in hal rk3576_camera_paths.py, e.g.:")
  print("  DEFAULT_MIPI_CAMERA_PATHS = {")
  for c in CAMERAS_02M:
    print(f'    "{c.role}": ["/dev/videoN"],')
  print("  }")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
