"""Verify the real controlsd deceleration condition against monitoring stages."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from nagaspilot.controls.ngp_driver_activity import DriverActivityMonitor


@pytest.mark.parametrize("awareness,soft_disabling,expected", [(1.0, False, False), (0.25, False, False),
                                                               (0.0, False, True), (-0.1, False, True),
                                                               (1.0, True, True)])
def test_monitor_and_actual_controlsd_deceleration_condition_agree(awareness, soft_disabling, expected):
  # Execute the production assignment without loading unrelated hardware,
  # controller or MPC extensions. Changes to the consumer are exercised too.
  source = Path(__file__).resolve().parents[2] / "selfdrive/controls/controlsd.py"
  assignments = [node for node in ast.walk(ast.parse(source.read_text())) if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Attribute) and target.attr == "forceDecel" for target in node.targets)]
  assert len(assignments) == 1
  monitor = DriverActivityMonitor()
  monitor.awareness = awareness
  status = monitor.update(0.0, True, True, False)
  states = {"driverMonitoringState": NS(awarenessStatus=status.awareness),
            "selfdriveState": NS(state="softDisabling" if soft_disabling else "enabled")}
  cs = NS()
  scope = {"cs": cs, "self": NS(sm=states), "State": NS(softDisabling="softDisabling")}
  exec(compile(ast.Module(body=assignments, type_ignores=[]), str(source), "exec"), scope)
  assert cs.forceDecel is expected
