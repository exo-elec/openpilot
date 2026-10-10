"""Verify the real controlsd deceleration condition against monitoring stages."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from nagaspilot.controls.ngp_driver_activity import DriverActivityMonitor, monitoring_force_decel, monitoring_speed_target


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
  scope = {"cs": cs, "self": NS(sm=states), "State": NS(softDisabling="softDisabling"),
           "monitoring_force_decel": monitoring_force_decel}
  exec(compile(ast.Module(body=assignments, type_ignores=[]), str(source), "exec"), scope)
  assert cs.forceDecel is expected


@pytest.mark.parametrize("target,forced,expected", [(33.0, True, 0.0), (1.4, True, 0.0),
                                                   (33.0, False, 33.0), (0.0, False, 0.0)])
def test_actual_planner_applies_shared_monitoring_guard_before_mpc(target, forced, expected):
  source = Path(__file__).resolve().parents[2] / "selfdrive/controls/lib/longitudinal_planner.py"
  tree = ast.parse(source.read_text())
  guard = [node for node in ast.walk(tree) if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
           and isinstance(node.value.func, ast.Name) and node.value.func.id == "monitoring_speed_target"]
  mpc = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
         and node.func.attr == "update" and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "mpc"]
  assert len(guard) == len(mpc) == 1
  assert guard[0].lineno < mpc[0].lineno
  assert isinstance(mpc[0].args[1], ast.Name) and mpc[0].args[1].id == "v_cruise"
  writes = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
            and guard[0].lineno < node.lineno < mpc[0].lineno
            and any(isinstance(t, ast.Name) and t.id == "v_cruise" for t in node.targets)]
  assert not writes
  scope = {"v_cruise": target, "force_slow_decel": forced, "monitoring_speed_target": monitoring_speed_target}
  exec(compile(ast.Module(body=guard, type_ignores=[]), str(source), "exec"), scope)
  assert scope["v_cruise"] == expected
