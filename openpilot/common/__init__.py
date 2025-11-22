"""Thin shim to make `openpilot.common.*` resolve to the shared `common` package."""
from __future__ import annotations

import pathlib
import sys

_pkg_dir = pathlib.Path(__file__).resolve().parent
_common_dir = (_pkg_dir.parent.parent / "common").resolve()

# Ensure submodules (e.g., prefix.py) import from the shared common directory
for path in (str(_pkg_dir), str(_common_dir)):
  if path not in sys.path:
    sys.path.insert(0, path)

# Advertise both this shim directory and the shared common directory as the package search path
__path__ = [str(_pkg_dir), str(_common_dir)]
