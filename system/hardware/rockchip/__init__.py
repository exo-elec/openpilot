#!/usr/bin/env python3
"""Rockchip hardware ctypes bindings (RK3588 / ExoPilot 01M).

Loads vendor shared libraries from submodule prebuilts or system paths at runtime.
No heavy SDK content is stored in the repo.

Usage:
  from openpilot.system.hardware.rockchip import RockchipBackendFactory
  rknn = RockchipBackendFactory.create("rknn")
  rga = RockchipBackendFactory.create("rga")
"""

from openpilot.system.hardware.rockchip.rga import RGABackend
from openpilot.system.hardware.rockchip.mpp import MPPBackend, MPPCodec, MPPDecoderConfig, MPPEncoderConfig
from openpilot.system.hardware.rockchip.rknn import RKNNBackend
from openpilot.system.hardware.rockchip.factory import RockchipBackendFactory

__all__ = [
  "RGABackend",
  "MPPBackend",
  "MPPCodec",
  "MPPDecoderConfig",
  "MPPEncoderConfig",
  "RKNNBackend",
  "RockchipBackendFactory",
]
