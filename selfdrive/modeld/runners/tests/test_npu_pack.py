"""Tests for npu_pack.py's board-agnostic NPU task-to-core packer."""

import pytest

from openpilot.selfdrive.modeld.runners.npu_pack import NPUTaskGroup, core_loads, pack_tasks_to_cores


def test_heaviest_group_takes_the_first_core():
  groups = (NPUTaskGroup(("a",), 2.0), NPUTaskGroup(("b",), 1.0))
  allocation = pack_tasks_to_cores(groups, core_count=2)
  assert allocation == {"a": 1, "b": 2}


def test_every_alias_in_a_group_shares_one_mask():
  groups = (NPUTaskGroup(("modeld", "driving_vision"), 2.0), NPUTaskGroup(("stereod",), 1.0))
  allocation = pack_tasks_to_cores(groups, core_count=2)
  assert allocation["modeld"] == allocation["driving_vision"]


def test_balances_load_across_cores_not_first_fit():
  # Five equal-weight tasks on two cores should split 3/2 or 2/3, not stack
  # everything greedily onto core 0 and force-overflow the rest onto core 1.
  groups = tuple(NPUTaskGroup((f"t{i}",), 1.0) for i in range(5))
  allocation = pack_tasks_to_cores(groups, core_count=2)
  counts = [sum(1 for m in allocation.values() if m == 1 << i) for i in range(2)]
  assert sorted(counts) == [2, 3]


def test_single_core_puts_everything_on_it():
  groups = (NPUTaskGroup(("a",), 2.0), NPUTaskGroup(("b",), 1.0))
  allocation = pack_tasks_to_cores(groups, core_count=1)
  assert allocation == {"a": 1, "b": 1}


def test_rejects_zero_cores():
  with pytest.raises(ValueError):
    pack_tasks_to_cores((NPUTaskGroup(("a",), 1.0),), core_count=0)


def test_core_loads_sums_each_core_from_the_allocation_in_use():
  groups = (NPUTaskGroup(("a",), 2.0), NPUTaskGroup(("b",), 1.0), NPUTaskGroup(("c",), 1.0))
  # A real/measured override, not necessarily what pack_tasks_to_cores would choose.
  allocation = {"a": 1, "b": 1, "c": 2}
  loads = core_loads(groups, allocation, core_count=2)
  assert loads == [3.0, 1.0]


def test_core_loads_ignores_a_group_missing_from_the_allocation():
  groups = (NPUTaskGroup(("a",), 2.0), NPUTaskGroup(("unassigned",), 5.0))
  loads = core_loads(groups, {"a": 1}, core_count=2)
  assert loads == [2.0, 0.0]
