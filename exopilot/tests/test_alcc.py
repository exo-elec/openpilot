from exopilot.selfdrive.controls.lib.alcc import ALCC, ALCCInput, ALCCState, BrakeMode


def engaged(mode):
  alcc = ALCC(mode)
  assert alcc.update(ALCCInput(True, engage_request=True)).state is ALCCState.ENABLED
  return alcc


def test_maintain_keeps_steering_on_brake():
  alcc = engaged(BrakeMode.MAINTAIN)
  assert alcc.update(ALCCInput(True, engage_request=True, brake_pressed=True)).state is ALCCState.ENABLED


def test_pause_resumes_when_brake_released():
  alcc = engaged(BrakeMode.PAUSE)
  paused = alcc.update(ALCCInput(True, engage_request=True, brake_pressed=True))
  assert paused.state is ALCCState.PAUSED and paused.latched and not paused.active_suggestion
  assert alcc.update(ALCCInput(True, engage_request=True)).state is ALCCState.ENABLED


def test_disengage_needs_a_button_press_to_engage_again():
  alcc = engaged(BrakeMode.DISENGAGE)
  released = alcc.update(ALCCInput(True, engage_request=True, brake_pressed=True))
  assert released.state is ALCCState.DISABLED and not released.latched
  # cruise main still on: a level-held engage request does not latch it again
  for _ in range(10):
    assert alcc.update(ALCCInput(True, engage_request=True)).state is ALCCState.DISABLED
  assert alcc.update(ALCCInput(True, engage_request=True, engage_button=True)).state is ALCCState.ENABLED


def test_disengage_rearms_when_cruise_main_cycles():
  alcc = engaged(BrakeMode.DISENGAGE)
  alcc.update(ALCCInput(True, engage_request=True, brake_pressed=True))
  # main off: NGP10's controlsd sends engage_request False with user_disable
  assert alcc.update(ALCCInput(True, engage_request=False, user_disable=True)).state is ALCCState.DISABLED
  assert alcc.update(ALCCInput(True, engage_request=True)).state is ALCCState.ENABLED


def test_disengage_brake_held_through_the_button_stays_released():
  alcc = engaged(BrakeMode.DISENGAGE)
  assert alcc.update(ALCCInput(True, engage_request=True, brake_pressed=True, engage_button=True)).state is ALCCState.DISABLED


def test_override_never_requests_authority_in_any_mode():
  for mode in BrakeMode:
    alcc = engaged(mode)
    result = alcc.update(ALCCInput(True, engage_request=True, steering_override=True))
    assert result.state is ALCCState.OVERRIDING and not result.active_suggestion and not result.control_authority


def test_brake_mode_from_saved_param():
  assert BrakeMode.from_param(None) is BrakeMode.MAINTAIN
  assert BrakeMode.from_param(1) is BrakeMode.PAUSE
  assert BrakeMode.from_param("2") is BrakeMode.DISENGAGE
  assert BrakeMode.from_param(7) is BrakeMode.MAINTAIN
  assert BrakeMode.from_param("Maintain") is BrakeMode.MAINTAIN
