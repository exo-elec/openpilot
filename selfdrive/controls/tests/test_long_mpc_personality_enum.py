import cereal.messaging as messaging
from cereal import log
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib import long_mpc
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import get_T_FOLLOW, get_jerk_factor

P = log.LongitudinalPersonality


def _from_message(value):
  msg = messaging.new_message('selfdriveState')
  msg.selfdriveState.personality = value
  return messaging.log_from_bytes(msg.to_bytes()).selfdriveState.personality


def test_every_personality_resolves_from_a_message_enum(monkeypatch):
  monkeypatch.setattr(long_mpc, '_load_personality_params',
                      lambda now=None: {n: d.copy() for n, d in
                                        ((name, long_mpc._PERSONALITY_DEFAULTS[getattr(P, name)]) for name in ('aggressive', 'standard', 'relaxed', 'traffic'))})
  for name in ('aggressive', 'standard', 'relaxed', 'traffic'):
    value = getattr(P, name)
    enum = _from_message(value)
    expected = long_mpc._PERSONALITY_DEFAULTS[value]
    assert get_T_FOLLOW(enum) == expected['t_follow'], name
    assert get_jerk_factor(enum) == expected['jerk'], name


def test_plain_int_and_unknown_values_still_work(monkeypatch):
  monkeypatch.setattr(long_mpc, '_load_personality_params',
                      lambda now=None: {n: long_mpc._PERSONALITY_DEFAULTS[getattr(P, n)].copy() for n in ('aggressive', 'standard', 'relaxed', 'traffic')})
  assert get_T_FOLLOW(P.relaxed) == long_mpc._PERSONALITY_DEFAULTS[P.relaxed]['t_follow']
  assert get_T_FOLLOW(99) == long_mpc._PERSONALITY_DEFAULTS[P.standard]['t_follow']
