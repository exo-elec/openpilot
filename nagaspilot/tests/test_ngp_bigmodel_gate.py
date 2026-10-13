from nagaspilot.controls.ngp_bigmodel_gate import SETTLING_S, NGPBigModelGate, tier_flag


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


def test_events_reads_the_phone_flags_and_names_events():
  params = {}
  g = NGPBigModelGate()
  assert g.events(params.get, True, now=0) == []
  params["BigModelLoading"] = True
  assert g.events(params.get, True, now=1) == ["bigModelLoading"]
  params.update(BigModelLoading=False, BigModelActive=True)
  assert g.events(params.get, True, now=2) == []
  params["BigModelActive"] = False
  assert g.events(params.get, True, now=3) == ["bigModelFailed"]


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


def test_another_branch_names_its_own_keys_and_never_reads_the_defaults():
  asked = []
  params = {"EOPBigModelLoading": True}

  def get(key):
    asked.append(key)
    return params.get(key)

  g = NGPBigModelGate(("EOPBigModelLoading",), ("EOPBigModelActive",))
  assert g.events(get, True, now=0) == ["bigModelLoading"]
  assert not {"BigModelLoading", "BigModelActive"} & set(asked)
  assert {"EOPBigModelLoading", "EOPBigModelActive"} <= set(asked)


def test_params_are_read_at_most_every_period_not_every_cycle():
  reads = []

  def get(key):
    reads.append(key)
    return None

  g = NGPBigModelGate()
  for i in range(100):  # a second of selfdrived cycles at 100 Hz
    g.events(get, True, now=i * 0.01)
  per_pass = len(g._loading_keys) + len(g._active_keys)
  assert len(reads) == 4 * per_pass  # reads at t = 0, 0.25, 0.5, 0.75


def test_a_change_is_seen_within_one_period_and_the_gate_state_still_updates_every_cycle():
  params = {"BigModelActive": True}
  g = NGPBigModelGate()
  g.events(params.get, True, now=0.0)
  params["BigModelActive"] = False
  assert g.events(params.get, True, now=0.1) == []                 # still the cached reading
  assert g.events(params.get, True, now=0.26) == ["bigModelFailed"]  # read again


def test_settling_is_upstreams_five_seconds_around_a_load_and_after_a_failure():
  assert SETTLING_S == 5.0
  g = NGPBigModelGate()
  assert not g.settling(now=0.0)                                   # nothing configured: never settling
  g.update(True, None, now=10.0)
  assert g.settling(now=10.0) and g.settling(now=500.0)           # while loading, however long
  g.update(False, True, now=20.0)                                  # the load ended
  assert g.settling(now=24.9) and not g.settling(now=25.1)
  g.update(False, False, now=100.0)                                # a failure restarts it
  assert g.settling(now=104.0) and not g.settling(now=105.1)


def test_a_failure_that_is_already_announced_does_not_restart_settling():
  g = NGPBigModelGate()
  g.update(False, False, now=0.0)
  assert g.settling(now=1.0)
  g.update(False, False, now=50.0)                                 # same failed state, no new announcement
  assert not g.settling(now=51.0)


def test_events_feeds_the_settling_clock():
  params = {"BigModelLoading": True}
  g = NGPBigModelGate()
  g.events(params.get, True, now=0.0)
  assert g.settling(now=1.0)
  params["BigModelLoading"] = False
  g.events(params.get, True, now=1.0)
  assert g.settling(now=5.0) and not g.settling(now=6.1)
