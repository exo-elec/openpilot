from nagaspilot.controls.ngp_bigmodel_status import MODEL_ERROR, NOT_DETECTED, USB_SLOW
from nagaspilot.runtime import bigmodel_platform as platform
from nagaspilot.runtime.bigmodel_link import PhoneLinkMonitor, cable_connected, udc_speed_mbps


def udc(root, state, speed):
  d = root / 'fc000000.usb'
  d.mkdir(exist_ok=True)
  (d / 'state').write_text(state + '\n')
  (d / 'current_speed').write_text(speed + '\n')


class Params:
  def __init__(self, **values):
    self.values = values

  def get(self, key, return_default=False):
    return self.values.get(key)

  def get_bool(self, key):
    return bool(self.values.get(key))


def test_speed_names_map_to_mbps_and_unknown_is_none(tmp_path):
  assert udc_speed_mbps(tmp_path / 'missing') is None
  for name, mbps in (('super-speed', 5000.0), ('super-speed-plus', 10000.0), ('high-speed', 480.0), ('full-speed', 12.0)):
    udc(tmp_path, 'configured', name)
    assert udc_speed_mbps(tmp_path) == mbps
  udc(tmp_path, 'not attached', 'UNKNOWN')
  assert udc_speed_mbps(tmp_path) is None


def test_the_monitor_sets_the_three_alerts_through_the_hardwared_helper(tmp_path):
  udc(tmp_path, 'configured', 'high-speed')
  calls = []
  monitor = PhoneLinkMonitor(clock=lambda: 100.0, root=tmp_path)
  monitor.update(Params(**{platform.LINK: 2}), True, lambda name, show, extra: calls.append((name, show, extra)))
  assert {c[0] for c in calls} == {NOT_DETECTED, USB_SLOW, MODEL_ERROR}
  assert [c for c in calls if c[0] == USB_SLOW] == [(USB_SLOW, True, '480 Mbps')]
  assert not [c for c in calls if c[0] in (NOT_DETECTED, MODEL_ERROR) and c[1]]
  assert cable_connected(tmp_path)


def test_an_unset_link_shows_nothing(tmp_path):
  calls = []
  PhoneLinkMonitor(clock=lambda: 1000.0, root=tmp_path).update(Params(), True, lambda n, s, e: calls.append((n, s, e)))
  assert calls and not any(show for _, show, _ in calls)


def test_a_monitor_failure_never_reaches_hardwared(tmp_path):
  class Broken(Params):
    def get(self, key, return_default=False):
      raise OSError('params')

  calls = []
  PhoneLinkMonitor(root=tmp_path).update(Broken(), True, lambda *a: calls.append(a))
  assert calls == []


def test_the_monitor_only_sets_an_alert_when_it_changes(tmp_path):
  udc(tmp_path, 'configured', 'high-speed')
  calls = []
  monitor = PhoneLinkMonitor(clock=lambda: 100.0, root=tmp_path)
  params = Params(**{platform.LINK: 2})
  for _ in range(5):
    monitor.update(params, True, lambda name, show, extra: calls.append((name, show, extra)))
  assert len(calls) == 3                                          # once each, not five cycles of three
  udc(tmp_path, 'configured', 'super-speed')
  monitor.update(params, True, lambda name, show, extra: calls.append((name, show, extra)))
  assert calls[3:] == [(USB_SLOW, False, None)]                   # and again only for what changed
