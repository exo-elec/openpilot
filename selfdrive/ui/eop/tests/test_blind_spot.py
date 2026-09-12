"""Blind-spot fusion and band presentation.

Runs headless (QT_QPA_PLATFORM=offscreen) and imports no cereal, so it needs
neither a display nor a built capnp.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from openpilot.selfdrive.ui.eop.qt import QApplication, QWidget

from openpilot.selfdrive.ui.eop.components.blind_spot import (
  CAUTION,
  CLEAR,
  WARNING,
  BlindSpotBands,
  BlindSpotSeverity,
  fuse_blind_spot,
  severity_color,
)
from openpilot.selfdrive.ui.eop.components.border_overlay import (
  _BREATHE_FLOOR as BREATHE_FLOOR,
)
from openpilot.selfdrive.ui.eop.components.border_overlay import (
  _BREATHE_PERIOD_MS as BREATHE_PERIOD_MS,
)
from openpilot.selfdrive.ui.eop.components.border_overlay import (
  BorderOverlay,
  Mode,
  Side,
)


@pytest.fixture(scope="module")
def app():
  return QApplication.instance() or QApplication([])


class TestFusion:
  def test_clear_when_nothing_reports(self):
    assert fuse_blind_spot(0, 0, False, False) == BlindSpotSeverity(CLEAR, CLEAR)

  def test_carstate_bool_raises_to_caution(self):
    sev = fuse_blind_spot(0, 0, True, False)
    assert (sev.left, sev.right) == (CAUTION, CLEAR)

  def test_carstate_cannot_suppress_a_warning(self):
    # The whole point of max(): a bool source must never downgrade a real
    # severity-2 from controlsState.
    sev = fuse_blind_spot(WARNING, WARNING, False, False)
    assert (sev.left, sev.right) == (WARNING, WARNING)

  def test_sides_are_independent(self):
    sev = fuse_blind_spot(WARNING, 0, False, True)
    assert sev.left == WARNING and sev.right == CAUTION

  def test_stale_source_clears_rather_than_sticking(self):
    # Callers pass 0/False for an invalid source. Feeding that in must drop
    # back to CLEAR -- the failure this guards is a phantom warning left on
    # screen forever because max() can only raise.
    assert fuse_blind_spot(WARNING, 0, False, False).left == WARNING
    assert fuse_blind_spot(0, 0, False, False).left == CLEAR

  def test_any_active(self):
    assert not BlindSpotSeverity().any_active
    assert BlindSpotSeverity(left=CAUTION).any_active
    assert BlindSpotSeverity(right=WARNING).any_active

  def test_colour_escalates_at_warning(self):
    assert severity_color(CAUTION) == "caution"
    assert severity_color(WARNING) == "warning"


class TestBands:
  def _bands(self, app, w=1600, h=600):
    # The host must outlive the test body. A local would be collected the
    # moment _bands() returns, taking its children with it, and every
    # assertion below would then touch a deleted C++ object -- which is
    # exactly what shiboken reports rather than segfaulting.
    host = QWidget()
    host.resize(w, h)
    bands = BlindSpotBands(host)
    bands.resize(w, h)
    # A hidden widget never receives resizeEvent at all -- not queued,
    # not delivered by processEvents(), simply not sent. Verified against
    # PySide6 6.11: geometry stays at the default 100x30 until first show.
    # So show the host, which is also what BlindSpotBands.showEvent() exists
    # to cover in production.
    host.show()
    QApplication.processEvents()
    self._host = host
    return bands

  def test_both_bands_hidden_when_clear(self, app):
    b = self._bands(app)
    b.set_severity(BlindSpotSeverity())
    assert not b._left.is_enabled() and not b._right.is_enabled()

  def test_only_the_flagged_side_enables(self, app):
    b = self._bands(app)
    b.set_severity(BlindSpotSeverity(left=CAUTION))
    assert b._left.is_enabled() and not b._right.is_enabled()

  def test_warning_breathes_caution_is_solid(self, app):
    # 01M's C++ indicator ramps alpha on a raised cosine rather than blinking,
    # on the reasoning that a hard on/off this deep into peripheral vision
    # reads as a distraction. Reproducing 01M's design means reproducing that.
    b = self._bands(app)
    b.set_severity(BlindSpotSeverity(left=CAUTION, right=WARNING))
    assert b._left.mode is Mode.SOLID
    assert b._right.mode is Mode.BREATHE

  def test_bands_hug_the_edges_at_1600x600(self, app):
    b = self._bands(app, 1600, 600)
    assert b._left.geometry().left() == 0
    assert b._right.geometry().right() == 1599
    assert b._left.geometry().height() == 600
    # Wide enough to catch peripheral vision, nowhere near the middle.
    assert 100 <= b._left.geometry().width() <= 200

  def test_geometry_follows_a_resize(self, app):
    b = self._bands(app, 1024, 600)
    left_at_1024 = b._left.geometry().width()
    b.resize(1600, 600)
    QApplication.processEvents()
    assert b._right.geometry().right() == 1599
    assert b._left.geometry().width() > left_at_1024

  def test_clearing_disables_both_again(self, app):
    b = self._bands(app)
    b.set_severity(BlindSpotSeverity(left=WARNING, right=WARNING))
    b.set_severity(BlindSpotSeverity())
    assert not b._left.is_enabled() and not b._right.is_enabled()


class TestBreathe:
  """The raised-cosine ramp itself, on BorderOverlay rather than through the
  bands, so a failure points at the arithmetic and not the widget."""

  def _band(self, app, mode):
    b = BorderOverlay(Side.LEFT, "warning", mode)
    b.set_enabled(True)
    return b

  def test_solid_is_always_fully_painted(self, app):
    b = self._band(app, Mode.SOLID)
    for phase in (0, 300, 600, 1199):
      BorderOverlay._phase_ms = phase
      assert b._alpha_scale() == 1.0

  def test_breathe_never_goes_dark(self, app):
    # A band that blinks off entirely stops reading as one continuous signal.
    b = self._band(app, Mode.BREATHE)
    for phase in range(0, 2400, 25):
      BorderOverlay._phase_ms = phase
      assert b._alpha_scale() > 0.0

  def test_breathe_spans_the_intended_range(self, app):
    b = self._band(app, Mode.BREATHE)
    seen = []
    for phase in range(0, 1200, 10):
      BorderOverlay._phase_ms = phase
      seen.append(b._alpha_scale())
    assert min(seen) == pytest.approx(BREATHE_FLOOR, abs=1e-3)
    assert max(seen) == pytest.approx(1.0, abs=1e-3)

  def test_breathe_peaks_mid_cycle(self, app):
    b = self._band(app, Mode.BREATHE)
    BorderOverlay._phase_ms = 0
    at_start = b._alpha_scale()
    BorderOverlay._phase_ms = BREATHE_PERIOD_MS // 2
    at_peak = b._alpha_scale()
    assert at_peak > at_start

  def test_blink_still_toggles_fully_off(self, app):
    b = self._band(app, Mode.BLINK)
    BorderOverlay._phase_ms = 0
    assert b._alpha_scale() == 1.0
    BorderOverlay._phase_ms = b._blink_interval_ms
    assert b._alpha_scale() == 0.0

  def test_the_clock_stops_when_nothing_is_animating(self, app):
    # The clock is shared class state, so bands built by earlier tests in
    # this module may still be animating. Quiet every live instance first --
    # what is under test is that the tick stands the timer down once nothing
    # needs it, not that this one band is the only one that ever existed.
    for inst in BorderOverlay._live():
      inst.set_enabled(False)

    b = self._band(app, Mode.BREATHE)
    assert BorderOverlay._timer is not None, "enabling a breathing band starts the clock"

    b.set_enabled(False)
    BorderOverlay._tick()          # the tick notices and stands itself down
    assert BorderOverlay._timer is None

  def test_the_clock_restarts_on_demand(self, app):
    # Follows the test above, which leaves the clock stopped: enabling a band
    # has to bring it back rather than leaving a permanently frozen band.
    band = self._band(app, Mode.BREATHE)
    assert BorderOverlay._timer is not None
    band.set_enabled(False)
