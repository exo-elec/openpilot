#!/usr/bin/env python3
"""RGA (2D Graphics Accelerator) bindings for RK3588.

Hardware-accelerated image resize, crop, format conversion, rotation, flip.
Falls back to OpenCV when librga.so is unavailable.

Reference: third_party/rockchip_rga/include/im2d.h
"""

from __future__ import annotations

import ctypes
import logging
from enum import IntEnum
from typing import Any, cast

import numpy as np

from openpilot.system.hardware.rockchip._libloader import try_load

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class RGAFormat(IntEnum):
  RK_FORMAT_RGBA_8888 = 0x0 << 8
  RK_FORMAT_RGBX_8888 = 0x1 << 8
  RK_FORMAT_RGB_888 = 0x2 << 8
  RK_FORMAT_BGRA_8888 = 0x3 << 8
  RK_FORMAT_RGB_565 = 0x4 << 8
  RK_FORMAT_RGBA_5551 = 0x5 << 8
  RK_FORMAT_RGBA_4444 = 0x6 << 8
  RK_FORMAT_BGR_888 = 0x7 << 8
  RK_FORMAT_YCbCr_422_SP = 0x8 << 8
  RK_FORMAT_YCbCr_420_SP = 0xa << 8
  RK_FORMAT_YCrCb_420_SP = 0xe << 8


class IM_STATUS(IntEnum):
  IM_STATUS_NOERROR = 2
  IM_STATUS_SUCCESS = 1
  IM_STATUS_FAILED = 0


class IM_USAGE(IntEnum):
  IM_HAL_TRANSFORM_ROT_90 = 1 << 0
  IM_HAL_TRANSFORM_ROT_180 = 1 << 1
  IM_HAL_TRANSFORM_ROT_270 = 1 << 2
  IM_HAL_TRANSFORM_FLIP_H = 1 << 3
  IM_HAL_TRANSFORM_FLIP_V = 1 << 4
  IM_HAL_TRANSFORM_FLIP_H_V = 1 << 5


# ---------------------------------------------------------------------------
# C structures
# ---------------------------------------------------------------------------

class _ImRect(ctypes.Structure):
  _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int), ("width", ctypes.c_int), ("height", ctypes.c_int)]


class _RgaBufferT(ctypes.Structure):
  _fields_ = [
    ("vir_addr", ctypes.c_void_p), ("phy_addr", ctypes.c_void_p), ("fd", ctypes.c_int),
    ("width", ctypes.c_int), ("height", ctypes.c_int), ("wstride", ctypes.c_int), ("hstride", ctypes.c_int),
    ("format", ctypes.c_int), ("color_space_mode", ctypes.c_int), ("global_alpha", ctypes.c_int),
    ("rd_mode", ctypes.c_int), ("color", ctypes.c_int), ("colorkey_min", ctypes.c_int), ("colorkey_max", ctypes.c_int),
    ("nn_scale_r", ctypes.c_int), ("nn_scale_g", ctypes.c_int), ("nn_scale_b", ctypes.c_int),
    ("nn_offset_r", ctypes.c_int), ("nn_offset_g", ctypes.c_int), ("nn_offset_b", ctypes.c_int),
    ("rop_code", ctypes.c_int), ("handle", ctypes.c_uint32),
  ]


_FORMAT_MAP: dict[tuple[np.dtype, int, str], RGAFormat] = {
  (np.dtype(np.uint8), 4, "RGBA"): RGAFormat.RK_FORMAT_RGBA_8888,
  (np.dtype(np.uint8), 4, "BGRA"): RGAFormat.RK_FORMAT_BGRA_8888,
  (np.dtype(np.uint8), 3, "RGB"): RGAFormat.RK_FORMAT_RGB_888,
  (np.dtype(np.uint8), 3, "BGR"): RGAFormat.RK_FORMAT_BGR_888,
  (np.dtype(np.uint8), 2, "NV12"): RGAFormat.RK_FORMAT_YCbCr_420_SP,
  (np.dtype(np.uint8), 2, "NV21"): RGAFormat.RK_FORMAT_YCrCb_420_SP,
}


def _np_to_rga(image: np.ndarray, hint: str = "RGB") -> int:
  dtype = image.dtype
  channels = image.shape[2] if image.ndim == 3 else 1
  key = (dtype, channels, hint.upper())
  if key in _FORMAT_MAP:
    return _FORMAT_MAP[key].value
  if dtype == np.uint8:
    if channels == 4:
      return RGAFormat.RK_FORMAT_RGBA_8888.value
    elif channels == 3:
      return RGAFormat.RK_FORMAT_RGB_888.value
    elif channels == 2:
      return RGAFormat.RK_FORMAT_YCbCr_420_SP.value
  return RGAFormat.RK_FORMAT_RGB_888.value


def _opencv_resize(src: np.ndarray, dst_h: int, dst_w: int) -> np.ndarray:
  import cv2
  return cv2.resize(src, (dst_w, dst_h), interpolation=cv2.INTER_LINEAR)


def _opencv_cvt_color(src: np.ndarray, dst_format: str) -> np.ndarray:
  import cv2
  channels = src.shape[2] if src.ndim == 3 else 1
  df = dst_format.upper()
  if df == "RGB":
    if channels == 4:
      return cv2.cvtColor(src, cv2.COLOR_RGBA2RGB)
    if channels == 1:
      return cv2.cvtColor(src, cv2.COLOR_GRAY2RGB)
  elif df == "BGR":
    if channels == 4:
      return cv2.cvtColor(src, cv2.COLOR_RGBA2BGR)
    if channels == 1:
      return cv2.cvtColor(src, cv2.COLOR_GRAY2BGR)
  elif df == "GRAY":
    if channels == 3:
      return cv2.cvtColor(src, cv2.COLOR_RGB2GRAY)
    if channels == 4:
      return cv2.cvtColor(src, cv2.COLOR_RGBA2GRAY)
  elif df == "RGBA":
    if channels == 3:
      return cv2.cvtColor(src, cv2.COLOR_RGB2RGBA)
    if channels == 1:
      return cv2.cvtColor(src, cv2.COLOR_GRAY2RGBA)
  return src


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

class RGABackend:
  """RGA 2D accelerator backend."""

  def __init__(self) -> None:
    self._lib: ctypes.CDLL | None = None
    self._lib_path: str | None = None
    self._fn_wrap: Any = None
    self._fn_resize: Any = None
    self._fn_crop: Any = None
    self._fn_cvtcolor: Any = None
    self._fn_rotate: Any = None
    self._fn_flip: Any = None
    self._initialized = False

  def initialize(self) -> bool:
    """Load librga.so and bind functions."""
    try:
      self._lib = try_load("rga")
      if self._lib is None:
        return False
      self._lib_path = self._lib._name
      self._bind_functions()
      self._initialized = True
      LOG.info("RGA backend initialized (hardware)")
      return True
    except Exception as e:
      LOG.debug(f"RGA init error: {e}")
      return False

  def _bind_functions(self) -> None:
    lib = self._lib
    assert lib is not None

    try:
      fn = lib.wrapbuffer_virtualaddr_t
      fn.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
      fn.restype = _RgaBufferT
      self._fn_wrap = fn
    except AttributeError:
      LOG.warning("wrapbuffer_virtualaddr_t missing")

    for name, attr, argtypes in [
      ("imresize_t", "_fn_resize", [_RgaBufferT, _RgaBufferT, ctypes.c_double, ctypes.c_double, ctypes.c_int, ctypes.c_int]),
      ("imcrop_t", "_fn_crop", [_RgaBufferT, _RgaBufferT, _ImRect, ctypes.c_int]),
      ("imcvtcolor_t", "_fn_cvtcolor", [_RgaBufferT, _RgaBufferT, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]),
      ("imrotate_t", "_fn_rotate", [_RgaBufferT, _RgaBufferT, ctypes.c_int, ctypes.c_int]),
      ("imflip_t", "_fn_flip", [_RgaBufferT, _RgaBufferT, ctypes.c_int, ctypes.c_int]),
    ]:
      try:
        fn = getattr(lib, name)
        fn.argtypes = argtypes
        fn.restype = ctypes.c_int
        setattr(self, attr, fn)
      except AttributeError:
        LOG.warning(f"{name} missing in librga")

  def is_available(self) -> bool:
    return self._initialized

  def release(self) -> None:
    self._lib = None
    self._initialized = False

  def _check(self, status: int, op: str) -> bool:
    ok = status == IM_STATUS.IM_STATUS_SUCCESS or status == IM_STATUS.IM_STATUS_NOERROR
    if not ok:
      LOG.warning(f"RGA {op} failed: status={status}")
    return ok

  def _wrap(self, arr: np.ndarray, hint: str = "RGB") -> _RgaBufferT:
    assert self._fn_wrap is not None
    h, w = arr.shape[:2]
    stride = arr.strides[0]
    channels = arr.shape[2] if arr.ndim == 3 else 1
    wstride_px = stride // arr.itemsize
    if channels > 1:
      wstride_px = wstride_px // channels
    return cast(_RgaBufferT, self._fn_wrap(arr.ctypes.data, w, h, wstride_px, h, _np_to_rga(arr, hint)))

  # ------------------------------------------------------------------
  # Operations
  # ------------------------------------------------------------------

  def resize(self, src: np.ndarray, width: int, height: int) -> np.ndarray:
    """Hardware resize; falls back to OpenCV."""
    if len(src.shape) == 3:
      dst = np.empty((height, width, src.shape[2]), dtype=src.dtype)
    else:
      dst = np.empty((height, width), dtype=src.dtype)

    if self._fn_resize and self._fn_wrap:
      status = self._fn_resize(self._wrap(src), self._wrap(dst), 0.0, 0.0, 0, 1)
      if self._check(status, "resize"):
        return dst
    return _opencv_resize(src, height, width)

  def crop(self, src: np.ndarray, x: int, y: int, w: int, h: int) -> np.ndarray:
    """Hardware crop; falls back to numpy slicing."""
    if len(src.shape) == 3:
      dst = np.empty((h, w, src.shape[2]), dtype=src.dtype)
    else:
      dst = np.empty((h, w), dtype=src.dtype)

    if self._fn_crop and self._fn_wrap:
      status = self._fn_crop(self._wrap(src), self._wrap(dst), _ImRect(x, y, w, h), 1)
      if self._check(status, "crop"):
        return dst
    return src[y:y + h, x:x + w]

  def cvtColor(self, src: np.ndarray, dst_format: str) -> np.ndarray:
    """Hardware color conversion; falls back to OpenCV."""
    df = dst_format.upper()
    if df == "RGB":
      dc = 3
    elif df == "BGR":
      dc = 3
    elif df == "RGBA":
      dc = 4
    elif df == "BGRA":
      dc = 4
    elif df == "GRAY":
      dc = 1
    else:
      return _opencv_cvt_color(src, df)

    h, w = src.shape[:2]
    if dc > 1:
      dst = np.empty((h, w, dc), dtype=src.dtype)
    else:
      dst = np.empty((h, w), dtype=src.dtype)

    if self._fn_cvtcolor and self._fn_wrap:
      src_fmt = _np_to_rga(src, "RGB")
      dst_fmt = _np_to_rga(dst, df)
      status = self._fn_cvtcolor(self._wrap(src, "RGB"), self._wrap(dst, df), src_fmt, dst_fmt, 0, 1)
      if self._check(status, "cvtColor"):
        return dst
    return _opencv_cvt_color(src, df)

  def rotate(self, src: np.ndarray, angle: float) -> np.ndarray:
    """Hardware rotation (90/180/270 only); falls back to OpenCV."""
    h, w = src.shape[:2]
    if abs(angle - 90) < 5:
      rot = IM_USAGE.IM_HAL_TRANSFORM_ROT_90
      dst = np.empty((w, h, src.shape[2]) if src.ndim == 3 else (w, h), dtype=src.dtype)
    elif abs(angle - 180) < 5:
      rot = IM_USAGE.IM_HAL_TRANSFORM_ROT_180
      dst = np.empty_like(src)
    elif abs(angle - 270) < 5:
      rot = IM_USAGE.IM_HAL_TRANSFORM_ROT_270
      dst = np.empty((w, h, src.shape[2]) if src.ndim == 3 else (w, h), dtype=src.dtype)
    else:
      import cv2
      center = (w // 2, h // 2)
      M = cv2.getRotationMatrix2D(center, angle, 1.0)
      return cv2.warpAffine(src, M, (w, h))

    if self._fn_rotate and self._fn_wrap:
      status = self._fn_rotate(self._wrap(src), self._wrap(dst), rot, 1)
      if self._check(status, "rotate"):
        return dst
    import cv2
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(src, M, (w, h))

  def flip(self, src: np.ndarray, flip_code: int) -> np.ndarray:
    """Hardware flip; falls back to OpenCV.

    Args:
      flip_code: 1=horizontal, 0=vertical, -1=both
    """
    dst = np.empty_like(src)
    if flip_code == 1:
      mode = IM_USAGE.IM_HAL_TRANSFORM_FLIP_H
    elif flip_code == 0:
      mode = IM_USAGE.IM_HAL_TRANSFORM_FLIP_V
    elif flip_code == -1:
      mode = IM_USAGE.IM_HAL_TRANSFORM_FLIP_H_V
    else:
      import cv2
      return cv2.flip(src, flip_code)

    if self._fn_flip and self._fn_wrap:
      status = self._fn_flip(self._wrap(src), self._wrap(dst), mode, 1)
      if self._check(status, "flip"):
        return dst
    import cv2
    return cv2.flip(src, flip_code)

  def get_device_info(self) -> dict[str, str]:
    info: dict[str, str] = {"backend": "RGA"}
    if self._lib_path:
      info["library_path"] = self._lib_path
    return info
