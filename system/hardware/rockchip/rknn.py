#!/usr/bin/env python3
"""RKNN (NPU Runtime) bindings for RK3588.

Loads librknnrt.so from submodule prebuilts or system paths.
Provides model load + inference with timing.

No full rknpu2 submodule required in repo — just the runtime .so.
"""

from __future__ import annotations

import ctypes
import logging
import numpy as np
from pathlib import Path
from typing import Any

from openpilot.system.hardware.rockchip._libloader import try_load

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RKNN_SUCC = 0
RKNN_QUERY_IN_OUT_NUM = 0
RKNN_QUERY_INPUT_ATTR = 1
RKNN_QUERY_OUTPUT_ATTR = 2
RKNN_QUERY_PERF_RUN = 6

RKNN_TENSOR_UINT8 = 3
RKNN_TENSOR_FLOAT32 = 0


# ---------------------------------------------------------------------------
# C structures
# ---------------------------------------------------------------------------

class _RknnTensorAttr(ctypes.Structure):
  _fields_ = [
    ("index", ctypes.c_uint32), ("n_dims", ctypes.c_uint32), ("dims", ctypes.c_uint32 * 16),
    ("name", ctypes.c_char * 256), ("n_elems", ctypes.c_uint32), ("size", ctypes.c_uint32),
    ("fmt", ctypes.c_uint32), ("type", ctypes.c_uint32), ("qnt_type", ctypes.c_uint8),
    ("fl", ctypes.c_int8), ("zp", ctypes.c_int32), ("scale", ctypes.c_float),
    ("w_stride", ctypes.c_uint32), ("size_with_stride", ctypes.c_uint32),
    ("pass_through", ctypes.c_uint8), ("h_stride", ctypes.c_uint32),
  ]


class _RknnInput(ctypes.Structure):
  _fields_ = [("index", ctypes.c_uint32), ("buf", ctypes.c_void_p), ("size", ctypes.c_uint32),
              ("pass_through", ctypes.c_uint8), ("fmt", ctypes.c_uint32), ("type", ctypes.c_uint32)]


class _RknnOutput(ctypes.Structure):
  _fields_ = [("want_float", ctypes.c_uint8), ("is_prealloc", ctypes.c_uint8), ("index", ctypes.c_uint32),
              ("buf", ctypes.c_void_p), ("size", ctypes.c_uint32)]


class _RknnPerfRun(ctypes.Structure):
  _fields_ = [("run_duration", ctypes.c_int64)]


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

class RKNNBackend:
  """RK3588 NPU runtime backend."""

  def __init__(self) -> None:
    self._lib: ctypes.CDLL | None = None
    self._lib_path: str | None = None
    self._ctx = ctypes.c_void_p()
    self._input_attrs: list[_RknnTensorAttr] = []
    self._output_attrs: list[_RknnTensorAttr] = []
    self._initialized = False

  def initialize(self) -> bool:
    try:
      self._lib = try_load("rknnrt")
      if self._lib is None:
        return False
      self._lib_path = self._lib._name
      self._bind_functions()
      self._initialized = True
      LOG.info("RKNN backend initialized")
      return True
    except Exception as e:
      LOG.debug(f"RKNN init error: {e}")
      return False

  def _bind_functions(self) -> None:
    lib = self._lib
    assert lib is not None
    lib.rknn_init.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    lib.rknn_init.restype = ctypes.c_int
    lib.rknn_destroy.argtypes = [ctypes.c_void_p]
    lib.rknn_destroy.restype = ctypes.c_int
    lib.rknn_query.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
    lib.rknn_query.restype = ctypes.c_int
    lib.rknn_inputs_set.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_RknnInput)]
    lib.rknn_inputs_set.restype = ctypes.c_int
    lib.rknn_run.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.rknn_run.restype = ctypes.c_int
    lib.rknn_outputs_get.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_RknnOutput), ctypes.c_void_p]
    lib.rknn_outputs_get.restype = ctypes.c_int
    lib.rknn_outputs_release.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_RknnOutput)]
    lib.rknn_outputs_release.restype = ctypes.c_int

  def is_available(self) -> bool:
    return self._initialized

  def release(self) -> None:
    if self._lib and self._ctx:
      self._lib.rknn_destroy(self._ctx)
      self._ctx = ctypes.c_void_p()
    self._initialized = False

  def load_model(self, path: str | Path) -> bool:
    if not self._lib:
      return False
    data = Path(path).read_bytes()
    buf = ctypes.create_string_buffer(data)
    ret = self._lib.rknn_init(ctypes.byref(self._ctx), ctypes.cast(buf, ctypes.c_void_p), len(data), 0, None)
    if ret != RKNN_SUCC:
      return False
    self._query_tensors()
    return True

  def _query_tensors(self) -> None:
    assert self._lib is not None
    in_out = (ctypes.c_uint32 * 2)()
    self._lib.rknn_query(self._ctx, RKNN_QUERY_IN_OUT_NUM, None, 0, ctypes.cast(in_out, ctypes.c_void_p), ctypes.sizeof(in_out))
    n_in, n_out = in_out[0], in_out[1]
    self._input_attrs = []
    self._output_attrs = []
    for i in range(n_in):
      attr = _RknnTensorAttr()
      attr.index = i
      self._lib.rknn_query(self._ctx, RKNN_QUERY_INPUT_ATTR, ctypes.byref(attr), ctypes.sizeof(attr), None, 0)
      self._input_attrs.append(attr)
    for i in range(n_out):
      attr = _RknnTensorAttr()
      attr.index = i
      self._lib.rknn_query(self._ctx, RKNN_QUERY_OUTPUT_ATTR, ctypes.byref(attr), ctypes.sizeof(attr), None, 0)
      self._output_attrs.append(attr)

  def infer(self, inputs: list[np.ndarray]) -> list[np.ndarray]:
    assert self._lib is not None
    rknn_inputs = []
    for i, arr in enumerate(inputs):
      if not arr.flags['C_CONTIGUOUS']:
        arr = np.ascontiguousarray(arr)
      inp = _RknnInput()
      inp.index = i
      inp.buf = ctypes.c_void_p(arr.ctypes.data)
      inp.size = arr.nbytes
      inp.pass_through = 0
      inp.fmt = 0
      inp.type = RKNN_TENSOR_UINT8 if arr.dtype == np.uint8 else RKNN_TENSOR_FLOAT32
      rknn_inputs.append(inp)

    n = len(rknn_inputs)
    arr_type = _RknnInput * n
    self._lib.rknn_inputs_set(self._ctx, n, arr_type(*rknn_inputs))
    self._lib.rknn_run(self._ctx, None)

    n_out = len(self._output_attrs)
    outputs = []
    rknn_outputs = []
    for i in range(n_out):
      out = _RknnOutput()
      out.want_float = 1
      out.is_prealloc = 0
      out.index = i
      out.buf = ctypes.c_void_p(0)
      out.size = 0
      rknn_outputs.append(out)
      outputs.append(np.empty(self._output_attrs[i].n_elems, dtype=np.float32))

    out_arr = (_RknnOutput * n_out)(*rknn_outputs)
    self._lib.rknn_outputs_get(self._ctx, n_out, out_arr, None)

    result = []
    for i in range(n_out):
      ptr = ctypes.cast(out_arr[i].buf, ctypes.POINTER(ctypes.c_float))
      arr = np.ctypeslib.as_array(ptr, shape=(self._output_attrs[i].n_elems,))
      result.append(arr.copy())

    self._lib.rknn_outputs_release(self._ctx, n_out, out_arr)
    return result

  def perf_run(self) -> int:
    if not self._lib or not self._ctx:
      return 0
    perf = _RknnPerfRun()
    ret = self._lib.rknn_query(self._ctx, RKNN_QUERY_PERF_RUN, None, 0, ctypes.byref(perf), ctypes.sizeof(perf))
    return perf.run_duration if ret == RKNN_SUCC else 0

  def get_device_info(self) -> dict[str, Any]:
    info: dict[str, Any] = {"backend": "RKNN", "library_path": self._lib_path}
    return info

  def __enter__(self):
    return self

  def __exit__(self, *args):
    self.release()
