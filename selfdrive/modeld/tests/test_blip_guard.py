#!/usr/bin/env python3
"""Tests for ModelState's blip guard (ported from bukapilot's proven KA2
runner) — suppresses one-frame "straight blips" that follow a run of curved
frames in the split RKNN/ONNX policy's plan output. Exercises the guard
methods directly against a bare ModelState instance (no CLContext/HAL needed)."""

from __future__ import annotations

from collections import deque

import numpy as np


def _bare_model_state():
  """A ModelState with only the blip-guard state initialized — avoids the
  real __init__'s CLContext/HAL/metadata-file dependencies."""
  from openpilot.selfdrive.modeld.modeld import ModelState
  ms = ModelState.__new__(ModelState)
  ms._blip_guard_context = 3
  ms._blip_guard_curved = 0.8
  ms._blip_guard_straight = 0.25
  ms._blip_guard_recent_y20 = deque(maxlen=ms._blip_guard_context)
  ms._blip_guard_prev_plan_position = None
  ms._blip_guard_prev_plan_stds_position = None
  return ms


def _plan(y_val: float, n: int = 5) -> np.ndarray:
  x = np.linspace(0, 20, n)
  plan = np.zeros((1, n, 3), dtype=np.float64)
  plan[0, :, 0] = x
  plan[0, :, 1] = y_val
  return plan


def test_y_at_distance_interpolates():
  ms = _bare_model_state()
  plan = _plan(2.0)
  assert ms._y_at_distance_from_plan(plan) == 2.0


def test_y_at_distance_none_outside_range():
  ms = _bare_model_state()
  x = np.array([0, 5, 10])
  plan = np.zeros((1, 3, 3))
  plan[0, :, 0] = x
  assert ms._y_at_distance_from_plan(plan, distance_m=20.0) is None


def test_y_at_distance_none_when_x_not_monotonic():
  ms = _bare_model_state()
  plan = np.zeros((1, 5, 3))
  plan[0, :, 0] = [0, 10, 5, 15, 20]
  assert ms._y_at_distance_from_plan(plan) is None


def test_apply_blip_guard_noop_when_outputs_missing():
  ms = _bare_model_state()
  ms._apply_blip_guard({})  # must not raise


def test_apply_blip_guard_suppresses_straight_blip_after_sustained_curve():
  ms = _bare_model_state()

  # Three consecutive curved (same-side, above threshold) frames.
  for y in (1.0, 1.1, 1.2):
    outputs = {"plan": _plan(y), "plan_stds": _plan(0.5)}
    ms._apply_blip_guard(outputs)

  # A sudden one-frame straight blip should be suppressed back to the last
  # curved frame's plan.
  blip_outputs = {"plan": _plan(0.05), "plan_stds": _plan(0.5)}
  ms._apply_blip_guard(blip_outputs)

  assert np.allclose(blip_outputs["plan"][0, :, 1], 1.2)


def test_apply_blip_guard_lets_straight_plan_through_without_sustained_curve():
  ms = _bare_model_state()

  # Only two curved frames — below the 3-frame context window.
  for y in (1.0, 1.1):
    outputs = {"plan": _plan(y), "plan_stds": _plan(0.5)}
    ms._apply_blip_guard(outputs)

  straight_outputs = {"plan": _plan(0.05), "plan_stds": _plan(0.5)}
  ms._apply_blip_guard(straight_outputs)

  assert np.allclose(straight_outputs["plan"][0, :, 1], 0.05)


def test_apply_blip_guard_ignores_mixed_side_curve():
  ms = _bare_model_state()

  # Curved but alternating sides — same_side_curve is False, so no guard.
  for y in (1.0, -1.0, 1.0):
    outputs = {"plan": _plan(y), "plan_stds": _plan(0.5)}
    ms._apply_blip_guard(outputs)

  straight_outputs = {"plan": _plan(0.05), "plan_stds": _plan(0.5)}
  ms._apply_blip_guard(straight_outputs)

  assert np.allclose(straight_outputs["plan"][0, :, 1], 0.05)
