"""
RCD - Road Condition Detection controller. The logic lives in nagaspilot/controls/ngp_rcd.py (shared with NagasPilot, golden-tested
against the original of this file); this module keeps EOP10's names and its `EOPRCDEnabled` switch.

Sources, as before: surfaced's `surfaceStatus`, then the card's `monoSegments`. The classical-CV classifier (`RoadConditionClassifier`) is kept for
callers that have a frame; nothing in the drive loop feeds it one.

Fixed on the way (nagaspilot/controls/ngp_rcd.py docstring): the cap used to start at ~0.3 m/s and smooth up to the limit over ~10 s.
"""
from __future__ import annotations

import cv2
import numpy as np

from openpilot.common.params import Params
from nagaspilot.controls.ngp_rcd import (SPEED_LIMITS, RCDState, RoadCondition, RoadConditionResult,  # noqa: F401
                                         classify_metrics)
from nagaspilot.runtime.rcd import RCDRuntime

HYSTERESIS_FRAMES = 5
MIN_CONFIDENCE = 0.5


class RoadConditionClassifier:
  """HSV / bright-spot classification of the bottom 40 % of a BGR road image."""

  def __init__(self, roi_height_ratio: float = 0.4, icy_threshold: float = 0.1, wet_saturation_threshold: float = 30.0,
               wet_value_threshold: float = 80.0, debris_threshold: float = 0.05):
    self.roi_height_ratio = roi_height_ratio
    self.icy_threshold = icy_threshold
    self.wet_saturation_threshold = wet_saturation_threshold
    self.wet_value_threshold = wet_value_threshold
    self.debris_threshold = debris_threshold
    self.speed_limits = dict(SPEED_LIMITS)

  def classify(self, image: np.ndarray) -> RoadConditionResult:
    h = image.shape[0]
    road_roi = image[int(h * (1.0 - self.roi_height_ratio)):, :]
    hsv = cv2.cvtColor(road_roi, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(road_roi, cv2.COLOR_BGR2GRAY)
    _, bright = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    ratio = float(np.sum(bright > 0) / (road_roi.shape[0] * road_roi.shape[1]))
    return classify_metrics(float(np.mean(hsv[:, :, 1])), float(np.mean(hsv[:, :, 2])), ratio, self.icy_threshold,
                            self.wet_saturation_threshold, self.wet_value_threshold, self.debris_threshold)


class RCD:
  def __init__(self):
    self.params = Params()
    self.classifier = RoadConditionClassifier()
    self._rt = RCDRuntime(self.params.get_bool, "EOPRCDEnabled")

  @property
  def enabled(self) -> bool:
    return self._rt.enabled

  @property
  def current_condition(self) -> RoadCondition:
    return self._rt.core.current_condition

  def update(self, sm) -> RCDState:
    return self._rt.update(sm)

  def _update_from_surface_status(self, sm):
    return self._rt.from_surface(sm)

  def _update_from_segmentation(self, sm):
    return self._rt.from_segmentation(sm)

  def get_speed_limit(self, current_speed_ms: float) -> float:
    if not self.enabled or self.current_condition == RoadCondition.GOOD:
      return 0.0
    return self._rt.core._limit


def apply_rcd_limit(v_cruise: float, rcd_state: RCDState) -> float:
  if not rcd_state.is_active or rcd_state.speed_limit_ms <= 0:
    return v_cruise
  return min(v_cruise, rcd_state.speed_limit_ms)
