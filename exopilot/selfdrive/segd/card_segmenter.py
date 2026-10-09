#!/usr/bin/env python3
"""
Drivable-area segmentation on the camera-tier card (Hailo-8 or DX-M1M).

A thin IPC client: frames go to inferenced as BackendType.ACCEL jobs, which
route them to whichever card is fitted, run TwinLiteNet+ Large (the same
network on both cards; system/inferenced/drivable.py) and return an OTHER / DRIVABLE /
LANE map at the transport size below. The card has one owner, inferenced;
this client never opens it in-process.

Used by segd (every camera -> monoSegments) and by gridd (road mask for the
occupancy grid). No fallback: when the card fails, segmentation degrades.
"""

from __future__ import annotations

import cv2
import numpy as np

from openpilot.common.swaglog import cloudlog
from openpilot.system.inferenced.client import InferenceClient
from openpilot.system.inferenced.compute import BackendType

# Frame size sent to inferenced, and size of the class map that comes back.
# Half of 1080p per side: 1.5 MB per job over msgq instead of 6.2 MB.
SEG_TRANSPORT_HW = (540, 960)

# Values in the returned map (system/inferenced/drivable.py)
OTHER, DRIVABLE, LANE = 0, 1, 2


class CardSegmenter:
  """One camera's segmentation route on the card."""

  POLL_TIMEOUT_MS = 120.0

  def __init__(self, model_name: str, daemon_name: str, priority: int = 2,
               poll_timeout_ms: float | None = None, client: InferenceClient | None = None) -> None:
    self.model_name = model_name
    self.priority = priority
    self.poll_timeout_ms = poll_timeout_ms or self.POLL_TIMEOUT_MS
    self._client = client or InferenceClient(daemon_name, use_ipc=True)
    # Optimistic: switched off by inferenced's first "not available" answer
    # (no card, or no artifact for it), so a board without a card costs one job.
    self._available = True

  @property
  def is_available(self) -> bool:
    return self._available

  @staticmethod
  def preprocess(frame_bgr: np.ndarray) -> np.ndarray:
    h, w = SEG_TRANSPORT_HW
    rgb = cv2.cvtColor(cv2.resize(frame_bgr, (w, h), interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)
    return np.expand_dims(rgb, axis=0)  # NHWC uint8

  def segment(self, frame_bgr: np.ndarray | None) -> np.ndarray | None:
    """OTHER/DRIVABLE/LANE map (SEG_TRANSPORT_HW, uint8), or None when the card has no answer."""
    if frame_bgr is None or not self._available:
      return None
    try:
      result = self._client.submit_job(
        backend_type=BackendType.ACCEL,
        model_name=self.model_name,
        input_array=self.preprocess(frame_bgr),
        priority=self.priority,
        poll_timeout_ms=self.poll_timeout_ms,
        allow_direct_fallback=False,
      )
    except Exception as e:
      cloudlog.debug(f"CardSegmenter[{self.model_name}]: {e}")
      return None
    if not result.success:
      if result.error_message and 'not available' in result.error_message:
        self._available = False
        cloudlog.warning(f"CardSegmenter[{self.model_name}]: {result.error_message} — disabling")
      return None
    class_map = result.outputs.get('output') if result.outputs else None
    if class_map is None:
      return None
    class_map = np.asarray(class_map)
    return class_map if class_map.ndim == 2 else None


def summarize(drivable_map: np.ndarray) -> tuple[bool, bool, bool]:
  """(hasRoad, hasEdge, hasDrivable) for a MonoSegment.

  Lane-line pixels count as road surface.
  hasRoad     : road covers at least 5 % of the lower half
  hasDrivable : road covers at least 30 % of the lower-centre third
  hasEdge     : the road's boundary is in view -- road present and at least
                10 % of the lower half off the road
  """
  h, w = drivable_map.shape
  lower = drivable_map[h // 2:] != OTHER
  centre = lower[:, w // 3: 2 * w // 3]
  road = float(np.mean(lower))
  return road >= 0.05, road >= 0.05 and (1.0 - road) >= 0.10, float(np.mean(centre)) >= 0.30
