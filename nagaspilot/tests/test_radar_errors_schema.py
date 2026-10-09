"""RadarData.errors is opendbc's Error struct, not the fork's list of enum values."""
from cereal import car, log


def test_radar_data_errors_is_a_struct_with_radar_fault():
  rr = car.RadarData.new_message()
  rr.errors.radarFault = True
  assert rr.errors.radarFault and not rr.errors.canError


def test_radard_copies_the_struct_into_radar_state():
  rr = car.RadarData.new_message()
  rr.errors.radarFault = True
  radar_state = log.RadarState.new_message()
  radar_state.radarErrors = rr.errors
  assert radar_state.radarErrors.radarFault
