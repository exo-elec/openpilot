"""Camera-monitor UI must not return through a descendant rebase."""
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]


def test_unused_driver_preview_catalogs_and_settings_are_removed():
  catalogs = list((ROOT / "selfdrive/ui/translations").glob("main_*.ts"))
  assert len(catalogs) >= 13
  forbidden = {"Driver Camera", "Record and Upload Driver Camera", "Always-On Driver Monitoring"}
  for path in catalogs:
    tree = ET.parse(path)
    assert not any(node.text in forbidden for node in tree.iter("source")), path
    assert not any(node.text in {"DriverViewWindow", "DriverViewScene"} for node in tree.iter("name")), path


def test_camera_preview_ui_and_face_asset_are_absent():
  assert not (ROOT / "selfdrive/assets/icons/driver_face.png").exists()
  for path in (ROOT / "selfdrive/ui").rglob("*"):
    if path.is_file() and path.suffix in {".py", ".cc", ".h"}:
      source = path.read_text()
      assert "PreviewDriverCamera" not in source, path
      assert "driverPoseState" not in source, path
      assert "driverStatus" not in source, path
      assert "_draw_gaze" not in source, path
