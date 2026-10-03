"""Ratchet: the branch may not widen its edits to upstream selfdrive/ (see docs/CODE_BOUNDARY_TASKS.md)."""
import json

import pytest

from nagaspilot import footprint


@pytest.mark.skipif(not footprint.base_available(), reason="upstream v0.10.0 baseline commit not fetched")
def test_upstream_selfdrive_footprint_does_not_grow():
  budget = json.loads(footprint.BUDGET.read_text())
  assert budget["base"] == footprint.BASE
  found = footprint.violations(footprint.measure(), budget)
  assert not found, "\n".join(found) + "\nMove logic into nagaspilot/ and keep a small hook, or justify and run `python3 nagaspilot/footprint.py --update`."
