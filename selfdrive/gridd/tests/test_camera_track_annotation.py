"""gridd (the fusion) adds relative and lateral speed to camera objects, so pathd (the planner) can predict a cut-in."""
from nagaspilot.runtime.fusion_tracks import CameraTrackAnnotator


def test_gridd_style_object_dicts_get_vyrel_and_vrel():
  t = [0.0]
  a = CameraTrackAnnotator(lambda: t[0])
  last = None
  for i in range(30):
    t[0] += 0.05
    last = [{'dRel': 40.0 - 0.4 * i, 'yRel': 4.0 - 0.075 * i, 'obstacleType': 'car', 'confidence': 0.9, 'trackId': 1, 'source': 'road', 'vRel': 0.0}]
    a.annotate(last)
  assert abs(last[0]['vyRel'] + 1.5) < 0.6 and abs(last[0]['vRel'] + 8.0) < 1.0
