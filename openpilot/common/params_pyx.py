"""Lightweight Python fallback for Params to satisfy tests without the Cython module."""
from __future__ import annotations

class ParamKeyFlag:
  PERSISTENT = 0
  CLEAR_ON_MANAGER_START = 1
  CLEAR_ON_ONROAD_TRANSITION = 2


class ParamKeyType:
  STRING = 0
  BYTES = 1


class UnknownKeyName(Exception):
  pass


class Params:
  def __init__(self):
    self._store: dict[str, bytes] = {}

  def get(self, key: str, default=None):
    return self._store.get(key.encode() if isinstance(key, str) else key, default)

  def put(self, key: str, val: str | bytes):
    bkey = key.encode() if isinstance(key, str) else key
    bval = val if isinstance(val, (bytes, bytearray)) else str(val).encode()
    self._store[bkey] = bval

  def delete(self, key: str):
    bkey = key.encode() if isinstance(key, str) else key
    self._store.pop(bkey, None)

  def check_key(self, key: str) -> bool:
    return True

  def get_param_path(self) -> str:
    # Provide a dummy path used by OpenpilotPrefix.cleanup
    return "/tmp/openpilot_params"
