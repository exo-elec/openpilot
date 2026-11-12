"""Shared SOC observation and lane geometry gates; offset authority stays with its caller."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SOCInput:
  v_ego: float
  left_threat: bool
  right_threat: bool
  lane_line_y: tuple[tuple[float, ...], ...]
  lane_line_probs: tuple[float, ...]
  lane_line_stds: tuple[float, ...]


def geometry_valid(sample: SOCInput) -> bool:
  if len(sample.lane_line_y) < 4 or len(sample.lane_line_probs) < 4 or len(sample.lane_line_stds) < 4:
    return False
  if min(sample.lane_line_probs[:4]) < 0.60 or max(sample.lane_line_stds[:4]) > 0.35:
    return False
  try:
    line_y = [line[5] for line in sample.lane_line_y[:4]]
  except IndexError:
    return False
  widths = [line_y[i + 1] - line_y[i] for i in range(3)]
  return all(2.8 <= w <= 3.6 for w in widths)
