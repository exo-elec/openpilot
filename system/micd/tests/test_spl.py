import numpy as np
import pytest

from openpilot.system.micd.micd import REFERENCE_SPL, apply_a_weighting, calculate_spl


def test_calculate_spl_returns_pressure_and_db():
  # Both callers in micd unpack two values (it used to return one float,
  # which raised TypeError on the first full buffer).
  x = np.full(1600, 0.5)
  pressure, db = calculate_spl(x)
  assert pressure == pytest.approx(0.5)
  assert db == pytest.approx(20 * np.log10(0.5 / REFERENCE_SPL))


def test_calculate_spl_silence():
  assert calculate_spl(np.zeros(1600)) == (0.0, 0.0)


def test_weighted_path_unpacks():
  t = np.arange(1600) / 16000
  pressure, db = calculate_spl(apply_a_weighting(0.1 * np.sin(2 * np.pi * 1000 * t)))
  assert pressure > 0 and db > 0
