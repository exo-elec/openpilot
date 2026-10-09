"""Temporary stub for params_pyx to allow pytest without compiled Cython extension.

WARNING: This file MUST be deleted before building with SCons.
The compiled .so will shadow it, but in some Python environments the .py
may be imported instead of the .so, causing silent behavior differences
(e.g. get_bool() returns False for missing keys instead of None).

To remove: rm common/params_pyx.py
"""
import os as _os
if not _os.environ.get("OPENPILOT_STUB_PARAMS_PYX"):
  import importlib.util as _ilu
  if _ilu.find_spec("common.params_pyx") and __file__.endswith(".py"):
    import warnings
    warnings.warn(
      "params_pyx.py stub is active — delete this file before production use",
      stacklevel=2,
    )
from typing import Any


class UnknownKeyName(Exception):
  pass


class ParamKeyFlag:
  PERSISTENT = 1
  CLEAR_ON_MANAGER_START = 2
  CLEAR_ON_ONROAD_TRANSITION = 4
  CLEAR_ON_OFFROAD_TRANSITION = 8
  CLEAR_ON_IGNITION_ON = 16
  DEVELOPMENT_ONLY = 32


class ParamKeyType:
  pass


class Params:
  def __init__(self, path: str = "") -> None:
    self._path = path
    self._data: dict[str, Any] = {}

  def get_param_path(self) -> str:
    import os
    return os.path.join("/tmp", "params_test", self._path)

  def check_key(self, key: str) -> bool:
    return True

  def get(self, key: str) -> bytes | None:
    val = self._data.get(key)
    return val.encode() if isinstance(val, str) else val

  def get_bool(self, key: str) -> bool:
    return self._data.get(key) == b"1"

  def put(self, key: str, val: str | bytes) -> None:
    self._data[key] = val.decode() if isinstance(val, bytes) else val

  def put_bool(self, key: str, val: bool) -> None:
    self._data[key] = "1" if val else "0"

  def put_nonblocking(self, key: str, val: str | bytes) -> None:
    self.put(key, val)

  def put_bool_nonblocking(self, key: str, val: bool) -> None:
    self.put_bool(key, val)

  def remove(self, key: str) -> None:
    self._data.pop(key, None)

  def cpp2python(self, key: str, value: bytes | str) -> Any:
    return value.decode() if isinstance(value, bytes) else value
