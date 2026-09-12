"""Settings completeness gate.

Asserts every EOP param is accounted for: exposed as a control, or excluded
with a reason. This is the check that makes porting 259 params tractable --
without it, a missing control is invisible until someone goes looking for a
setting that was never built.
"""

from openpilot.selfdrive.ui.eop.settings.descriptor import (
  PAGES,
  Kind,
  all_controls,
  declared_keys,
)
from openpilot.selfdrive.ui.eop.settings.exclusions import (
  TRIAGED,
  UNTRIAGED,
  excluded_keys,
)
from openpilot.selfdrive.ui.eop.settings.params_registry import (
  all_params,
  eop_keys,
  eop_params,
)


class TestRegistry:
  def test_header_parses(self):
    # Guards the regex in params_registry against the header's format moving.
    assert len(all_params()) > 300
    assert len(eop_params()) > 200

  def test_types_are_known(self):
    assert {p.type for p in eop_params()} <= {"BOOL", "INT", "FLOAT", "STRING"}


class TestCoverage:
  def test_every_eop_param_is_accounted_for(self):
    missing = eop_keys() - declared_keys() - excluded_keys()
    assert not missing, (
      f"{len(missing)} EOP params are neither exposed nor excluded. "
      + "Add them to descriptor.py, or to exclusions.py with a reason:\n  "
      + "\n  ".join(sorted(missing))
    )

  def test_descriptor_only_declares_real_params(self):
    # The other direction: a control for a key that no longer exists in the
    # header is dead UI that silently does nothing when toggled.
    unknown = declared_keys() - eop_keys()
    assert not unknown, f"controls for non-existent params: {sorted(unknown)}"

  def test_nothing_is_both_declared_and_excluded(self):
    assert not (declared_keys() & excluded_keys())

  def test_untriaged_debt_is_visible(self):
    # Not a failure -- a ratchet. It reports the gap inherited from the C++
    # panel so it stays in view rather than quietly becoming permanent.
    total = len(eop_keys())
    print(f"\nsettings coverage: {len(declared_keys())}/{total} exposed, "
          + f"{len(TRIAGED)} triaged, {len(UNTRIAGED)} untriaged")
    assert len(UNTRIAGED) <= 219, "untriaged set grew -- triage before adding"


class TestDescriptor:
  def test_pages_are_uniquely_named(self):
    names = [p.name for p in PAGES]
    assert len(names) == len(set(names))

  def test_no_duplicate_controls(self):
    keys = [c.key for c in all_controls()]
    assert len(keys) == len(set(keys))

  def test_every_control_has_a_title(self):
    assert all(c.title.strip() for c in all_controls())

  def test_spinboxes_have_sane_ranges(self):
    for c in all_controls():
      if c.kind is Kind.SPINBOX:
        assert c.min < c.max, f"{c.key}: min >= max"
        assert c.step > 0, f"{c.key}: non-positive step"

  def test_button_groups_have_options(self):
    for c in all_controls():
      if c.kind is Kind.BUTTONS:
        assert len(c.options) >= 2, f"{c.key}: needs at least two options"
