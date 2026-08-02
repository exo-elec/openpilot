"""Comma 3-safe DLAT arbitration for the NGP10 proving line.

This module deliberately has no controlsd or cereal integration.  It converts
lane-line confidence into a stable *suggestion* that can be replay-tested
before it is allowed to select a lateral path.
"""

from dataclasses import dataclass
from enum import Enum
from statistics import fmean


class DLATSuggestion(Enum):
  LANEFUL = "laneful"
  LANELESS = "laneless"


@dataclass(frozen=True)
class DLATResult:
  """Replay/logging result. ``suggestion`` never selects a control path."""

  suggestion: DLATSuggestion
  lane_confidence: float
  path_confidence: float
  model_confidence: float
  path_deviation: float | None
  road_edge_confidence: float
  curve_detected: bool
  model_valid: bool


class NGPDLAT:
  """Hysteretic lane confidence arbiter; output is non-controlling."""

  def __init__(self, enter_threshold=0.40, exit_threshold=0.70,
               enter_frames=20, exit_frames=40):
    if not 0.0 <= enter_threshold < exit_threshold <= 1.0:
      raise ValueError("thresholds must satisfy 0 <= enter < exit <= 1")
    self.enter_threshold = enter_threshold
    self.exit_threshold = exit_threshold
    self.enter_frames = enter_frames
    self.exit_frames = exit_frames
    self.suggestion = DLATSuggestion.LANEFUL
    self._low_frames = 0
    self._high_frames = 0

  @staticmethod
  def lane_confidence(lane_line_probs):
    """Weight the two inner lane lines more than the outer lines."""
    probs = list(lane_line_probs or [])
    if len(probs) < 4:
      return 0.5
    confidence = 0.1 * probs[0] + 0.4 * probs[1] + 0.4 * probs[2] + 0.1 * probs[3]
    return max(0.0, min(1.0, confidence))

  @staticmethod
  def _field(value, name):
    """Read a dotted field from cereal readers, namespaces, or replay dicts."""
    for part in name.split("."):
      if value is None:
        return None
      value = value.get(part) if isinstance(value, dict) else getattr(value, part, None)
    return value

  @classmethod
  def _values(cls, value, name):
    field = cls._field(value, name)
    if field is None:
      return ()
    try:
      return tuple(float(item) for item in field)
    except (TypeError, ValueError):
      return ()

  @staticmethod
  def _confidence_from_stds(stds):
    if not stds:
      return 0.5
    mean_std = fmean(abs(std) for std in stds)
    return max(0.0, min(1.0, 1.0 / (1.0 + mean_std)))

  @classmethod
  def _path_deviation(cls, model):
    """Compare the v0.10.0 predicted position with the inner-lane center."""
    predicted_y = cls._values(model, "position.y")
    lane_lines = cls._field(model, "laneLines")
    if len(predicted_y) <= 5 or lane_lines is None:
      return None
    try:
      lane_lines = list(lane_lines)
      left_y = cls._values(lane_lines[1], "y")
      right_y = cls._values(lane_lines[2], "y")
    except (IndexError, TypeError):
      return None
    if len(left_y) <= 5 or len(right_y) <= 5:
      return None
    lane_center_y = (left_y[5] + right_y[5]) / 2.0
    return abs(predicted_y[5] - lane_center_y)

  @classmethod
  def _curve_detected(cls, model, v_ego):
    rates = cls._values(model, "orientationRate.z")
    if not rates or v_ego is None or float(v_ego) < 1.0:
      return False
    # The EOP reference uses the fifth model-horizon sample.
    curvature = abs(rates[min(4, len(rates) - 1)]) / float(v_ego)
    return curvature > 0.055

  def update(self, lane_line_probs):
    """Update state at model cadence and return a non-controlling suggestion."""
    confidence = self.lane_confidence(lane_line_probs)
    if confidence < self.enter_threshold:
      self._low_frames += 1
      self._high_frames = 0
    elif confidence > self.exit_threshold:
      self._high_frames += 1
      self._low_frames = 0
    else:
      self._low_frames = 0
      self._high_frames = 0

    if self.suggestion is DLATSuggestion.LANEFUL and self._low_frames >= self.enter_frames:
      self.suggestion = DLATSuggestion.LANELESS
      self._low_frames = 0
    elif self.suggestion is DLATSuggestion.LANELESS and self._high_frames >= self.exit_frames:
      self.suggestion = DLATSuggestion.LANEFUL
      self._high_frames = 0
    return self.suggestion

  def update_model(self, model, v_ego=None):
    """Adapt a v0.10.0 ``ModelDataV2`` sample into a shadow DLAT result.

    v0.10.0 exposes the laneless path as ``position`` and its uncertainty as
    ``position.yStd``; the EOP prototype's ``predictedPath`` fields do not
    exist in this schema.
    """
    lane_probs = self._values(model, "laneLineProbs")
    lane_confidence = self.lane_confidence(lane_probs)
    path_stds = self._values(model, "position.yStd")
    path_confidence = self._confidence_from_stds(path_stds)
    road_edge_confidence = self._confidence_from_stds(self._values(model, "roadEdgeStds"))
    model_confidence = max(0.0, min(1.0, 0.6 * lane_confidence + 0.4 * path_confidence))
    model_valid = len(lane_probs) >= 4 and bool(self._values(model, "position.y"))

    # Missing/partial model data deliberately resolves to the neutral 0.5
    # confidence and therefore cannot push the arbiter into laneless mode.
    suggestion = self.update(lane_probs if model_valid else ())
    return DLATResult(
      suggestion=suggestion,
      lane_confidence=lane_confidence,
      path_confidence=path_confidence,
      model_confidence=model_confidence,
      path_deviation=self._path_deviation(model),
      road_edge_confidence=road_edge_confidence,
      curve_detected=self._curve_detected(model, v_ego),
      model_valid=model_valid,
    )
