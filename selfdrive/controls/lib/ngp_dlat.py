"""Comma 3-safe DLAT arbitration for the NGP10 proving line.

This module deliberately has no controlsd or cereal integration.  It converts
lane-line confidence into a stable *suggestion* that can be replay-tested
before it is allowed to select a lateral path.
"""

from enum import Enum


class DLATSuggestion(Enum):
  LANEFUL = "laneful"
  LANELESS = "laneless"


class NGP10DLAT:
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
