"""Shim package to expose the repository's `system` modules under `openpilot.system`."""
from __future__ import annotations

import pathlib
import sys

_pkg_dir = pathlib.Path(__file__).resolve().parent
_system_dir = (_pkg_dir.parent.parent / "system").resolve()

for path in (str(_pkg_dir), str(_system_dir)):
  if path not in sys.path:
    sys.path.insert(0, path)

__path__ = [str(_pkg_dir), str(_system_dir)]
