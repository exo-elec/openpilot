"""HARDWARE.hal_import / load_stereo_intrinsics: the one seam selfdrive/ uses
to reach exopilot's hal (None when absent, as hal_module)."""
import re
from pathlib import Path
from types import SimpleNamespace

from openpilot.system.hardware.base import HardwareBase

ROOT = Path(__file__).resolve().parents[3]


def test_hal_import_returns_the_module_or_none():
  seen = []

  def importer(name):
    seen.append(name)
    if name == "hal.tuning.npu":
      raise ImportError
    return SimpleNamespace(name=name)

  assert HardwareBase.hal_import("drivers.camera", import_module=importer).name == "hal.drivers.camera"
  assert HardwareBase.hal_import("tuning.npu", import_module=importer) is None   # hal absent: normal on a dev PC
  assert seen == ["hal.drivers.camera", "hal.tuning.npu"]


def test_load_stereo_intrinsics_passes_the_path_only_when_given(monkeypatch):
  calls = []
  camera = SimpleNamespace(load_stereo_intrinsics=lambda *a: calls.append(a) or SimpleNamespace(Q="q"))
  monkeypatch.setattr(HardwareBase, "hal_import", classmethod(lambda cls, name, **kw: camera))
  assert HardwareBase.load_stereo_intrinsics().Q == "q"
  assert HardwareBase.load_stereo_intrinsics("/x.npz").Q == "q"
  assert calls == [(), ("/x.npz",)]


def test_no_hal_no_intrinsics(monkeypatch):
  monkeypatch.setattr(HardwareBase, "hal_import", classmethod(lambda cls, name, **kw: None))
  assert HardwareBase.load_stereo_intrinsics() is None
  monkeypatch.setattr(HardwareBase, "hal_import", classmethod(lambda cls, name, **kw: SimpleNamespace()))
  assert HardwareBase.load_stereo_intrinsics() is None                            # hal without the loader


def test_hal_calibration_is_all_or_nothing(monkeypatch):
  monkeypatch.setattr(HardwareBase, "hal_import", classmethod(lambda cls, name, **kw: SimpleNamespace(name=name)))
  pieces = HardwareBase.hal_calibration()
  assert [p.name for p in pieces] == [f"calibration.{n}" for n in ("camera_model", "extrinsics", "mounting", "store", "tf_tree")]
  monkeypatch.setattr(HardwareBase, "hal_import",
                      classmethod(lambda cls, name, **kw: None if name.endswith("store") else SimpleNamespace()))
  assert HardwareBase.hal_calibration() is None


NO_DIRECT_HAL_IMPORT = [
  "selfdrive/gridd/gridd.py", "selfdrive/pointcloudd/pointcloudd.py",
  "selfdrive/steamd/stereo_correction.py", "selfdrive/modeld/runners/rknn_platform.py",
  "selfdrive/locationd/side_rear_calibration.py", "selfdrive/sided/bev_reprojector.py",
]


def test_selfdrive_reaches_hal_through_hardware_not_by_import():
  for rel in NO_DIRECT_HAL_IMPORT:
    src = (ROOT / rel).read_text()
    assert not re.search(r"^\s*(from|import)\s+hal\b", src, re.M), rel
