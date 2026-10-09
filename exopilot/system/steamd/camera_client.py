#!/usr/bin/env python3
"""VisionIPC camera client for multi-view streaming."""

import logging
from dataclasses import dataclass

import numpy as np

from msgq.visionipc import VisionIpcClient, VisionStreamType

logger = logging.getLogger("SteamD.camera_client")


@dataclass
class StreamConfig:
  output_width: int = 1280    # per-eye
  output_height: int = 720
  fps: int = 30
  bitrate_kbps: int = 4000
  target_addr: str = ""       # unicast target IP (headset)
  target_port: int = 5120
  encoder: str = "libx264"    # h264_rkmpp on RK3588
  preset: str = "ultrafast"
  tune: str = "zerolatency"
  gop: int = 30


class MultiCameraClient:
  """Manages VisionIPC connections for road, wide, side, and rear cameras."""

  STREAM_MAP = {
    "wide":   VisionStreamType.VISION_STREAM_WIDE_ROAD,
    "tele":   VisionStreamType.VISION_STREAM_TELE_ROAD,
    "left":   VisionStreamType.VISION_STREAM_SIDE_LEFT,
    "right":  VisionStreamType.VISION_STREAM_SIDE_RIGHT,
    "rear":   VisionStreamType.VISION_STREAM_REAR,
    "road":   VisionStreamType.VISION_STREAM_ROAD,
  }

  def __init__(self):
    self._clients: dict[str, VisionIpcClient | None] = {}
    self._stereo_clients: dict[str, tuple[VisionIpcClient | None, VisionIpcClient | None]] = {}

  def discover(self) -> dict[str, bool]:
    """Try to connect all cameras. Returns availability map."""
    available = {}
    for name, stream_type in self.STREAM_MAP.items():
      client = self._try_connect(stream_type)
      if client is not None:
        self._clients[name] = client
        available[name] = True
      else:
        available[name] = False
    # Tele camera
    tele = self._try_connect(VisionStreamType.VISION_STREAM_TELE_ROAD)
    if tele is not None:
      self._clients["tele"] = tele
      available["tele"] = True
    else:
      available["tele"] = False

    # Stereo pair: road + wide for side-by-side
    left = self._try_connect(VisionStreamType.VISION_STREAM_STEREO_LEFT)
    right = self._try_connect(VisionStreamType.VISION_STREAM_STEREO_RIGHT)
    if left and right:
      self._stereo_clients["stereo"] = (left, right)
      available["stereo"] = True
    else:
      available["stereo"] = False
    return available

  def _try_connect(self, stream: VisionStreamType) -> VisionIpcClient | None:
    try:
      client = VisionIpcClient("steamd", stream, True)
      if client.connect(False):
        return client
      client.close()
    except Exception:
      pass
    return None

  def recv(self, view_mode: str) -> tuple:
    """Receive frame(s) for the given view mode."""
    if view_mode == "stereo" and "stereo" in self._stereo_clients:
      left_c, right_c = self._stereo_clients["stereo"]
      try:
        buf_l = left_c.recv(timeout_ms=100)
        buf_r = right_c.recv(timeout_ms=100)
        if buf_l and buf_r:
          img_l = np.frombuffer(buf_l.data, dtype=np.uint8).reshape((buf_l.height, buf_l.width, 3))
          img_r = np.frombuffer(buf_r.data, dtype=np.uint8).reshape((buf_r.height, buf_r.width, 3))
          return img_l, img_r
      except Exception:
        pass
      return None, None

    client = self._clients.get(view_mode)
    if client is not None:
      try:
        buf = client.recv(timeout_ms=100)
        if buf:
          img = np.frombuffer(buf.data, dtype=np.uint8).reshape((buf.height, buf.width, 3))
          return img, None
      except Exception:
        pass
    return None, None

  def close(self):
    for c in self._clients.values():
      if c:
        try:
          c.close()
        except Exception:
          pass
    for left, right in self._stereo_clients.values():
      for c in (left, right):
        if c:
          try:
            c.close()
          except Exception:
            pass
