from nagaspilot.lineage import violations


def test_child_additions_are_allowed_but_parent_edits_and_deletions_are_reported():
  parent = {'policy.py': 'one', 'adapter.py': 'two', 'obsolete.py': 'three'}
  child = {'policy.py': 'one', 'adapter.py': 'changed', 'eop_sensor.py': 'four'}
  assert violations(parent, child) == ['changed: adapter.py', 'missing: obsolete.py']
  assert violations({'policy.py': 'one'}, child) == []
