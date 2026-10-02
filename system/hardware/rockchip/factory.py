#!/usr/bin/env python3
"""Backend factory for Rockchip RK3588 hardware accelerators.

Discovers and creates RGA, MPP, RKNN backends in priority order.
"""

from __future__ import annotations

from typing import Any, Protocol

from openpilot.system.hardware.rockchip.rga import RGABackend
from openpilot.system.hardware.rockchip.mpp import MPPBackend
from openpilot.system.hardware.rockchip.rknn import RKNNBackend


class _Backend(Protocol):
  """Minimal protocol for factory-created Rockchip backends."""

  def initialize(self) -> bool: ...
  def release(self) -> None: ...


class RockchipBackendFactory:
  """Factory for RK3588 hardware backends."""

  _backends: dict[str, type[_Backend]] = {
    "rknn": RKNNBackend,
    "rga": RGABackend,
    "mpp": MPPBackend,
  }

  @classmethod
  def create(cls, name: str) -> Any | None:
    """Create a backend instance by name."""
    klass = cls._backends.get(name)
    if klass is None:
      return None
    inst = klass()
    return inst if inst.initialize() else None

  @classmethod
  def list_available(cls) -> list[str]:
    """List backends that successfully initialized."""
    available = []
    for name, klass in cls._backends.items():
      inst = klass()
      if inst.initialize():
        available.append(name)
        inst.release()
    return available

  @classmethod
  def get_default_npu(cls) -> RKNNBackend | None:
    """Get the best available NPU backend."""
    return cls.create("rknn")
