#!/usr/bin/env python3
"""Video utilities: NV12 conversion, track ID helpers."""

import logging

import cv2
import numpy as np

logger = logging.getLogger("SteamD.video_utils")

# RGA will be initialized per-instance to avoid module-level memory leaks
_rga = None


def _get_rga():
  """Lazy-init RGA client to avoid module-level memory leak."""
  global _rga
  if _rga is None:
    try:
      from openpilot.system.inferenced.client import InferenceClient
      client = InferenceClient("steamd")
      _rga = client.rga()
      logger.info("RGA available for NV12 conversion")
    except Exception:
      _rga = None
  return _rga


def nv12_to_rgb(data: bytes, width: int, height: int) -> np.ndarray:
  """Convert NV12 buffer to RGB numpy array.

  Uses RGA hardware acceleration if available, otherwise falls back to
  CPU conversion via OpenCV.
  """
  # Try RGA hardware conversion first (lazy init)
  rga = _get_rga()
  if rga is not None:
    try:
      y_size = height * width
      uv_size = height * width // 2
      y = np.frombuffer(data[:y_size], dtype=np.uint8).reshape((height, width))
      uv = np.frombuffer(data[y_size:y_size + uv_size], dtype=np.uint8).reshape((height // 2, width))

      result = rga.infer(
        model_name='cvtColor',
        inputs={'src_y': y, 'src_uv': uv, 'width': width, 'height': height, 'src_fmt': 'nv12', 'dst_fmt': 'rgb'}
      )
      if result.success:
        return result.outputs['output']
    except Exception:
      pass  # Fall back to CPU

  # CPU fallback: cv2-based NV12→RGB
  y_size = height * width
  uv_size = height * width // 2

  y = np.frombuffer(data[:y_size], dtype=np.uint8).reshape((height, width))
  uv = np.frombuffer(data[y_size:y_size + uv_size], dtype=np.uint8).reshape((height // 2, width))

  uv_upsampled = cv2.resize(uv, (width, height), interpolation=cv2.INTER_LINEAR)

  yuv = np.zeros((height, width, 3), dtype=np.uint8)
  yuv[:, :, 0] = y
  yuv[:, :, 1] = uv_upsampled
  yuv[:, :, 2] = uv_upsampled

  return cv2.cvtColor(yuv, cv2.COLOR_YUV2RGB_NV12)


def video_track_id(camera_type: str, track_id: str) -> str:
  """Generate video track ID with camera type prefix."""
  return f"{camera_type}:{track_id}"


def parse_video_track_id(track_id: str) -> tuple[str, str]:
  """Parse camera type and track ID from video track ID."""
  parts = track_id.split(":")
  if len(parts) != 2:
    raise ValueError(f"Invalid video track id: {track_id}")
  return parts[0], parts[1]
