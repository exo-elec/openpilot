#!/usr/bin/env python3
"""Shared library loader for Rockchip hardware libraries on RK3588.

Search priority (default — production mode):
  1. System libraries (vendor .deb packages: /usr/lib, /usr/lib/aarch64-linux-gnu)
  2. ldconfig cache
  3. Project-shipped prebuilts (third_party/rockchip_algo, rknpu2 runtime)
  4. Self-built from submodules (rockchip_mpp/build, rockchip_rga/build)
  5. CPU fallback (handled by caller)

For development with local builds, set env var:
  export RK_LIBLOADER_PREFER_PROJECT=1
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
from pathlib import Path

LOG = logging.getLogger(__name__)

# Project root detection (__file__ = openpilot/system/hardware/rockchip/_libloader.py)
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_THIRD_PARTY = _PROJECT_ROOT / "third_party"

# Standard system paths for RK3588 Ubuntu BSP
_SYSTEM_PATHS = [
  "/usr/lib/aarch64-linux-gnu",
  "/usr/lib",
  "/lib/aarch64-linux-gnu",
  "/lib",
  "/usr/local/lib",
]

# Environment override
_PREFER_PROJECT = os.environ.get("RK_LIBLOADER_PREFER_PROJECT", "0") == "1"


def _detect_deb_package(lib_file: str) -> bool:
  """Check if a library was installed via a vendor .deb package."""
  try:
    result = subprocess.run(
      ["dpkg", "-S", lib_file],
      capture_output=True, text=True, timeout=5
    )
    return result.returncode == 0 and "rockchip" in result.stdout.lower()
  except Exception:
    return False


def _ldconfig_search(name: str) -> str | None:
  """Search ldconfig cache for a library."""
  try:
    result = subprocess.run(
      ["ldconfig", "-p"],
      capture_output=True, text=True, timeout=5
    )
    for line in result.stdout.splitlines():
      if name in line:
        parts = line.split("=>")
        if len(parts) == 2:
          path = parts[1].strip()
          if Path(path).exists():
            return path
  except Exception:
    pass
  return None


def _submodule_paths(name: str) -> list[Path]:
  """Generate search paths inside third_party submodules and algorithm libs."""
  paths: list[Path] = []

  # Algorithm libraries (no .deb available — shipped in repo)
  algo_dir = _THIRD_PARTY / "rockchip_algo" / "aarch64"
  if algo_dir.exists():
    if name in ("od_share", "md_share", "RKAP_3A"):
      paths.append(algo_dir)

  # rknpu2 prebuilt runtime libraries (RK3588)
  if name == "rknnrt":
    rknpu2 = _THIRD_PARTY / "rknpu2"
    if rknpu2.exists():
      paths.append(rknpu2 / "runtime" / "RK3588" / "Linux" / "librknn_api" / "aarch64")
      paths.append(rknpu2 / "runtime" / "RK356X" / "Linux" / "librknn_api" / "aarch64")
      paths.append(rknpu2 / "runtime" / "RK3588" / "Linux" / "rknn_server" / "aarch64" / "usr" / "bin")
      paths.append(rknpu2 / "runtime" / "RK356X" / "Linux" / "rknn_server" / "aarch64" / "usr" / "bin")
      paths.append(rknpu2 / "examples" / "3rdparty" / "rga" / "RK3588" / "lib" / "Linux" / "aarch64")
      paths.append(rknpu2 / "examples" / "3rdparty" / "rga" / "RK356X" / "lib" / "Linux" / "aarch64")
      paths.append(rknpu2 / "examples" / "3rdparty" / "mpp" / "Linux" / "aarch64")

  # Self-built from source submodules
  if name in ("rockchip_mpp", "mpp"):
    mpp = _THIRD_PARTY / "rockchip_mpp"
    if mpp.exists():
      paths.append(mpp / "build" / "mpp")
      paths.append(mpp / "build")
      paths.append(mpp / "install" / "lib")

  if name == "rga":
    rga = _THIRD_PARTY / "rockchip_rga"
    if rga.exists():
      paths.append(rga / "build")
      paths.append(rga / "libs")

  return [p for p in paths if p.exists()]


def _find_library(name: str, extra_paths: list[str] | None = None) -> str | None:
  """Find a shared library file on the system or in submodules.

  Args:
    name: Library base name without 'lib' prefix or '.so' suffix
          (e.g. 'rga', 'rockchip_mpp', 'rknnrt', 'od_share', 'md_share', 'RKAP_3A')
    extra_paths: Additional directories to search

  Returns:
    Absolute path to library, or None if not found
  """
  # Candidate file names
  candidates_map: dict[str, list[str]] = {
    "rknnrt":       ["librknnrt.so", "librknn_api.so"],
    "rockchip_mpp": ["librockchip_mpp.so", "librockchip_mpp.so.0", "librockchip_mpp.so.1"],
    "mpp":          ["librockchip_mpp.so", "librockchip_mpp.so.0", "librockchip_mpp.so.1"],
    "rga":          ["librga.so", "librga.so.2", "librga.so.2.1.0", "librga.so.2.2.0"],
    "od_share":     ["libod_share.so"],
    "md_share":     ["libmd_share.so"],
    "RKAP_3A":      ["libRKAP_3A.so"],
  }
  candidates = candidates_map.get(name, [f"lib{name}.so", f"lib{name}.so.0", f"lib{name}.so.1"])

  # Build search path list
  paths: list[Path] = []

  if _PREFER_PROJECT:
    # Development mode: project first
    paths.extend(_submodule_paths(name))
    if extra_paths:
      paths.extend([Path(p) for p in extra_paths])
    paths.extend([Path(p) for p in _SYSTEM_PATHS])
  else:
    # Production mode: system first ( .deb packages )
    paths.extend([Path(p) for p in _SYSTEM_PATHS])
    if extra_paths:
      paths.extend([Path(p) for p in extra_paths])
    paths.extend(_submodule_paths(name))

  # LD_LIBRARY_PATH
  for ld_path in os.environ.get("LD_LIBRARY_PATH", "").split(":"):
    if ld_path:
      paths.append(Path(ld_path))

  # 1. Direct path search
  for candidate in candidates:
    for path in paths:
      full = path / candidate
      if full.exists():
        LOG.debug(f"Found lib{name} at {full}")
        return str(full)

  # 2. ldconfig fallback (for .deb installed libs)
  for candidate in candidates:
    found = _ldconfig_search(candidate)
    if found:
      LOG.debug(f"Found lib{name} via ldconfig at {found}")
      return found

  return None


def load_library(name: str, extra_paths: list[str] | None = None) -> ctypes.CDLL:
  """Load a shared library, raising a clear error if missing.

  Args:
    name: Library base name (e.g. 'rga', 'rockchip_mpp', 'rknnrt', 'od_share')
    extra_paths: Additional directories to search

  Returns:
    Loaded ctypes.CDLL handle

  Raises:
    OSError: If library cannot be found
  """
  path = _find_library(name, extra_paths)
  if path is None:
    searched = [str(p) for p in _submodule_paths(name)] + _SYSTEM_PATHS[:3]
    deb_hint = ""
    if name in ("rga", "rockchip_mpp", "mpp"):
      deb_hint = (
        f"Install vendor package: sudo dpkg -i {name}*.deb\n"
        + "Or run: sudo ~/pilot/exopilot/scripts/install/install_rockchip_deps.sh"
      )
    elif name in ("od_share", "md_share", "RKAP_3A"):
      deb_hint = (
        "This library ships in third_party/rockchip_algo/. "
        + "Ensure the .so file is present."
      )
    raise OSError(
      f"Library 'lib{name}.so' not found. Searched: {searched}.\n"
      + f"{deb_hint}"
    )
  return ctypes.CDLL(path)


def try_load(name: str, extra_paths: list[str] | None = None) -> ctypes.CDLL | None:
  """Try to load a library, returning None if unavailable."""
  try:
    return load_library(name, extra_paths)
  except OSError:
    return None


def library_available(name: str) -> bool:
  """Check if a library is available without loading it."""
  return _find_library(name) is not None


def get_library_paths() -> dict[str, str | None]:
  """Get a dict of all found library paths for diagnostics."""
  all_names = ["rga", "rockchip_mpp", "rknnrt", "od_share", "md_share", "RKAP_3A"]
  return {name: _find_library(name) for name in all_names}
