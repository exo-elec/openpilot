"""
ngp_rcd.py - Road Condition Detection speed cap (moved from EOP10's rcd.py; pure, no cereal / Params / cv2).

A road-surface classification (GOOD / WET / ICY / SNOW / DEBRIS) becomes a speed cap the planner takes `min(v_cruise, cap)` of: it only ever lowers the
cruise speed. The condition comes from a source the caller supplies (a surface-quality score, a segmentation hint, or the three image metrics of
`classify_metrics`); a condition must repeat HYSTERESIS_FRAMES times before it is adopted, and a cap needs MIN_CONFIDENCE.

One fix over EOP10, behind `legacy_filter_bug` (True reproduces the original exactly): EOP10 kept its smoothing filter at 0 whenever there was no cap and
smoothed UP from there on activation, so the first cap after a good road was ~0.3 m/s and took ~10 s to reach 12 m/s (a hard brake for a wet-road warning).
Here the cap steps straight to the limit on activation and is smoothed only between different limits.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

DT = 0.05                  # planner cycle (DT_MDL)
HYSTERESIS_FRAMES = 5
MIN_CONFIDENCE = 0.5
SPEED_FILTER_TC = 2.0      # s


class RoadCondition(Enum):
  GOOD = 0
  WET = 1
  ICY = 2
  SNOW = 3
  DEBRIS = 4


SPEED_LIMITS = {          # m/s, 0 = no limit
  RoadCondition.GOOD: 0.0,
  RoadCondition.WET: 12.0,
  RoadCondition.ICY: 8.0,
  RoadCondition.SNOW: 8.0,
  RoadCondition.DEBRIS: 12.0,
}


@dataclass
class RoadConditionResult:
  condition: RoadCondition
  confidence: float
  description: str
  speed_limit_ms: float


@dataclass
class RCDState:
  condition: RoadCondition
  confidence: float
  speed_limit_ms: float
  is_active: bool
  reason: str


def result_for(condition: RoadCondition, confidence: float, description: str) -> RoadConditionResult:
  return RoadConditionResult(condition, confidence, description, SPEED_LIMITS[condition])


def classify_metrics(avg_saturation: float, avg_value: float, bright_ratio: float, icy_threshold: float = 0.1, wet_saturation: float = 30.0,
                     wet_value: float = 80.0, debris_threshold: float = 0.05) -> RoadConditionResult:
  """The three numbers EOP10's classifier took off the bottom 40 % of the road image (HSV mean saturation, mean value, share of pixels > 200)."""
  if bright_ratio > icy_threshold and avg_value > 180:
    return result_for(RoadCondition.ICY, 0.7, 'icy_surface')
  if avg_saturation < wet_saturation and avg_value < wet_value:
    return result_for(RoadCondition.WET, 0.6, 'wet_surface')
  if bright_ratio > debris_threshold:
    return result_for(RoadCondition.DEBRIS, 0.5, 'debris_detected')
  return result_for(RoadCondition.GOOD, 0.9, 'good')


def from_surface_score(score: float, texture: str = '') -> RoadConditionResult:
  """surfaced's 0..1 roughness score."""
  if score > 0.6:
    return result_for(RoadCondition.DEBRIS, min(score, 0.9), f"rough_surface ({texture})")
  if score > 0.3:
    return result_for(RoadCondition.WET, score, f"wet_surface ({texture})")
  return result_for(RoadCondition.GOOD, 1.0 - score, f"good ({texture})")


def from_segmentation(has_road: bool, has_edge: bool, has_drivable: bool) -> RoadConditionResult | None:
  """Road edge seen but no drivable area: be cautious. Anything else says nothing."""
  if has_road and has_edge and not has_drivable:
    return result_for(RoadCondition.DEBRIS, 0.4, 'edge_no_drivable')
  return None


class RCD:
  def __init__(self, legacy_filter_bug: bool = False):
    self.legacy_filter_bug = legacy_filter_bug
    self.current_condition = RoadCondition.GOOD
    self.current_confidence = 0.0
    self._history: deque = deque(maxlen=HYSTERESIS_FRAMES)
    self._last_condition = RoadCondition.GOOD
    self._alpha = DT / (SPEED_FILTER_TC + DT)
    self._x = 0.0
    self._limit = 0.0
    self._cap_on = False           # filter holds a live cap (fixed mode)

  def _hysteresis(self, new: RoadCondition) -> RoadCondition:
    self._history.append(new)
    if len(self._history) < HYSTERESIS_FRAMES:
      return self._last_condition
    if all(c == new for c in self._history):
      self._last_condition = new
      return new
    return self._last_condition

  def _smooth(self, target: float) -> float:
    if self.legacy_filter_bug:
      self._x = (1. - self._alpha) * self._x + self._alpha * target
      return self._x
    if not self._cap_on:
      self._x, self._cap_on = target, True
    else:
      self._x = (1. - self._alpha) * self._x + self._alpha * target
    return self._x

  def update(self, result: RoadConditionResult | None) -> RCDState:
    """One cycle. `result` is None when no source has anything to say."""
    if result is None:
      return RCDState(self.current_condition, self.current_confidence, self._limit, False, "No data source available")
    condition = self._hysteresis(result.condition)
    self.current_condition, self.current_confidence = condition, result.confidence
    if result.confidence >= MIN_CONFIDENCE and condition != RoadCondition.GOOD:
      limit = SPEED_LIMITS[condition]
      self._limit = self._smooth(limit)
      return RCDState(condition, result.confidence, self._limit, True, f"{condition.name}: limit={limit:.1f}m/s")
    if self.legacy_filter_bug:
      self._x = (1. - self._alpha) * self._x
    self._cap_on = False
    self._limit = 0.0
    return RCDState(condition, result.confidence, 0.0, False, f"{condition.name}: no limit")
