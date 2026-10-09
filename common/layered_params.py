#!/usr/bin/env python3
"""LayeredParams — Topic 06 integration.

Provides a hierarchical parameter store with automatic fallback across
multiple storage layers (e.g., memory → cache → persistent).

Typical layer configuration:
  LayeredParams([
      ("/dev/shm/params", True),   # Runtime / memory-backed (fastest, volatile)
      ("/cache/params", True),     # Cache / semi-persistent
      ("", False),                 # Default persistent (read-only fallback)
  ])

Reads traverse layers top-to-bottom, returning the first hit.
Writes go to the first writable layer.
"""

from openpilot.common.params import Params


class LayeredParams:
  """Multi-layer parameter store with read fallback and selective writes."""

  def __init__(self, layers: list[tuple[str, bool]]):
    """
    Args:
      layers: List of (path, writable) tuples. Path is passed to Params();
              empty string means default path. Writable=True means put/delete
              operations target this layer.
    """
    self._layers: list[tuple[Params, bool]] = []
    for path, writable in layers:
      self._layers.append((Params(path), writable))

  def get(self, key: str, block: bool = False):
    """Get param from first layer that has a value."""
    for params, _ in self._layers:
      val = params.get(key, block=block)
      if val is not None:
        return val
    return None

  def get_bool(self, key: str, block: bool = False) -> bool | None:
    for params, _ in self._layers:
      val = params.get_bool(key, block=block)
      if val is not None:
        return val
    return None

  def get_float(self, key: str, block: bool = False) -> float | None:
    for params, _ in self._layers:
      val = params.get(key, block=block)
      if val is not None:
        try:
          return float(val)
        except (ValueError, TypeError):
          continue
    return None

  def get_int(self, key: str, block: bool = False) -> int | None:
    for params, _ in self._layers:
      val = params.get(key, block=block)
      if val is not None:
        try:
          return int(val)
        except (ValueError, TypeError):
          continue
    return None

  def put(self, key: str, value: str):
    """Write param to the first writable layer."""
    for params, writable in self._layers:
      if writable:
        params.put(key, value)
        return
    raise RuntimeError(f"No writable layer available for param {key}")

  def put_bool(self, key: str, value: bool):
    self.put(key, "1" if value else "0")

  def put_nonblocking(self, key: str, value: str):
    """Write param non-blocking to the first writable layer."""
    for params, writable in self._layers:
      if writable:
        params.put_nonblocking(key, value)
        return
    raise RuntimeError(f"No writable layer available for param {key}")

  def remove(self, key: str):
    """Remove param from the first writable layer."""
    for params, writable in self._layers:
      if writable:
        params.remove(key)
        return
    raise RuntimeError(f"No writable layer available for param {key}")

  def clear_all(self):
    """Clear all writable layers."""
    for params, writable in self._layers:
      if writable:
        params.clear_all()
