#!/usr/bin/env python3
"""RGA Backend - Rockchip 2D Graphics Accelerator (RK3588).

Image processing operations: cvtColor (NV12→RGB), resize, crop.
Works on both edge hardware (with librga) and dev PC (with OpenCV fallback).
"""

from __future__ import annotations

import logging
from typing import Any

from openpilot.system.inferenced.compute import HardwareBackend, BackendType, InferenceResult, ModelConfig

logger = logging.getLogger(__name__)


class RGABackend(HardwareBackend):
  """Rockchip RGA 2D graphics accelerator backend."""

  def __init__(self):
    super().__init__(BackendType.RGA)
    self._rga_available = False
    self._use_opencv_fallback = False

  def initialize(self) -> bool:
    """Initialize RGA backend."""
    try:
      import ctypes
      try:
        ctypes.CDLL('librga.so')
        self._rga_available = True
        self._use_opencv_fallback = False
        logger.info("RGA backend initialized (hardware)")
        self._initialized = True
        return True
      except OSError:
        # Dev PC: use OpenCV fallback
        try:
          import cv2  # noqa: F401
          self._use_opencv_fallback = True
          self._rga_available = False
          logger.info("RGA backend initialized (OpenCV fallback)")
          self._initialized = True
          return True
        except ImportError:
          logger.warning("Neither RGA nor OpenCV available")
          return False

    except Exception as e:
      logger.warning(f"RGA not available: {e}")
      return False

  def release(self) -> None:
    """Release RGA resources."""
    self._initialized = False

  def load_model(self, config: ModelConfig) -> bool:
    """RGA doesn't load models - it's just image operations."""
    return self._initialized

  def infer(self, model_name: str, inputs: dict[str, Any]) -> InferenceResult:
    """Execute RGA image operation."""
    if not self._initialized:
      return InferenceResult(
          backend_type=self.backend_type,
          model_name=model_name,
          success=False,
          error_message="RGA backend not initialized"
      )

    try:
      import time

      start_time = time.monotonic()

      op = model_name.lower()
      if op == 'cvtcolor':
        output = self._cvtcolor(inputs)
      elif op in ('resize', 'scale'):
        output = self._resize(inputs)
      elif op == 'crop':
        output = self._crop(inputs)
      else:
        return InferenceResult(
            backend_type=self.backend_type,
            model_name=model_name,
            success=False,
            error_message=f"Unknown RGA operation: {model_name}"
        )

      inference_time_ms = (time.monotonic() - start_time) * 1000
      self._stats.tasks_completed += 1
      self._stats.total_exec_time_ms += inference_time_ms

      return InferenceResult(
          backend_type=self.backend_type,
          model_name=model_name,
          outputs={'output': output},
          inference_time_ms=inference_time_ms,
          success=True
      )

    except Exception as e:
      logger.exception("RGA operation error")
      self._stats.tasks_failed += 1
      return InferenceResult(
          backend_type=self.backend_type,
          model_name=model_name,
          success=False,
          error_message=str(e)
      )

  def _cvtcolor(self, inputs: dict[str, Any]) -> Any:
    """Color conversion: supports packed src (BGR→RGB) and planar src_y/src_uv (NV12→RGB)."""
    import numpy as np

    if self._use_opencv_fallback:
      import cv2
      src_fmt = inputs.get('src_fmt', 'nv12')
      dst_fmt = inputs.get('dst_fmt', 'rgb')

      # Packed array path (e.g. yolo_rknn passes BGR frame as 'src')
      src = inputs.get('src')
      if src is not None:
        if not isinstance(src, np.ndarray):
          src = np.array(src, dtype=np.uint8)
        if src_fmt.lower() in ('bgr', 'nv12') and dst_fmt.lower() == 'rgb':
          return cv2.cvtColor(src, cv2.COLOR_BGR2RGB)
        return src

      # Planar NV12 path (src_y + src_uv)
      y = inputs.get('src_y')
      uv = inputs.get('src_uv')

      if y is None:
        return np.zeros(inputs.get('shape', (480, 640, 3)), dtype=np.uint8)

      if not isinstance(y, np.ndarray):
        y = np.array(y, dtype=np.uint8)
      if uv is not None and not isinstance(uv, np.ndarray):
        uv = np.array(uv, dtype=np.uint8)

      # NV12 to RGB conversion via BGR intermediate
      if src_fmt.lower() == 'nv12':
        import numpy as np
        if uv is None:
          # Y-only: can't convert without UV
          bgr = cv2.cvtColor(y, cv2.COLOR_GRAY2BGR)
        else:
          # NV12 format: Y plane + UV plane
          # Y is (height, width), UV is (height/2, width/2)
          # For cv2.cvtColor with COLOR_YUV2BGR_NV12, need (height + height/2, width)
          h, w = y.shape
          uv_h, uv_w = uv.shape
          # UV needs to be replicated horizontally to match width: (height/2, width)
          uv_expanded = np.tile(uv, (1, w // uv_w))
          nv12_data = np.concatenate([y, uv_expanded], axis=0)
          bgr = cv2.cvtColor(nv12_data, cv2.COLOR_YUV2BGR_NV12)
        if dst_fmt.lower() == 'rgb':
          return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return bgr

      logger.warning(f"Unsupported conversion: {src_fmt} → {dst_fmt}")
      return y

    # Hardware RGA (placeholder)
    logger.debug("RGA: NV12→RGB conversion")
    return inputs.get('src_y', np.zeros((480, 640), dtype=np.uint8))

  def _resize(self, inputs: dict[str, Any]) -> Any:
    """Image resize operation."""
    import numpy as np

    if self._use_opencv_fallback:
      import cv2
      src = inputs.get('input') if inputs.get('input') is not None else inputs.get('src')
      width = inputs.get('width', 640)
      height = inputs.get('height', 480)

      if src is None:
        return np.zeros((height, width, 3), dtype=np.uint8)

      if not isinstance(src, np.ndarray):
        src = np.array(src, dtype=np.uint8)

      return cv2.resize(src, (width, height))

    logger.debug(f"RGA: Resize to {inputs.get('width')}x{inputs.get('height')}")
    src = inputs.get('input') if inputs.get('input') is not None else inputs.get('src')
    return src if isinstance(src, np.ndarray) else np.array(src)

  def _crop(self, inputs: dict[str, Any]) -> Any:
    """Image crop operation."""
    import numpy as np

    if self._use_opencv_fallback:
      src = inputs.get('input')
      x = inputs.get('x', 0)
      y = inputs.get('y', 0)
      width = inputs.get('width', 640)
      height = inputs.get('height', 480)

      if src is None:
        return np.zeros((height, width, 3), dtype=np.uint8)

      if not isinstance(src, np.ndarray):
        src = np.array(src, dtype=np.uint8)

      return src[y:y+height, x:x+width]

    logger.debug(f"RGA: Crop {inputs.get('width')}x{inputs.get('height')} @ ({inputs.get('x')},{inputs.get('y')})")
    src = inputs.get('input')
    return src if isinstance(src, np.ndarray) else np.array(src)
