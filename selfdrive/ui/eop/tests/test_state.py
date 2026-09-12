"""UIState polling and snapshot derivation.

Uses a stub SubMaster rather than a mock framework: the real one is a dict-like
with valid()/update(), so a small hand-written double is both more honest about
the contract and readable when it fails.
"""

import dataclasses
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from openpilot.selfdrive.ui.eop.components.blind_spot import CAUTION, CLEAR, WARNING
from openpilot.selfdrive.ui.eop.qt import QApplication
from openpilot.selfdrive.ui.eop.state import (
  PARAM_POLL_DIVISOR,
  SET_SPEED_NA,
  Snapshot,
  UIState,
  UIStatus,
)


class Msg:
  """Stand-in for a capnp reader: attribute access over a dict."""
  def __init__(self, **fields):
    self.__dict__.update(fields)


class StubSM:
  def __init__(self, **msgs):
    self._msgs = msgs
    self._valid = dict.fromkeys(msgs, True)
    self.update_calls = 0

  def update(self, _timeout=0):
    self.update_calls += 1

  def valid(self, name):
    return self._valid.get(name, False)

  def invalidate(self, name):
    self._valid[name] = False

  def __getitem__(self, name):
    return self._msgs[name]


def car(**kw):
  base = dict(vEgo=0.0, leftBlinker=False, rightBlinker=False,
              gearShifter="drive", leftBlindspot=False, rightBlindspot=False)
  base.update(kw)
  return Msg(**base)


def controls(left=0, right=0):
  return Msg(leftBlindSpot=left, rightBlindSpot=right)


def selfdrive(enabled=False, state="disabled", **kw):
  return Msg(enabled=enabled, state=state, **kw)


def device(started=False, **kw):
  base = dict(started=started, cpuTempC=[], memoryUsagePercent=0.0,
              freeSpacePercent=0.0, networkType="none", networkStrength=0,
              thermalStatus="green")
  base.update(kw)
  return Msg(**base)


class FakeParams:
  """Just the two keys UIState reads, and a count so the polling decimation
  in update() can be asserted rather than assumed."""
  def __init__(self, **d):
    self.d = {"IsMetric": True, "EOPIgnitionOn": False}
    self.d.update(d)
    self.reads = 0

  def get_bool(self, key):
    self.reads += 1
    return bool(self.d.get(key, False))


def ui_state(sm, params=None, **param_kw):
  """UIState with Params injected. Without this the constructor reaches for
  openpilot.common.params, which is a compiled extension the tests must not
  depend on."""
  return UIState(sm=sm, params=params if params is not None else FakeParams(**param_kw))


def onroad_sm(**msgs):
  """A StubSM already carrying a started deviceState, for the many tests that
  are about something other than the onroad transition itself."""
  msgs.setdefault("deviceState", device(started=True))
  return StubSM(**msgs)


@pytest.fixture(scope="module")
def app():
  return QApplication.instance() or QApplication([])


class TestSnapshot:
  def test_defaults_are_safe(self):
    s = Snapshot()
    assert not s.started and s.status is UIStatus.DISENGAGED
    assert s.blind_spot.left == CLEAR and not s.hazards

  def test_hazards_needs_both_blinkers(self):
    assert not Snapshot(left_blinker=True).hazards
    assert not Snapshot(right_blinker=True).hazards
    assert Snapshot(left_blinker=True, right_blinker=True).hazards


class TestUIState:
  def test_polls_the_submaster(self, app):
    sm = StubSM(carState=car())
    st = ui_state(sm, EOPIgnitionOn=True)
    st.update()
    assert sm.update_calls == 1

  def test_reads_car_state(self, app):
    sm = StubSM(carState=car(vEgo=13.5, leftBlinker=True, gearShifter="reverse"))
    st = ui_state(sm, EOPIgnitionOn=True)
    st.update()
    snap = st.snapshot
    assert snap.v_ego == pytest.approx(13.5)
    assert snap.left_blinker and not snap.right_blinker
    assert snap.in_reverse

  def test_engaged_status(self, app):
    sm = onroad_sm(selfdriveState=selfdrive(enabled=True, state="enabled"))
    st = ui_state(sm, EOPIgnitionOn=True)
    st.update()
    assert st.snapshot.status is UIStatus.ENGAGED
    assert st.snapshot.started

  @pytest.mark.parametrize("op_state", ["preEnabled", "overriding"])
  def test_override_status(self, app, op_state):
    # The bug this guards: OVERRIDE used to be derived from
    # selfdriveState.overrideLateral, which is set during ordinary engaged
    # driving whenever the driver has a hand on the wheel -- so the border
    # went amber constantly while openpilot was in full control. The real
    # signal is the openpilot state machine being in preEnabled/overriding.
    sm = onroad_sm(selfdriveState=selfdrive(enabled=True, state=op_state))
    st = ui_state(sm, EOPIgnitionOn=True)
    st.update()
    assert st.snapshot.status is UIStatus.OVERRIDE

  def test_engaged_with_hands_on_wheel_is_not_override(self, app):
    sm = onroad_sm(selfdriveState=selfdrive(enabled=True, state="enabled",
                                            overrideLateral=True))
    st = ui_state(sm, EOPIgnitionOn=True)
    st.update()
    assert st.snapshot.status is UIStatus.ENGAGED

  def test_alcc_only_shows_while_disengaged(self, app):
    sm = onroad_sm(selfdriveState=selfdrive(enabled=False),
                   alccState=Msg(active=True))
    st = ui_state(sm, EOPIgnitionOn=True)
    st.update()
    assert st.snapshot.status is UIStatus.ALCC

    sm._msgs["selfdriveState"] = selfdrive(enabled=True, state="enabled")
    st.update()
    assert st.snapshot.status is UIStatus.ENGAGED

  def test_started_is_the_car_being_on_not_openpilot_being_engaged(self, app):
    # The bug this guards: started was read from selfdriveState.enabled, so a
    # drive where the driver never engaged left the offroad home screen up for
    # the entire trip. It is deviceState.started AND ignition.
    sm = StubSM(deviceState=device(started=True),
                selfdriveState=selfdrive(enabled=False))
    st = ui_state(sm, EOPIgnitionOn=True)
    st.update()
    assert st.snapshot.started

  def test_ignition_off_is_offroad_even_with_device_started(self, app):
    sm = StubSM(deviceState=device(started=True))
    st = ui_state(sm, EOPIgnitionOn=False)
    st.update()
    assert not st.snapshot.started

  def test_blind_spot_is_fused_from_both_sources(self, app):
    sm = StubSM(carState=car(rightBlindspot=True), controlsState=controls(left=WARNING))
    st = ui_state(sm, EOPIgnitionOn=True)
    st.update()
    bs = st.snapshot.blind_spot
    assert bs.left == WARNING   # from controlsState severity
    assert bs.right == CAUTION  # raised by carState's bool

  def test_invalid_controls_state_clears_rather_than_sticking(self, app):
    # The failure this guards: a severity-2 that outlives the message that
    # raised it, because fuse's max() can only raise.
    sm = StubSM(carState=car(), controlsState=controls(left=WARNING))
    st = ui_state(sm, EOPIgnitionOn=True)
    st.update()
    assert st.snapshot.blind_spot.left == WARNING
    sm.invalidate("controlsState")
    st.update()
    assert st.snapshot.blind_spot.left == CLEAR

  def test_missing_services_do_not_raise(self, app):
    st = ui_state(StubSM())
    st.update()
    assert st.snapshot == Snapshot()

  def test_emits_updated_with_the_snapshot(self, app):
    sm = StubSM(carState=car(vEgo=7.0))
    st = ui_state(sm, EOPIgnitionOn=True)
    seen = []
    st.updated.connect(seen.append)
    st.update()
    assert len(seen) == 1 and seen[0].v_ego == pytest.approx(7.0)

  def test_offroad_transition_fires_only_on_change(self, app):
    sm = StubSM(deviceState=device(started=False))
    st = ui_state(sm, EOPIgnitionOn=True)
    seen = []
    st.offroad_transition.connect(seen.append)

    st.update()
    assert seen == []                      # already offroad, no edge

    sm._msgs["deviceState"] = device(started=True)
    st.update()
    assert seen == [False]                 # went onroad

    st.update()
    assert seen == [False]                 # no repeat while unchanged

    sm._msgs["deviceState"] = device(started=False)
    st.update()
    assert seen == [False, True]           # back offroad

  def test_timer_is_not_running_until_started(self, app):
    st = ui_state(StubSM())
    assert not st._timer.isActive()
    st.start()
    assert st._timer.isActive()
    st.stop()
    assert not st._timer.isActive()

  def test_params_are_polled_at_a_divisor_of_the_ui_rate(self, app):
    # Params live on disk. At UI_FREQ_HZ this would be 40 reads a second for
    # the whole drive, which is why update() decimates.
    fp = FakeParams(EOPIgnitionOn=True)
    st = ui_state(StubSM(), params=fp)
    fp.reads = 0                           # ignore the constructor's read
    for _ in range(PARAM_POLL_DIVISOR * 2):
      st.update()
    assert fp.reads == 2 * 2               # two keys, two refreshes

  def test_ignition_change_reaches_the_snapshot(self, app):
    fp = FakeParams(EOPIgnitionOn=False)
    sm = StubSM(deviceState=device(started=True))
    st = ui_state(sm, params=fp)
    st.update()
    assert not st.snapshot.started
    fp.d["EOPIgnitionOn"] = True
    for _ in range(PARAM_POLL_DIVISOR):
      st.update()
    assert st.snapshot.started

  def test_set_speed_converts_only_for_imperial_display(self, app):
    # vCruiseCluster is km/h on every platform, so metric must not scale it.
    sm = onroad_sm(carState=car(vCruiseCluster=100.0))
    assert ui_state(sm, IsMetric=True, EOPIgnitionOn=True)._read().set_speed == pytest.approx(100.0)
    assert ui_state(sm, IsMetric=False, EOPIgnitionOn=True)._read().set_speed == pytest.approx(62.1371)

  def test_set_speed_na_is_not_a_set_point(self, app):
    sm = onroad_sm(carState=car(vCruiseCluster=SET_SPEED_NA))
    snap = ui_state(sm, EOPIgnitionOn=True)._read()
    assert snap.cruise_available and not snap.cruise_set and snap.set_speed == 0.0

  def test_no_cruise_hardware_reports_unavailable(self, app):
    sm = onroad_sm(carState=car(vCruiseCluster=-1.0))
    assert not ui_state(sm, EOPIgnitionOn=True)._read().cruise_available

  def test_v_ego_prefers_the_cluster_reading(self, app):
    # vEgoCluster is what the car's own speedometer shows; showing something
    # a few km/h off from the cluster next to it reads as a broken UI.
    sm = onroad_sm(carState=car(vEgo=13.5, vEgoCluster=14.0))
    assert ui_state(sm, EOPIgnitionOn=True)._read().v_ego == pytest.approx(14.0)

  def test_v_ego_falls_back_when_the_cluster_value_is_absent(self, app):
    sm = onroad_sm(carState=car(vEgo=13.5, vEgoCluster=0.0))
    assert ui_state(sm, EOPIgnitionOn=True)._read().v_ego == pytest.approx(13.5)

  def test_osm_speed_limit_outranks_the_route_step(self, app):
    sm = onroad_sm(mapData=Msg(speedLimit=80.0),
                   navInstruction=Msg(speedLimit=25.0, maneuverType="none"))
    assert ui_state(sm, EOPIgnitionOn=True)._read().speed_limit_ms == pytest.approx(80.0 / 3.6)

  def test_nav_speed_limit_is_used_when_osm_has_none(self, app):
    sm = onroad_sm(mapData=Msg(speedLimit=0.0),
                   navInstruction=Msg(speedLimit=25.0, maneuverType="none"))
    assert ui_state(sm, EOPIgnitionOn=True)._read().speed_limit_ms == pytest.approx(25.0)

  def test_maneuver_none_is_not_a_maneuver(self, app):
    sm = onroad_sm(navInstruction=Msg(maneuverType="none", speedLimit=0.0))
    assert not ui_state(sm, EOPIgnitionOn=True)._read().nav.valid

  def test_maneuver_is_read_whole(self, app):
    sm = onroad_sm(navInstruction=Msg(maneuverType="turn", maneuverModifier="left",
                                      maneuverPrimaryText="Sukhumvit Rd",
                                      maneuverDistance=180.0, speedLimit=0.0))
    nav = ui_state(sm, EOPIgnitionOn=True)._read().nav
    assert nav.valid and nav.modifier == "left"
    assert nav.primary_text == "Sukhumvit Rd" and nav.distance_m == pytest.approx(180.0)

  def test_driver_monitor_merges_both_daemons(self, app):
    sm = onroad_sm(driverPoseState=Msg(attentionProb=0.8),
                   driverStatus=Msg(faceDetected=True, faceForward=True, faceX=0.4,
                                    faceY=0.5, faceYaw=3.0, facePitch=-2.0))
    d = ui_state(sm, EOPIgnitionOn=True)._read().driver
    assert d.valid and d.attention_prob == pytest.approx(0.8)
    assert d.face_detected and d.face_forward

  def test_driver_monitor_works_without_a_camera(self, app):
    # The steering-based monitor runs on its own; driverStatus may never come.
    sm = onroad_sm(driverPoseState=Msg(attentionProb=0.3))
    d = ui_state(sm, EOPIgnitionOn=True)._read().driver
    assert d.valid and not d.face_detected

  def test_panda_is_connected_when_any_panda_reports_a_type(self, app):
    sm = onroad_sm(pandaStates=[Msg(pandaType="unknown"), Msg(pandaType="dos")])
    assert ui_state(sm, EOPIgnitionOn=True)._read().panda_connected

  def test_all_unknown_pandas_is_not_connected(self, app):
    sm = onroad_sm(pandaStates=[Msg(pandaType="unknown")])
    assert not ui_state(sm, EOPIgnitionOn=True)._read().panda_connected

  def test_speed_display_follows_the_unit_setting(self, app):
    assert Snapshot(v_ego=10.0, is_metric=True).speed_display == pytest.approx(36.0)
    assert Snapshot(v_ego=10.0, is_metric=False).speed_display == pytest.approx(22.36936)
    assert Snapshot(is_metric=False).speed_unit == "mph"

  def test_snapshot_is_immutable(self, app):
    st = ui_state(StubSM(carState=car()))
    st.update()
    # frozen dataclass -> FrozenInstanceError, a subclass of AttributeError.
    # Naming it matters: a bare Exception here would also pass if the attribute
    # simply did not exist, which is the opposite of what this asserts.
    with pytest.raises(dataclasses.FrozenInstanceError):
      st.snapshot.v_ego = 99.0
