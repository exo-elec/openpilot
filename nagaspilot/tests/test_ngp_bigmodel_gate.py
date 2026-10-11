from nagaspilot.controls.ngp_bigmodel_gate import NGPBigModelGate, tier_flag


def test_nothing_written_raises_nothing():
  out = NGPBigModelGate().update(False, None)
  assert not (out.loading or out.failed or out.announce_failed or out.soft_disable)


def test_loading_blocks_entry_only():
  out = NGPBigModelGate().update(True, None)
  assert out.loading and not out.failed


def test_active_big_model_is_quiet():
  g = NGPBigModelGate()
  out = g.update(False, True)
  assert not (out.failed or out.soft_disable)


def test_load_failure_announces_once_without_soft_disable():
  g = NGPBigModelGate()
  first = g.update(False, False)
  assert first.failed and first.announce_failed and not first.soft_disable
  again = g.update(False, False)
  assert again.failed and not again.announce_failed and not again.soft_disable


def test_failure_after_driving_soft_disables_every_cycle_until_recovered():
  g = NGPBigModelGate()
  g.update(False, True)
  first = g.update(False, False)
  assert first.announce_failed and first.soft_disable
  assert g.update(False, False).soft_disable
  # the link rejoins: failed state clears, and a second failure announces again
  assert not g.update(False, True).failed
  assert g.update(False, False).announce_failed


def test_stalled_model_stream_while_active_is_a_failure():
  g = NGPBigModelGate()
  g.update(False, True)
  out = g.update(False, True, model_dead=True)
  assert out.failed and out.soft_disable


def test_stalled_stream_without_a_big_model_is_not_ours():
  assert not NGPBigModelGate().update(False, None, model_dead=True).failed


def test_tier_flag_combines_sources():
  assert tier_flag(None, None) is None
  assert tier_flag(None, False) is False
  assert tier_flag(False, True) is True


def test_events_reads_both_sources_and_names_events():
  params = {}
  g = NGPBigModelGate()
  assert g.events(params.get, True) == []
  params["EgpuDrivingLoading"] = True
  assert g.events(params.get, True) == ["bigModelLoading"]
  params.update(EgpuDrivingLoading=False, BigModelActive=True)
  assert g.events(params.get, True) == []
  params["BigModelActive"] = False
  assert g.events(params.get, True) == ["bigModelFailed"]


def test_reengaging_on_the_small_model_after_a_handback_does_not_soft_disable_again():
  g = NGPBigModelGate()
  g.update(False, True)
  engaged_fail = g.update(False, False, enabled=True)
  assert engaged_fail.soft_disable
  # the soft disable lands: the driver is disengaged, the latch clears
  g.update(False, False, enabled=False)
  again = g.update(False, False, enabled=True)
  assert again.failed and not again.soft_disable and not again.announce_failed


def test_a_stalled_stream_keeps_the_latch_while_disengaged():
  g = NGPBigModelGate()
  g.update(False, True)
  g.update(False, True, enabled=False, model_dead=True)
  assert g.update(False, True, enabled=True, model_dead=True).soft_disable
