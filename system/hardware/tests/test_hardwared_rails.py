"""hardwared rail discovery and powerState publishing against a fake sysfs."""
import os

import pytest

from openpilot.system.hardware import hardwared as hw


def _reg(root, n, name, uv=None, min_uv=None, state="enabled"):
  d = root / f"regulator.{n}"
  d.mkdir()
  (d / "name").write_text(name + "\n")
  if uv is not None:
    (d / "microvolts").write_text(f"{uv}\n")
  if min_uv is not None:
    (d / "min_microvolts").write_text(f"{min_uv}\n")
  (d / "state").write_text(state + "\n")
  return d


@pytest.fixture
def sysfs(tmp_path, monkeypatch):
  root = tmp_path / "regulator"
  root.mkdir()
  monkeypatch.setattr(hw, "REGULATOR_SYSFS", str(root))
  return root


def test_discovers_by_name_and_skips_voltageless(sysfs):
  _reg(sysfs, 0, "vdd_cpu_big", 900000, 550000)
  _reg(sysfs, 1, "vcc_3v3_s3", 3300000, 3300000)
  _reg(sysfs, 2, "regulator-dummy")            # no microvolts: not monitored
  _reg(sysfs, 3, "vdd_cpu_big", 800000)          # duplicate name: first wins
  rails = hw.discover_regulators()
  assert list(rails) == ["vdd_cpu_big", "vcc_3v3_s3"]
  assert rails["vdd_cpu_big"].endswith("regulator.0")


def test_devfreq_governor_discovery(tmp_path):
  (tmp_path / "ffa30000.npu").mkdir()
  (tmp_path / "ffa30000.npu" / "governor").write_text("simple_ondemand")
  (tmp_path / "dmc").mkdir()
  (tmp_path / "dmc" / "governor").write_text("dmc_ondemand")
  assert hw.discover_devfreq_governor("npu", str(tmp_path)).endswith("ffa30000.npu/governor")
  assert hw.discover_devfreq_governor("dmc", str(tmp_path)).endswith("dmc/governor")
  assert hw.discover_devfreq_governor("gpu", str(tmp_path)) is None


class _PM:
  def __init__(self):
    self.sent = []

  def send(self, name, msg):
    self.sent.append((name, msg.as_reader()))


def test_update_publishes_rail_status(sysfs, monkeypatch):
  _reg(sysfs, 0, "vdd_cpu_big", 900000, 550000)     # nominal 0.55 V (DT min)
  low = _reg(sysfs, 1, "vcc_1v8", 1800000, 1800000)  # nominal 1.8 V
  _reg(sysfs, 2, "vcc_nodt", 3300000, state="disabled")  # no DT min: first reading
  monkeypatch.setattr(hw, "set_daemon_affinity", lambda name: None)
  monkeypatch.setattr(hw.HardwareD, "_init_hardware", lambda self: True)
  d = hw.HardwareD()
  d.pm = _PM()

  d.update()
  (low / "microvolts").write_text("1600000\n")        # 0.89 x nominal -> critical
  d.update()

  name, msg = d.pm.sent[-1]
  assert name == "powerState"
  rails = {r.name: r for r in msg.powerState.rails}
  assert set(rails) == {"vdd_cpu_big", "vcc_1v8", "vcc_nodt"}
  assert str(rails["vdd_cpu_big"].status) == "ok"
  assert str(rails["vcc_1v8"].status) == "critical"
  assert rails["vcc_1v8"].voltage == pytest.approx(1.6)
  assert rails["vcc_nodt"].enabled is False and str(rails["vcc_nodt"].status) == "ok"
  assert d.last_under_voltage_report == ["vcc_1v8(1.60V)"]


def test_no_regulators_still_publishes(sysfs, monkeypatch):
  monkeypatch.setattr(hw, "set_daemon_affinity", lambda name: None)
  monkeypatch.setattr(hw.HardwareD, "_init_hardware", lambda self: True)
  d = hw.HardwareD()
  d.pm = _PM()
  d.update()
  assert len(d.pm.sent[-1][1].powerState.rails) == 0
  assert os.path.isdir(sysfs)
