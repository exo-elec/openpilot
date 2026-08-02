from types import SimpleNamespace

from nagaspilot.controls.ngp_vtsc import NGPVTSC, VTSCState


def model(rates, velocities):
  return SimpleNamespace(orientationRate=SimpleNamespace(z=rates), velocity=SimpleNamespace(x=velocities))


def test_vtsc_shadow_enters_and_produces_target():
  vtsc = NGPVTSC()
  assert vtsc.update(20.0, model([0.0], [20.0])).state is VTSCState.ENABLED
  result = vtsc.update(20.0, model([0.15] * 10, [20.0] * 10))
  assert result.state is VTSCState.ENTERING
  assert result.target_speed is not None
  assert result.target_speed < 20.0


def test_vtsc_turning_then_leaves_without_target():
  vtsc = NGPVTSC()
  vtsc.update(20.0, model([0.0], [20.0]))
  vtsc.update(20.0, model([0.15] * 10, [20.0] * 10))
  vtsc.update(20.0, model([0.15] * 10, [20.0] * 10))
  result = vtsc.update(20.0, model([0.02] * 10, [20.0] * 10))
  assert result.state is VTSCState.LEAVING
  assert result.target_speed is None


def test_vtsc_missing_model_and_disable_are_safe():
  vtsc = NGPVTSC()
  result = vtsc.update(20.0, None)
  assert result.state is VTSCState.ENABLED
  assert result.target_speed is None
  result = vtsc.update(20.0, {}, enabled=False)
  assert result.state is VTSCState.DISABLED
  assert result.target_speed is None
