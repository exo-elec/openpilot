"""RadarPoint's aRel/yvRel/measured live in the `deprecated` group in opendbc's car.capnp."""
import cereal.messaging as messaging
from cereal import car
from openpilot.selfdrive.controls.radard import RadarD


def _radar_data(link_ok):
  rr = car.RadarData.new_message()
  rr.errors.radarFault = not link_ok
  pts = rr.init('points', 1)
  pts[0].trackId = 1
  pts[0].dRel = 30.0
  pts[0].yRel = 0.0
  pts[0].vRel = -1.0
  pts[0].deprecated.aRel = float('nan')
  pts[0].deprecated.yvRel = float('nan')
  pts[0].deprecated.measured = True
  return rr


def test_radar_point_extras_are_in_the_deprecated_group():
  pt = _radar_data(True).points[0]
  assert pt.deprecated.measured is True
  assert not hasattr(car.RadarData.RadarPoint.schema.fields, 'measured')
  assert 'measured' not in car.RadarData.RadarPoint.schema.fields


def test_radard_consumes_a_radar3d_style_message_for_both_link_states():
  rd = RadarD(0.0)
  sm = messaging.SubMaster(['modelV2', 'carState', 'radar3d'])
  pm = messaging.PubMaster(['radarState'])
  for link_ok in (True, False):
    rd.update(sm, _radar_data(link_ok))
    rd.publish(pm)
    assert rd.radar_state.radarErrors.radarFault == (not link_ok)
