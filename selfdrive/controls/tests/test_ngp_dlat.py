from openpilot.selfdrive.controls.lib.ngp_dlat import DLATSuggestion, NGP10DLAT


def test_lane_confidence_weights_inner_lines():
  assert NGP10DLAT.lane_confidence([0.0, 1.0, 1.0, 0.0]) == 0.8
  assert NGP10DLAT.lane_confidence([1.0]) == 0.5


def test_low_confidence_requires_hysteresis():
  dlat = NGP10DLAT(enter_frames=3, exit_frames=2)
  assert dlat.update([0.0, 0.0, 0.0, 0.0]) is DLATSuggestion.LANEFUL
  assert dlat.update([0.0, 0.0, 0.0, 0.0]) is DLATSuggestion.LANEFUL
  assert dlat.update([0.0, 0.0, 0.0, 0.0]) is DLATSuggestion.LANELESS


def test_recovery_requires_high_confidence_hysteresis():
  dlat = NGP10DLAT(enter_frames=1, exit_frames=2)
  dlat.update([0.0, 0.0, 0.0, 0.0])
  assert dlat.suggestion is DLATSuggestion.LANELESS
  assert dlat.update([1.0, 1.0, 1.0, 1.0]) is DLATSuggestion.LANELESS
  assert dlat.update([1.0, 1.0, 1.0, 1.0]) is DLATSuggestion.LANEFUL
