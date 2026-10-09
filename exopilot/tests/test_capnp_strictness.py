"""pycapnp 2.x rejects IntEnum, numpy scalars and list.append on builders; Params is typed per key."""
from datetime import UTC, datetime
from enum import IntEnum

import numpy as np
import cereal.messaging as messaging
from openpilot.common.params import Params


class _State(IntEnum):
  enabled = 2


def test_enum_fields_take_int_not_intenum():
  a = messaging.new_message('alccState').alccState
  a.state = int(_State.enabled)
  assert str(a.state) == 'enabled'


def test_list_fields_take_python_floats():
  msg = messaging.new_message('enhancedTrajectory')
  msg.enhancedTrajectory.v = [float(x) for x in np.array([1.0, 2.0], dtype=np.float32)]
  assert list(msg.enhancedTrajectory.v) == [1.0, 2.0]


def test_power_rails_are_built_with_init_and_index():
  from cereal import log
  ps = messaging.new_message('hardwareState' if 'hardwareState' in messaging.SERVICE_LIST else 'deviceState')
  state = getattr(ps, ps.which())
  if not hasattr(state, 'rails'):
    state = messaging.new_message('powerState').powerState
  rails = state.init('rails', 1)
  rails[0].status = int(log.PowerState.RailStatus.schema.fields['status'].schema.enumerants['ok'])
  assert len(state.rails) == 1


def test_updater_params_use_their_declared_types(tmp_path):
  p = Params()
  p.put("InstallDate", datetime.now(UTC))
  p.put("UpdateFailedCount", 0)
  p.put("LastUpdateTime", datetime.now(UTC))
  p.put("UpdaterNewReleaseNotes", b"notes\n")


def test_submaster_poll_is_a_service_name():
  messaging.SubMaster(["pointcloudProcessed", "gpsLocation"], poll="pointcloudProcessed")
