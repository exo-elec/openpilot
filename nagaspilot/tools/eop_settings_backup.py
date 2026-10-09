#!/usr/bin/env python3
"""
EOP Settings Backup/Restore Tool

Exports all EOP-prefixed params to a JSON file for backup.
Restores from a previously exported JSON file.

Usage:
  python eop_settings_backup.py export /path/to/backup.json
  python eop_settings_backup.py import /path/to/backup.json
"""

import json
import sys
import os

# Add openpilot to path
if os.path.exists('/home/vcar/pilot/openpilot'):
  sys.path.insert(0, '/home/vcar/pilot/openpilot')

from openpilot.common.params import Params


def export_settings(path: str):
  """Export all EOP* params to JSON."""
  p = Params()
  keys = p.all_keys()
  eop_settings = {}
  for key in keys:
    if key.startswith("EOP"):
      val = p.get(key)
      if val is not None:
        try:
          # Try decode as string; fall back to bytes repr
          eop_settings[key] = val.decode('utf-8')
        except (UnicodeDecodeError, AttributeError):
          eop_settings[key] = repr(val)

  with open(path, 'w') as f:
    json.dump(eop_settings, f, indent=2, sort_keys=True)

  print(f"Exported {len(eop_settings)} EOP settings to {path}")


def import_settings(path: str):
  """Import EOP* params from JSON."""
  with open(path) as f:
    eop_settings = json.load(f)

  p = Params()
  imported = 0
  skipped = 0
  for key, val in eop_settings.items():
    if not key.startswith("EOP"):
      skipped += 1
      continue
    p.put(key, val.encode('utf-8') if isinstance(val, str) else str(val).encode('utf-8'))
    imported += 1

  print(f"Imported {imported} EOP settings from {path} (skipped {skipped} non-EOP keys)")


if __name__ == "__main__":
  if len(sys.argv) != 3:
    print(__doc__)
    sys.exit(1)

  action, path = sys.argv[1], sys.argv[2]
  if action == "export":
    export_settings(path)
  elif action == "import":
    import_settings(path)
  else:
    print(f"Unknown action: {action}")
    print(__doc__)
    sys.exit(1)
