from types import SimpleNamespace as NS
import sys

import cereal.messaging as messaging
from openpilot.selfdrive.gridd.gridd import GridD
from nagaspilot.runtime.object_sources import GriddSource


def test_platform_fusion_keeps_classes_on_real_wire_for_shared_planner(monkeypatch):
  module = sys.modules[GridD.__module__]
  monkeypatch.setattr(module, 'fill_grid_objects', lambda *args: None)
  messages = {}
  host = NS(bev=NS(get_grid=lambda: None), frame_id=1,
            pm=NS(send=lambda name, msg: messages.update({name: msg})),
            _extract_road_boundaries=lambda points: ([], []),
            _cam_tracks=NS(annotate=lambda objects: None))
  objects = [dict(dRel=10, yRel=2, obstacleType=name, confidence=0.9, trackId=i + 1)
             for i, name in enumerate(('truck', 'bus', 'bicycle', 'person'))]
  GridD._publish(host, 1, None, False, None, objects)
  wire = messages['stereoObjects'].to_bytes()
  with messaging.log.Event.from_bytes(wire) as message:
    sm = type('SM', (dict,), {})({'stereoObjects': message.stereoObjects})
    sm.alive = sm.valid = {'stereoObjects': True}
    result, fresh = GriddSource().objects(sm)
    assert fresh and [o.name for o in result] == ['truck', 'bus', 'bicycle', 'person']
