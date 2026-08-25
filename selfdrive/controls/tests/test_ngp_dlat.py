from types import SimpleNamespace

from nagaspilot.controls.ngp_dlat import DLATSuggestion, NGPDLAT


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
  assert NGPDLAT.lane_confidence([0.0, 1.0, 1.0, 0.0]) == 0.8
  assert NGPDLAT.lane_confidence([1.0]) == 0.5


def test_low_confidence_requires_hysteresis():
  dlat = NGPDLAT(enter_frames=3, exit_frames=2)
  assert dlat.update([0.0, 0.0, 0.0, 0.0]) is DLATSuggestion.LANEFUL
  assert dlat.update([0.0, 0.0, 0.0, 0.0]) is DLATSuggestion.LANEFUL
  assert dlat.update([0.0, 0.0, 0.0, 0.0]) is DLATSuggestion.LANELESS


def test_recovery_requires_high_confidence_hysteresis():
  dlat = NGPDLAT(enter_frames=1, exit_frames=2)
  dlat.update([0.0, 0.0, 0.0, 0.0])
  assert dlat.suggestion is DLATSuggestion.LANELESS
  assert dlat.update([1.0, 1.0, 1.0, 1.0]) is DLATSuggestion.LANELESS
  assert dlat.update([1.0, 1.0, 1.0, 1.0]) is DLATSuggestion.LANEFUL


def test_v010_model_adapter_uses_position_schema():
  result = NGPDLAT().update_model(model_sample(path_y=1.5), v_ego=20.0)
  assert result.model_valid
  assert result.path_confidence == 0.8
  assert result.path_deviation == 1.5
  assert result.road_edge_confidence == 2.0 / 3.0
  assert not result.curve_detected
  assert result.suggestion is DLATSuggestion.LANEFUL


def test_model_adapter_curve_assist_forces_laneless_by_default():
  """DLP curve assist (ngp_lat_dlp_curves, default on) pre-emptively forces
  laneless on a predicted tight curve, bypassing the hysteresis frame
  counters entirely -- unlike ordinary low-confidence switching, one frame
  is enough."""
  dlat = NGPDLAT(enter_frames=20)  # would NOT be enough via ordinary hysteresis
  result = dlat.update_model(model_sample(yaw_rate=1.2), v_ego=20.0)
  assert result.curve_detected
  assert result.suggestion is DLATSuggestion.LANELESS


def test_model_adapter_curve_assist_disabled_reports_without_controlling():
  """curve_assist_enabled=False (ngp_lat_dlp_curves off) must still report
  curve_detected for logging/replay -- deliberately different from EOP10's
  _predict_curve(), which returns False outright when its own toggle is off
  and therefore never reports detection at all -- but must not force a
  switch."""
  dlat = NGPDLAT(enter_frames=20)
  result = dlat.update_model(model_sample(yaw_rate=1.2), v_ego=20.0, curve_assist_enabled=False)
  assert result.curve_detected
  assert result.suggestion is DLATSuggestion.LANEFUL


def test_force_laneless_bypasses_enter_hysteresis():
  dlat = NGPDLAT(enter_frames=20, exit_frames=2)
  # Build up low-confidence frames short of the enter threshold.
  dlat.update([0.0, 0.0, 0.0, 0.0])
  dlat.update([0.0, 0.0, 0.0, 0.0])
  assert dlat.suggestion is DLATSuggestion.LANEFUL
  # Force switches immediately even on a single, high-confidence frame --
  # entry doesn't wait for the ordinary low-confidence frame count.
  assert dlat.update([0.9, 0.9, 0.9, 0.9], force_laneless=True) is DLATSuggestion.LANELESS


def test_force_laneless_does_not_block_exit_accumulation():
  """After a forced entry, continued high-confidence frames -- with no more
  forcing -- must accumulate toward the ordinary exit normally, not restart
  from zero. This is what a curve genuinely ending looks like."""
  dlat = NGPDLAT(enter_frames=1, exit_frames=2)
  dlat.update([0.0, 0.0, 0.0, 0.0], force_laneless=True)
  assert dlat.suggestion is DLATSuggestion.LANELESS
  assert dlat.update([0.9, 0.9, 0.9, 0.9]) is DLATSuggestion.LANELESS  # 1 of 2 exit_frames
  assert dlat.update([0.9, 0.9, 0.9, 0.9]) is DLATSuggestion.LANEFUL  # 2 of 2 -- exits


def test_sustained_forced_curve_does_not_latch_if_confidence_recovers():
  """EOP10's own DLATState.laneless branch checks lane confidence and
  elapsed time only -- it ignores curve_detected/force_laneless entirely
  once already in laneless. So a long, gentle, well-marked curve (confidence
  high the whole time, force_laneless also true the whole time) must still
  exit once enough high-confidence frames accumulate, rather than latching
  laneless until curve_detected itself goes False."""
  dlat = NGPDLAT(enter_frames=1, exit_frames=5)
  dlat.update([0.0, 0.0, 0.0, 0.0], force_laneless=True)
  assert dlat.suggestion is DLATSuggestion.LANELESS
  for _ in range(4):
    dlat.update([0.9, 0.9, 0.9, 0.9], force_laneless=True)
  assert dlat.suggestion is DLATSuggestion.LANELESS  # only 4 of 5 exit_frames so far
  # 5th consecutive high-confidence frame, forcing still active -- must exit anyway.
  assert dlat.update([0.9, 0.9, 0.9, 0.9], force_laneless=True) is DLATSuggestion.LANEFUL


def test_missing_model_stays_neutral_and_laneful():
  dlat = NGPDLAT(enter_frames=1)
  for model in (None, SimpleNamespace(laneLineProbs=[])):
    result = dlat.update_model(model)
    assert not result.model_valid
    assert result.lane_confidence == 0.5
    assert result.path_deviation is None
    assert result.suggestion is DLATSuggestion.LANEFUL
