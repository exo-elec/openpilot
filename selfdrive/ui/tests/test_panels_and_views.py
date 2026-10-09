"""Panel gestures, settings controls, navigation, onboarding, DVR."""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from openpilot.selfdrive.ui.components.controls import ControlRow, ParamStore
from openpilot.selfdrive.ui.components.keyboard import OnScreenKeyboard
from openpilot.selfdrive.ui.components.panels import (
  HOLD_MS,
  SWIPE_PX,
  PanelData,
  PanelHost,
  register_builtin_panels,
)
from openpilot.selfdrive.ui.qt import Qt, QtCore, QtGui, QtWidgets, QApplication, QWidget
from openpilot.selfdrive.ui.settings.descriptor import Control, Kind
from openpilot.selfdrive.ui.views.dvr import DvrView, scan_segments
from openpilot.selfdrive.ui.views.navigation import (
  Maneuver,
  NavigationView,
  Route,
  Step,
  format_distance,
  format_eta,
)
from openpilot.selfdrive.ui.views.offroad import OffroadView
from openpilot.selfdrive.ui.views.onboarding import (
  TERMS_VERSION,
  TRAINING_VERSION,
  OnboardingView,
)


@pytest.fixture(scope="module")
def app():
  return QApplication.instance() or QApplication([])


class FakeParams:
  def __init__(self, initial=None):
    self.d = dict(initial or {})

  def get_bool(self, k):
    return bool(self.d.get(k, False))

  def put_bool(self, k, v):
    self.d[k] = bool(v)

  def get(self, k):
    return self.d.get(k)

  def put(self, k, v):
    self.d[k] = v


def _press_release(widget, x0, x1, hold_ms=0):
  """Synthesise a drag. Times come from the panel's own monotonic clock, so
  the hold is faked by rewinding the recorded press time."""
  press = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, QtCore.QPointF(x0, 10),
                            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
  widget.mousePressEvent(press)
  if hold_ms:
    widget._press_ms -= hold_ms
  release = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease, QtCore.QPointF(x1, 10),
                              Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
  widget.mouseReleaseEvent(release)


class TestPanelGestures:
  def _host(self, app):
    parent = QWidget()
    parent.resize(1600, 500)
    host = PanelHost(parent)
    host.resize(1600, 500)
    parent.show()
    QApplication.processEvents()
    self._keep = parent
    return host

  def test_registry_has_panels(self):
    assert len(register_builtin_panels().order) >= 5

  def test_swipe_cycles_that_panel_only(self, app):
    host = self._host(app)
    before_left, before_right = host.left_key, host.right_key
    _press_release(host.left, 100, 100 + SWIPE_PX + 20)
    assert host.left_key != before_left
    assert host.right_key == before_right

  def test_swipe_direction_reverses(self, app):
    host = self._host(app)
    start = host.left_key
    _press_release(host.left, 200, 200 + SWIPE_PX + 20)   # right
    forward = host.left_key
    _press_release(host.left, 200, 200 - SWIPE_PX - 20)   # back left
    assert host.left_key == start and forward != start

  def test_small_drag_is_not_a_swipe(self, app):
    host = self._host(app)
    start = host.left_key
    _press_release(host.left, 100, 100 + SWIPE_PX - 2)
    assert host.left_key == start

  def test_hold_swaps_sides(self, app):
    host = self._host(app)
    left, right = host.left_key, host.right_key
    _press_release(host.left, 100, 100, hold_ms=HOLD_MS + 50)
    assert (host.left_key, host.right_key) == (right, left)

  def test_blocked_refuses_to_cycle(self, app):
    host = self._host(app)
    host.set_blocked(True)
    start = host.left_key
    _press_release(host.left, 100, 100 + SWIPE_PX + 20)
    assert host.left_key == start

  def test_data_reaches_both_panels(self, app):
    host = self._host(app)
    host.set_data(PanelData({"v_ego": 12.0}))
    assert host.left.data.values["v_ego"] == 12.0
    assert host.right.data.values["v_ego"] == 12.0


class TestControls:
  def test_toggle_writes_through(self, app):
    p = FakeParams()
    row = ControlRow(Control("EOPX", Kind.TOGGLE, "X"), ParamStore(p))
    row.widget.setChecked(True)
    assert p.d["EOPX"] is True

  def test_spinbox_range_and_write(self, app):
    p = FakeParams()
    c = Control("EOPN", Kind.SPINBOX, "N", min=0, max=100, step=5, unit="km/h")
    row = ControlRow(c, ParamStore(p))
    row.widget.setValue(40)
    row._flush()                       # writes are debounced, see below
    assert float(p.d["EOPN"]) == 40

  def test_float_spinbox_keeps_decimals(self, app):
    p = FakeParams()
    c = Control("EOPF", Kind.SPINBOX, "F", min=0.5, max=5.0, step=0.1)
    row = ControlRow(c, ParamStore(p))
    row.widget.setValue(1.5)
    row._flush()
    assert abs(float(p.d["EOPF"]) - 1.5) < 1e-6

  def test_spinbox_writes_are_debounced(self, app):
    # Params writes fsync. Stepping through a range must not fsync per step.
    p = FakeParams()
    c = Control("EOPN", Kind.SPINBOX, "N", min=0, max=100, step=5)
    row = ControlRow(c, ParamStore(p))
    for v in range(0, 50, 5):
      row.widget.setValue(v)
    assert "EOPN" not in p.d           # nothing written yet
    row._flush()
    assert float(p.d["EOPN"]) == 45    # only the final value

  def test_pending_edit_is_flushed_on_page_change(self, app):
    # Leaving a page mid-debounce must not lose the edit. Navigation here is
    # a QStackedWidget page switch, not an explicit hide() on the control --
    # Qt only delivers QHideEvent to a widget that was actually visible, so a
    # row that was never shown would pass a hide() test for the wrong reason.
    p = FakeParams()
    c = Control("EOPN", Kind.SPINBOX, "N", min=0, max=100, step=5)
    row = ControlRow(c, ParamStore(p))

    stack = QtWidgets.QStackedWidget()
    page = QWidget()
    QtWidgets.QVBoxLayout(page).addWidget(row)
    stack.addWidget(page)
    stack.addWidget(QWidget())
    stack.show()
    QApplication.processEvents()
    self._keep = stack

    row.widget.setValue(25)
    assert "EOPN" not in p.d           # still inside the debounce window
    stack.setCurrentIndex(1)           # user navigates away
    QApplication.processEvents()
    assert float(p.d["EOPN"]) == 25

  def test_refresh_picks_up_external_change(self, app):
    # The bug this guards: a control read its param once at construction, so
    # anything changing it elsewhere left a stale widget until UI restart.
    p = FakeParams({"EOPX": False})
    row = ControlRow(Control("EOPX", Kind.TOGGLE, "X"), ParamStore(p))
    assert not row.widget.isChecked()
    p.d["EOPX"] = True                 # changed by a daemon, another page, adb
    row.refresh()
    assert row.widget.isChecked()

  def test_refresh_does_not_rewrite(self, app):
    p = FakeParams({"EOPX": True})
    row = ControlRow(Control("EOPX", Kind.TOGGLE, "X"), ParamStore(p))
    seen = []
    row.changed.connect(lambda *a: seen.append(a))
    row.refresh()
    assert seen == []                  # refresh must not echo back as an edit

  def test_buttons_store_index(self, app):
    p = FakeParams()
    c = Control("EOPB", Kind.BUTTONS, "B", options=("A", "B", "C"))
    row = ControlRow(c, ParamStore(p))
    row._on_button(2)
    assert int(p.d["EOPB"]) == 2

  def test_store_survives_unparseable_value(self, app):
    store = ParamStore(FakeParams({"EOPN": "not a number"}))
    assert store.get_number("EOPN", 7.0) == 7.0


class TestOffroadView:
  def test_builds_every_page(self, app):
    v = OffroadView(store=ParamStore(FakeParams()))
    v.resize(1600, 600)
    assert v.tabs.count() >= 8
    assert sum(len(p.rows) for p in v.pages.values()) > 0


class TestNavigation:
  def test_maneuver_parsing(self):
    assert Maneuver.parse("turn left") is Maneuver.LEFT
    assert Maneuver.parse("SHARP RIGHT") is Maneuver.SHARP_RIGHT
    assert Maneuver.parse("") is Maneuver.STRAIGHT
    assert Maneuver.parse("nonsense") is Maneuver.STRAIGHT

  def test_turn_directions_have_correct_sign(self):
    assert Maneuver.LEFT.turn_degrees < 0
    assert Maneuver.RIGHT.turn_degrees > 0
    assert Maneuver.STRAIGHT.turn_degrees == 0

  def test_distance_formatting(self):
    assert format_distance(240) == "240 m"
    assert format_distance(5400) == "5.4 km"
    assert "ft" in format_distance(100, metric=False)

  def test_eta_formatting(self):
    assert format_eta(0) == "--"
    assert format_eta(780) == "13 min"
    assert format_eta(3660).startswith("1 h")

  def test_view_handles_empty_route(self, app):
    v = NavigationView()
    v.resize(1600, 500)
    v.set_route(Route())
    assert v.card.instruction.text() == "No route"

  def test_view_shows_next_step(self, app):
    v = NavigationView()
    v.resize(1600, 500)
    v.set_route(Route(steps=[Step(Maneuver.LEFT, "Onto Rama IV", 240.0)], eta_s=600))
    assert "240" in v.card.distance.text()
    assert "Rama IV" in v.card.instruction.text()


class TestOnboarding:
  def test_incomplete_by_default(self, app):
    ob = OnboardingView(ParamStore(FakeParams()))
    assert not ob.completed()

  def test_terms_then_training_completes(self, app):
    p = FakeParams()
    ob = OnboardingView(ParamStore(p))
    ob.terms.accepted.emit()
    assert p.d["HasAcceptedTerms"] == TERMS_VERSION
    for _ in ob.training.STEPS:
      ob.training._advance()
    assert p.d["CompletedTrainingVersion"] == TRAINING_VERSION
    assert ob.completed()

  def test_resumes_at_training_when_terms_done(self, app):
    p = FakeParams({"HasAcceptedTerms": TERMS_VERSION})
    ob = OnboardingView(ParamStore(p))
    assert ob.currentWidget() is ob.training

  def test_declining_returns_to_welcome(self, app):
    ob = OnboardingView(ParamStore(FakeParams()))
    ob.terms.declined.emit()
    assert ob.currentWidget() is ob.welcome


class TestKeyboard:
  def test_typing_and_backspace(self, app):
    kb = OnScreenKeyboard()
    for c in "abc":
      kb._on_key(c)
    kb._on_key("⌫")
    assert kb.text() == "ab"

  def test_shift_is_one_shot(self, app):
    kb = OnScreenKeyboard()
    kb._on_key("⇧")
    assert kb._shift
    kb._on_key("A")
    assert not kb._shift        # sticky caps is the classic annoyance

  def test_submit_emits_text(self, app):
    kb = OnScreenKeyboard(initial="hello")
    seen = []
    kb.submitted.connect(seen.append)
    kb._on_key("done")
    assert seen == ["hello"]

  def test_password_masks_preview(self, app):
    kb = OnScreenKeyboard(password=True)
    kb._on_key("s")
    kb._on_key("e")
    assert kb.preview.text() == "••"
    assert kb.text() == "se"


class TestDvr:
  def test_missing_root_is_not_an_error(self, app):
    assert scan_segments(Path("/nonexistent")) == []
    v = DvrView(root=Path("/nonexistent"))
    assert "no recordings" in v.status.text()

  def test_lists_segments_newest_first(self, app, tmp_path):
    # Offsets are taken from each file's own mtime rather than time.time():
    # the repo bans wall-clock time in favour of monotonic, and monotonic is
    # meaningless as a filesystem timestamp, so neither is the right tool.
    for i, name in enumerate(["a.hevc", "b.hevc", "c.mp4"]):
      f = tmp_path / name
      f.write_bytes(b"\0" * 1024)
      base = f.stat().st_mtime
      os.utime(f, (base - (10 - i) * 60,) * 2)
    segs = scan_segments(tmp_path)
    assert len(segs) == 3
    assert segs[0].started >= segs[-1].started

  def test_ignores_non_video_files(self, app, tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "clip.hevc").write_bytes(b"\0")
    assert [s.path.name for s in scan_segments(tmp_path)] == ["clip.hevc"]


class TestOpenpilotPanelTabs:
  """The Device/WiFi/Bluetooth/Toggles/Software/Developer tabs.

  These were missing from this branch entirely until now. The WiFi one is not
  cosmetic: with no way to join a network there is no way to update and no
  way to pair a phone.
  """

  def _view(self, app):
    view = OffroadView(ParamStore(FakeParams()))
    view.resize(1600, 600)
    view.show()
    QApplication.processEvents()
    self._keep = view
    return view

  def test_every_openpilot_panel_has_a_tab(self, app):
    names = self._view(app).page_names()
    for expected in ("Device", "WiFi", "Bluetooth", "Toggles", "Software",
                     "Developer"):
      assert expected in names

  def test_tab_labels_are_unique(self, app):
    # The descriptor has its own "device" page. Two tabs with the same label
    # is a UI that cannot be navigated, so one of them is "EOP Device".
    names = self._view(app).page_names()
    assert len(names) == len(set(names))

  def test_opening_a_panel_does_not_recurse(self, app):
    # The bug this guards: swapping the placeholder for the real panel calls
    # removeTab/insertTab, each of which re-emits currentChanged, which comes
    # straight back into the builder. Leaving the builder registered until
    # after the swap made that unbounded -- it blew the stack the first time
    # any openpilot tab was opened.
    view = self._view(app)
    names = view.page_names()
    index = names.index("Toggles")
    view.tabs.setCurrentIndex(index)
    QApplication.processEvents()
    assert index in view._built
    assert index not in view._lazy

  def test_panels_are_not_built_until_visited(self, app):
    # Building all six up front would open DBus connections to
    # NetworkManager and BlueZ before the user asked for either.
    view = self._view(app)
    assert view._built == {}
    assert len(view._lazy) == 6

  def test_tab_order_survives_the_swap(self, app):
    view = self._view(app)
    before = view.page_names()
    for name in ("Device", "WiFi", "Toggles"):
      view.tabs.setCurrentIndex(before.index(name))
      QApplication.processEvents()
    assert view.page_names() == before

  def test_driving_state_reaches_built_panels(self, app):
    view = self._view(app)
    index = view.page_names().index("Developer")
    view.tabs.setCurrentIndex(index)
    QApplication.processEvents()
    view.set_driving_state(engaged=True, offroad=False)
    body = view._built[index].body
    # Every Developer row changes how the car drives; none may be touched
    # while it is moving.
    assert not body.adb.toggle.isEnabled()
    view.set_driving_state(engaged=False, offroad=True)
    assert body.adb.toggle.isEnabled()
