#!/usr/bin/env python3
"""MPP (Media Process Platform) bindings for RK3588.

Hardware video decoder/encoder via librockchip_mpp.so.
Falls back to software JPEG encode/decode when unavailable.

Reference: third_party/rockchip_mpp/inc/rk_mpi.h
"""

from __future__ import annotations

import ctypes
import logging
from enum import IntEnum

import numpy as np

from openpilot.system.hardware.rockchip._libloader import try_load

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class MPPCodec(IntEnum):
  H264 = 7
  H265 = 8
  MJPEG = 9


MPP_OK = 0


# ---------------------------------------------------------------------------
# C types
# ---------------------------------------------------------------------------

MppCtx = ctypes.c_void_p
MppApi_p = ctypes.c_void_p
MppPacket = ctypes.c_void_p
MppFrame = ctypes.c_void_p


class _MppApi(ctypes.Structure):
  _fields_ = [
    ("size", ctypes.c_uint32), ("version", ctypes.c_uint32),
    ("decode", ctypes.CFUNCTYPE(ctypes.c_int, MppCtx, MppPacket, ctypes.POINTER(MppFrame))),
    ("decode_put_packet", ctypes.CFUNCTYPE(ctypes.c_int, MppCtx, MppPacket)),
    ("decode_get_frame", ctypes.CFUNCTYPE(ctypes.c_int, MppCtx, ctypes.POINTER(MppFrame))),
    ("encode", ctypes.CFUNCTYPE(ctypes.c_int, MppCtx, MppFrame, ctypes.POINTER(MppPacket))),
    ("encode_put_frame", ctypes.CFUNCTYPE(ctypes.c_int, MppCtx, MppFrame)),
    ("encode_get_packet", ctypes.CFUNCTYPE(ctypes.c_int, MppCtx, ctypes.POINTER(MppPacket))),
    ("isp", ctypes.CFUNCTYPE(ctypes.c_int, MppCtx, MppFrame, MppFrame)),
    ("isp_put_frame", ctypes.CFUNCTYPE(ctypes.c_int, MppCtx, MppFrame)),
    ("isp_get_frame", ctypes.CFUNCTYPE(ctypes.c_int, MppCtx, ctypes.POINTER(MppFrame))),
    ("control", ctypes.CFUNCTYPE(ctypes.c_int, MppCtx, ctypes.c_int32, ctypes.c_void_p)),
    ("reset", ctypes.CFUNCTYPE(ctypes.c_int, MppCtx)),
  ]


# ---------------------------------------------------------------------------
# Low-level library
# ---------------------------------------------------------------------------

class _MPPLib:
  def __init__(self) -> None:
    lib = try_load("rockchip_mpp")
    if lib is None:
      raise OSError("librockchip_mpp.so not found")
    self._lib: ctypes.CDLL = lib
    self._setup()

  def _setup(self) -> None:
    lib = self._lib
    lib.mpp_create.argtypes = [ctypes.POINTER(MppCtx), ctypes.POINTER(MppApi_p)]
    lib.mpp_create.restype = ctypes.c_int
    lib.mpp_init.argtypes = [MppCtx, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
    lib.mpp_init.restype = ctypes.c_int
    lib.mpp_destroy.argtypes = [MppCtx]
    lib.mpp_destroy.restype = ctypes.c_int
    lib.mpp_packet_init.argtypes = [ctypes.POINTER(MppPacket), ctypes.c_void_p, ctypes.c_size_t]
    lib.mpp_packet_init.restype = ctypes.c_int
    lib.mpp_packet_deinit.argtypes = [ctypes.POINTER(MppPacket)]
    lib.mpp_packet_deinit.restype = ctypes.c_int
    lib.mpp_frame_init.argtypes = [ctypes.POINTER(MppFrame)]
    lib.mpp_frame_init.restype = ctypes.c_int
    lib.mpp_frame_deinit.argtypes = [ctypes.POINTER(MppFrame)]
    lib.mpp_frame_deinit.restype = ctypes.c_int

    # Frame geometry and buffer access. Without these an MppFrame is an opaque
    # handle and decoded pixels are unreachable from Python -- which is why the
    # DVR path had no way to display a frame. Bound as optional: an older
    # librockchip_mpp may not export all of them, and a missing symbol should
    # degrade the decoder rather than fail the import for every consumer of
    # this module (inferenced included).
    for name, argtypes, restype in (
      ("mpp_frame_get_buffer", [MppFrame], ctypes.c_void_p),
      ("mpp_frame_get_width", [MppFrame], ctypes.c_uint32),
      ("mpp_frame_get_height", [MppFrame], ctypes.c_uint32),
      ("mpp_frame_get_hor_stride", [MppFrame], ctypes.c_uint32),
      ("mpp_frame_get_ver_stride", [MppFrame], ctypes.c_uint32),
      ("mpp_frame_get_errinfo", [MppFrame], ctypes.c_uint32),
      ("mpp_buffer_get_ptr_with_caller", [ctypes.c_void_p, ctypes.c_char_p], ctypes.c_void_p),
      ("mpp_buffer_get_size_with_caller", [ctypes.c_void_p, ctypes.c_char_p], ctypes.c_size_t),
    ):
      fn = getattr(lib, name, None)
      if fn is not None:
        fn.argtypes = argtypes
        fn.restype = restype

  @property
  def handle(self) -> ctypes.CDLL:
    return self._lib


_mpp_lib: _MPPLib | None = None


def _get_lib() -> _MPPLib:
  global _mpp_lib
  if _mpp_lib is None:
    _mpp_lib = _MPPLib()
  return _mpp_lib


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

class MPPDecoderConfig:
  def __init__(self, codec: MPPCodec = MPPCodec.H264) -> None:
    self.codec = codec


class MPPEncoderConfig:
  def __init__(self, codec: MPPCodec = MPPCodec.H264, width: int = 1920, height: int = 1080) -> None:
    self.codec = codec
    self.width = width
    self.height = height


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

class MPPBackend:
  """MPP video codec backend with JPEG fallback."""

  def __init__(self) -> None:
    self._lib: _MPPLib | None = None
    self._decoders: dict[str, MppCtx] = {}
    self._encoders: dict[str, MppCtx] = {}
    self._initialized = False

  def initialize(self) -> bool:
    try:
      self._lib = _get_lib()
      self._initialized = True
      LOG.info("MPP backend initialized")
      return True
    except OSError:
      return False

  def is_available(self) -> bool:
    return self._initialized

  def release(self) -> None:
    for ctx in list(self._decoders.values()):
      if self._lib:
        self._lib.handle.mpp_destroy(ctx)
    for ctx in list(self._encoders.values()):
      if self._lib:
        self._lib.handle.mpp_destroy(ctx)
    self._decoders.clear()
    self._encoders.clear()
    self._initialized = False

  def create_decoder(self, name: str, config: MPPDecoderConfig) -> bool:
    if not self._lib:
      return False
    ctx = MppCtx()
    api = MppApi_p()
    ret = self._lib.handle.mpp_create(ctypes.byref(ctx), ctypes.byref(api))
    if ret != MPP_OK:
      return False
    ret = self._lib.handle.mpp_init(ctx, 0, config.codec, 0)
    if ret != MPP_OK:
      self._lib.handle.mpp_destroy(ctx)
      return False
    self._decoders[name] = ctx
    return True

  def create_encoder(self, name: str, config: MPPEncoderConfig) -> bool:
    if not self._lib:
      return False
    ctx = MppCtx()
    api = MppApi_p()
    ret = self._lib.handle.mpp_create(ctypes.byref(ctx), ctypes.byref(api))
    if ret != MPP_OK:
      return False
    ret = self._lib.handle.mpp_init(ctx, 1, config.codec, 0)
    if ret != MPP_OK:
      self._lib.handle.mpp_destroy(ctx)
      return False
    self._encoders[name] = ctx
    return True

  # ------------------------------------------------------------------
  # JPEG fallback (software, always works)
  # ------------------------------------------------------------------

  def encode_jpeg(self, image: np.ndarray, quality: int = 90) -> bytes:
    """Encode image to JPEG bytes."""
    import cv2
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
      raise RuntimeError("JPEG encode failed")
    return buf.tobytes()

  def decode_jpeg(self, data: bytes) -> np.ndarray:
    """Decode JPEG bytes to image."""
    import cv2
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
      raise RuntimeError("JPEG decode failed")
    return img

  def frame_to_nv12(self, frame) -> tuple[np.ndarray, int, int, int] | None:
    """Map a decoded MppFrame and return (buffer, width, height, stride).

    The buffer is a *view* of MPP's memory, not a copy -- valid only until the
    frame is released, so callers must convert or copy before doing so. mpp's
    ptr/size accessors are the `_with_caller` variants; the plain names are
    macros in the C header and are not exported symbols.
    """
    if not self._lib:
      return None
    lib = self._lib.handle
    get_buf = getattr(lib, "mpp_frame_get_buffer", None)
    get_ptr = getattr(lib, "mpp_buffer_get_ptr_with_caller", None)
    if get_buf is None or get_ptr is None:
      return None

    errinfo = getattr(lib, "mpp_frame_get_errinfo", None)
    if errinfo is not None and errinfo(frame):
      return None

    buf = get_buf(frame)
    if not buf:
      return None
    ptr = get_ptr(buf, b"eop_ui")
    if not ptr:
      return None

    width = int(lib.mpp_frame_get_width(frame))
    height = int(lib.mpp_frame_get_height(frame))
    stride = int(lib.mpp_frame_get_hor_stride(frame)) or width
    v_stride = int(lib.mpp_frame_get_ver_stride(frame)) or height
    if width <= 0 or height <= 0:
      return None

    nbytes = stride * v_stride * 3 // 2   # NV12
    array_type = ctypes.c_uint8 * nbytes
    view = np.frombuffer(array_type.from_address(ptr), dtype=np.uint8)
    return view, width, height, stride

  def get_device_info(self) -> dict[str, str]:
    info: dict[str, str] = {"backend": "MPP"}
    if self._lib:
      info["library_path"] = self._lib.handle._name
    return info
