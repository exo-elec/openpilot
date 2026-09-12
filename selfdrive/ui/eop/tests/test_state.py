"""UIState polling and snapshot derivation.

Uses a stub SubMaster rather than a mock framework: the real one is a dict-like
with valid()/update(), so a small hand-written double is both more honest about
the contract and readable when it fails.
"""

import dataclasses
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from openpilot.selfdrive.ui.eop.components.blind_spot import CAUTION, CLEAR, WARNING
from openpilot.selfdrive.ui.eop.qt import QApplication
from openpilot.selfdrive.ui.eop.state import (
  PARAM_POLL_DIVISOR,
  SELFDRIVE_GRACE_FRAMES,
  SELFDRIVE_TIMEOUT_S,
  SET_SPEED_NA,
  Snapshot,
  UIState,
  UIStatus,
)


class Msg:
  """Stand-in for a capnp reader: attribute access over a dict."""
  def __init__(self, **fields):
    self.__dict__.update(fields)


# What SubMaster hands back for a service it has never received.
_EMPTY = Msg()


class StubSM:
  """Shaped like the real Python SubMaster.

  valid/updated/recv_frame/recv_time are dicts, not methods -- that is what
  the real one does, and the C++ SubMaster this code was ported from exposes
  methods of the same names. This double used to have `valid` as a method,
  which hid a TypeError that fired on the very first tick against a real
  SubMaster. Keeping the double honest is the whole point of it.
  """

  def __init__(self, **msgs):
    self._msgs = msgs
    self.update_calls = 0
    self.frame = 0
    self.valid = dict.fromkeys(msgs, True)
    self.updated = dict.fromkeys(msgs, True)
    self.recv_frame = dict.fromkeys(msgs, 1)
    self.recv_time = {k: time.monotonic() for k in msgs}

  def update(self, _timeout=0):
    self.update_calls += 1
    self.frame += 1

  def go_quiet(self, name, seconds):
    """Stop marking `name` as updated and age its last receipt."""
    self.updated[name] = False
    self.recv_time[name] = time.monotonic() - seconds

  def invalidate(self, name):
    self.valid[name] = False

  def __getitem__(self, name):
    # The real SubMaster pre-populates `data` with a default-initialised
    # message for every subscribed service, so indexing one that has never
    # arrived gives an empty message rather than raising. A double that
    # raised KeyError instead would fail tests that the real thing passes.
    return self._msgs.get(name, _EMPTY)


def car(**kw):
  base = dict(vEgo=0.0, leftBlinker=False, rightBlinker=False,
              gearShifter="drive", leftBlindspot=False, rightBlindspot=False)
  base.update(kw)
  return Msg(**base)


def controls(left=0, right=0):
  return Msg(leftBlindSpot=left, rightBlindSpot=right)


def selfdrive(enabled=False, state="disabled", **kw):
  return Msg(enabled=enabled, state=state, **kw)


class FakeEnum:
  """Stands in for capnp's _DynamicEnum: str() gives the member name, and
  int() raises exactly as the real one does. That int() raising is the whole
  point -- reading these as ints is what broke the first live run."""

  def __init__(self, name):
    self._name = name

  def __str__(self):
    return self._name

  def __int__(self):
    raise TypeError("int() argument must be a real number, not 'FakeEnum'")

  def __bool__(self):
    return True


def device(started=False, **kw):
  base = dict(started=started, cpuTempC=[], memoryUsagePercent=0.0,
              freeSpacePercent=0.0, networkType=FakeEnum("none"),
              networkStrength=FakeEnum("unknown"),
              thermalStatus=FakeEnum("green"))
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

  # ---- selfdrived-timeout alerts (alerts.cc getAlert) --------------------

  def _onroad_for(self, st, sm, frames):
    """Drive the state machine far enough past the start grace period that
    the timeout checks are armed."""
    for _ in range(frames):
      st.update()

  def test_no_timeout_alert_during_the_startup_grace_period(self, app):
    # selfdrived legitimately takes a moment to come up after ignition. The
    # UI must not call that a fault.
    sm = StubSM(deviceState=device(started=True))
    st = ui_state(sm, EOPIgnitionOn=True)
    self._onroad_for(st, sm, 3)
    assert st.snapshot.alert_size == "none"

  def test_selfdrived_never_seen_says_waiting_to_start(self, app):
    sm = StubSM(deviceState=device(started=True))
    st = ui_state(sm, EOPIgnitionOn=True)
    self._onroad_for(st, sm, SELFDRIVE_GRACE_FRAMES + 2)
    assert st.snapshot.alert_text1 == "openpilot Unavailable"
    assert st.snapshot.alert_text2 == "Waiting to start"
    assert st.snapshot.alert_size == "mid"

  def test_engaged_and_unresponsive_demands_the_driver_take_over(self, app):
    # The one alert that outranks everything: openpilot is engaged and the
    # process doing the driving has stopped reporting.
    sm = StubSM(deviceState=device(started=True),
                selfdriveState=selfdrive(enabled=True, state="enabled"))
    st = ui_state(sm, EOPIgnitionOn=True)
    self._onroad_for(st, sm, SELFDRIVE_GRACE_FRAMES + 2)
    assert st.snapshot.alert_size == "none"    # healthy so far

    sm.go_quiet("selfdriveState", SELFDRIVE_TIMEOUT_S + 1)
    st.update()
    assert st.snapshot.alert_text1 == "TAKE CONTROL IMMEDIATELY"
    assert st.snapshot.alert_severity == "critical"
    assert st.snapshot.alert_size == "full"

  def test_disengaged_and_unresponsive_asks_for_a_reboot(self, app):
    sm = StubSM(deviceState=device(started=True),
                selfdriveState=selfdrive(enabled=False))
    st = ui_state(sm, EOPIgnitionOn=True)
    self._onroad_for(st, sm, SELFDRIVE_GRACE_FRAMES + 2)
    sm.go_quiet("selfdriveState", SELFDRIVE_TIMEOUT_S + 1)
    st.update()
    assert st.snapshot.alert_text1 == "System Unresponsive"
    assert st.snapshot.alert_text2 == "Reboot Device"

  def test_a_brief_gap_is_not_a_timeout(self, app):
    sm = StubSM(deviceState=device(started=True),
                selfdriveState=selfdrive(enabled=True, state="enabled"))
    st = ui_state(sm, EOPIgnitionOn=True)
    self._onroad_for(st, sm, SELFDRIVE_GRACE_FRAMES + 2)
    sm.go_quiet("selfdriveState", SELFDRIVE_TIMEOUT_S - 1)
    st.update()
    assert st.snapshot.alert_size == "none"

  def test_offroad_never_raises_a_timeout_alert(self, app):
    sm = StubSM(deviceState=device(started=False),
                selfdriveState=selfdrive(enabled=False))
    st = ui_state(sm, EOPIgnitionOn=True)
    self._onroad_for(st, sm, SELFDRIVE_GRACE_FRAMES + 2)
    sm.go_quiet("selfdriveState", SELFDRIVE_TIMEOUT_S + 60)
    st.update()
    assert st.snapshot.alert_size == "none"

  def test_a_normal_alert_carries_its_size_through(self, app):
    sm = onroad_sm(selfdriveState=selfdrive(
      enabled=True, state="enabled", alertText1="Speed too low",
      alertText2="", alertStatus="userPrompt", alertSize="small"))
    snap = ui_state(sm, EOPIgnitionOn=True)._read()
    assert snap.alert_severity == "warning" and snap.alert_size == "small"

  def test_submaster_tables_may_be_dicts_or_methods(self, app):
    # The real Python SubMaster uses dicts; the C++ this was ported from uses
    # methods. Reading one as the other is a TypeError -- for `valid` it fired
    # on the first tick against a real SubMaster, and for the rest it would
    # have waited until selfdrived went quiet, which is the worst possible
    # time to find out.
    class MethodSM(StubSM):
      def __init__(self, **msgs):
        super().__init__(**msgs)
        tables = self.valid, self.updated, self.recv_frame, self.recv_time
        self.valid = lambda n, t=tables[0]: t.get(n, False)
        self.updated = lambda n, t=tables[1]: t.get(n, False)
        self.recv_frame = lambda n, t=tables[2]: t.get(n, 0)
        self.recv_time = lambda n, t=tables[3]: t.get(n, 0.0)

    sm = MethodSM(deviceState=device(started=True))
    st = ui_state(sm, EOPIgnitionOn=True)
    self._onroad_for(st, sm, SELFDRIVE_GRACE_FRAMES + 2)
    assert st.snapshot.alert_text1 == "openpilot Unavailable"

  # ---- capnp enums --------------------------------------------------------

  def test_device_enums_are_read_by_name_not_by_int(self, app):
    # networkType, networkStrength and thermalStatus are capnp enums. int()
    # on one raises, which took the whole _read() down on the first tick
    # against a real SubMaster.
    sm = onroad_sm(deviceState=device(started=True,
                                      networkType=FakeEnum("wifi"),
                                      networkStrength=FakeEnum("good"),
                                      thermalStatus=FakeEnum("yellow")))
    snap = ui_state(sm, EOPIgnitionOn=True)._read()
    assert snap.network_type == "wifi"
    assert snap.network_strength == 3
    assert snap.thermal_status == "yellow"

  @pytest.mark.parametrize("name,level", [
    ("unknown", 0), ("poor", 1), ("moderate", 2), ("good", 3), ("great", 4),
  ])
  def test_every_network_strength_maps_to_a_level(self, app, name, level):
    sm = onroad_sm(deviceState=device(started=True,
                                      networkStrength=FakeEnum(name)))
    assert ui_state(sm, EOPIgnitionOn=True)._read().network_strength == level

  def test_an_unrecognised_strength_reads_as_no_signal(self, app):
    # Better to draw no bars than to draw a full set for something the
    # schema grew after this table was written.
    sm = onroad_sm(deviceState=device(started=True,
                                      networkStrength=FakeEnum("cell6G")))
    assert ui_state(sm, EOPIgnitionOn=True)._read().network_strength == 0

  def test_started_does_not_require_deviceState_to_be_valid(self, app):
    # ui.cc reads deviceState.started ungated. Whether the car is on is not
    # something to suppress because another field in deviceState is degraded.
    sm = StubSM(deviceState=device(started=True))
    sm.invalidate("deviceState")
    st = ui_state(sm, EOPIgnitionOn=True)
    st.update()
    assert st.snapshot.started

  def test_a_never_seen_deviceState_is_offroad_not_a_crash(self, app):
    st = ui_state(StubSM(), EOPIgnitionOn=True)
    st.update()
    assert not st.snapshot.started

  def test_snapshot_is_immutable(self, app):
    st = ui_state(StubSM(carState=car()))
    st.update()
    # frozen dataclass -> FrozenInstanceError, a subclass of AttributeError.
    # Naming it matters: a bare Exception here would also pass if the attribute
    # simply did not exist, which is the opposite of what this asserts.
    with pytest.raises(dataclasses.FrozenInstanceError):
      st.snapshot.v_ego = 99.0
