import pytest
from nagaspilot.runtime.device_policy import RecordingDelay, audible_alert, custom_shutdown_due, vehicle_override


@pytest.mark.parametrize('ignition,in_car,disabled,seen', [(True,True,False,True),(False,False,False,True),(False,True,True,True),(False,True,False,False)])
def test_custom_shutdown_keeps_power_safeguards(ignition,in_car,disabled,seen):
  assert not custom_shutdown_due(0, 900, ignition, in_car, disabled, seen)


def test_shutdown_stock_default_and_minimum_grace():
  assert not custom_shutdown_due(-1, 99999, False, True, False, True)
  assert not custom_shutdown_due(0, 299, False, True, False, True)
  assert custom_shutdown_due(0, 300, False, True, False, True)
  assert not custom_shutdown_due(10, 599, False, True, False, True)
  assert custom_shutdown_due(10, 600, False, True, False, True)


def test_recording_delay_resets_each_drive_and_does_not_block_control():
  delay=RecordingDelay()
  assert delay.blocked(True, 100, 10)==['loggerd','encoderd']
  assert delay.blocked(True, 110, 10)==[]
  assert delay.blocked(False, 120, 10)==[]
  assert delay.blocked(True, 150, 10)==['loggerd','encoderd']
  assert delay.blocked(True, 150, 0)==[]


def test_quiet_chimes_preserve_every_safety_warning():
  assert audible_alert('engage',1)=='none'
  assert audible_alert('disengage',1)=='none'
  for sound in ['warningImmediate','warningSoft','prompt','refuse','promptDistracted']:
    assert audible_alert(sound,1)==sound
  assert audible_alert('engage',0)=='engage'


def test_vehicle_selection_accepts_only_installed_interfaces():
  assert vehicle_override(b'TESLA',{'TESLA'})=='TESLA'
  assert vehicle_override('UNKNOWN',{'TESLA'}) is None
  assert vehicle_override('',{'TESLA'}) is None
