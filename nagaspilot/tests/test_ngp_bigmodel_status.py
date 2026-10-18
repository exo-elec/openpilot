from nagaspilot.controls.ngp_bigmodel_status import MODEL_ERROR, NOT_DETECTED, NOT_DETECTED_AFTER_S, SLOW_MBPS, USB_SLOW, PhoneLinkStatus


def step(s, now, offroad=True, enabled=True, cable=True, speed=5000.0, loading=False, active=None):
  return s.update(now, offroad, enabled, cable, speed, loading, active)


def shown(alerts):
  return {name for name, (show, _) in alerts.items() if show}


def test_a_disabled_link_never_alerts():
  s = PhoneLinkStatus(0.0)
  assert shown(step(s, 100.0, enabled=False, cable=False, speed=480.0)) == set()


def test_no_cable_is_not_detected_only_after_ten_seconds_parked():
  s = PhoneLinkStatus(0.0)
  assert shown(step(s, NOT_DETECTED_AFTER_S - 1, cable=False)) == set()
  assert shown(step(s, NOT_DETECTED_AFTER_S + 1, cable=False)) == {NOT_DETECTED}
  assert shown(step(s, NOT_DETECTED_AFTER_S + 2, cable=True)) == set()


def test_a_cable_that_was_there_and_is_lost_while_driving_is_not_detected_until_it_returns_parked():
  s = PhoneLinkStatus(0.0)
  step(s, 1.0, offroad=True, cable=True)
  step(s, 2.0, offroad=False, cable=True)                       # drive starts with the cable there
  assert shown(step(s, 3.0, offroad=False, cable=False)) == {NOT_DETECTED}
  assert shown(step(s, 4.0, offroad=False, cable=True)) == {NOT_DETECTED}   # sticky while driving, as upstream's usb_failed
  assert shown(step(s, 5.0, offroad=True, cable=True)) == set()


def test_a_link_below_usb3_is_slow_with_its_speed_offroad_only():
  s = PhoneLinkStatus(0.0)
  alerts = step(s, 1.0, speed=480.0)
  assert shown(alerts) == {USB_SLOW} and alerts[USB_SLOW][1] == "480 Mbps"
  assert shown(step(s, 2.0, speed=SLOW_MBPS)) == set()          # 5 Gbps is fine: upstream's `speedMbps < 5000`
  assert shown(step(s, 3.0, speed=None)) == set()               # unknown is not slow
  assert shown(step(s, 4.0, offroad=False, speed=480.0)) == set()


def test_a_model_that_finished_loading_and_is_not_active_is_a_model_error_for_the_drive():
  s = PhoneLinkStatus(0.0)
  step(s, 1.0, offroad=False, cable=True, loading=True, active=None)
  assert shown(step(s, 2.0, offroad=False, loading=False, active=None)) == set()   # no attempt result yet
  assert shown(step(s, 3.0, offroad=False, loading=False, active=False)) == {MODEL_ERROR}
  assert shown(step(s, 4.0, offroad=False, loading=False, active=True)) == {MODEL_ERROR}   # sticky for the drive
  step(s, 5.0, offroad=True)
  step(s, 6.0, offroad=False, cable=True, loading=False, active=None)                     # a new drive resets it
  assert shown(step(s, 7.0, offroad=False)) == set()


def test_a_failure_without_a_cable_is_not_a_model_error_and_not_detected_explains_it():
  s = PhoneLinkStatus(0.0)
  step(s, 1.0, offroad=False, cable=True, loading=True)
  alerts = step(s, 2.0, offroad=False, cable=False, loading=False, active=False)
  assert shown(alerts) == {NOT_DETECTED}                         # ModelError is suppressed when the cable explains it


def test_a_drive_that_never_loaded_a_model_does_not_cry_wolf():
  s = PhoneLinkStatus(0.0)
  step(s, 1.0, offroad=False, cable=True, loading=False, active=None)
  assert shown(step(s, 100.0, offroad=False, cable=True, loading=False, active=None)) == set()
