from types import SimpleNamespace

from openpilot.selfdrive.controls.lib.ngp_dlat import DLATSuggestion, NGP10DLAT


def model_sample(lane_probs=(0.9, 0.9, 0.9, 0.9), path_y=0.0, path_std=0.25,
                 lane_center=0.0, yaw_rate=0.0):
  def line(y):
    return SimpleNamespace(y=[y] * 10)

  return SimpleNamespace(
    laneLineProbs=list(lane_probs),
    laneLines=[line(lane_center - 3.5), line(lane_center - 1.75),
               line(lane_center + 1.75), line(lane_center + 3.5)],
    position=SimpleNamespace(y=[path_y] * 10, yStd=[path_std] * 10),
    orientationRate=SimpleNamespace(z=[yaw_rate] * 10),
    roadEdgeStds=[0.5, 0.5],
  )


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


def test_v010_model_adapter_uses_position_schema():
  result = NGP10DLAT().update_model(model_sample(path_y=1.5), v_ego=20.0)
  assert result.model_valid
  assert result.path_confidence == 0.8
  assert result.path_deviation == 1.5
  assert result.road_edge_confidence == 2.0 / 3.0
  assert not result.curve_detected
  assert result.suggestion is DLATSuggestion.LANEFUL


def test_model_adapter_reports_curve_without_controlling():
  dlat = NGP10DLAT(enter_frames=1)
  result = dlat.update_model(model_sample(yaw_rate=1.2), v_ego=20.0)
  assert result.curve_detected
  assert result.suggestion is DLATSuggestion.LANEFUL


def test_missing_model_stays_neutral_and_laneful():
  dlat = NGP10DLAT(enter_frames=1)
  for model in (None, SimpleNamespace(laneLineProbs=[])):
    result = dlat.update_model(model)
    assert not result.model_valid
    assert result.lane_confidence == 0.5
    assert result.path_deviation is None
    assert result.suggestion is DLATSuggestion.LANEFUL
