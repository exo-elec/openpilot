import re
from pathlib import Path

from exopilot.common.param_migration import RENAMED, migrate_renamed_params

ROOT = Path(__file__).resolve().parents[2]


def _registered():
  return set(re.findall(r'\{"(\w+)",\s*\{', (ROOT / "common" / "params_keys.h").read_text()))


def test_every_new_name_is_registered_and_no_old_name_is():
  keys = _registered()
  assert not [new for new in RENAMED.values() if new not in keys]
  assert not [old for old in RENAMED if old in keys]


def test_saved_values_move_once_and_a_value_under_the_new_name_wins(tmp_path):
  (tmp_path / "ngp_lon_brsc").write_bytes(b"0")
  (tmp_path / "ngp_lon_mtsc").write_bytes(b"1")
  (tmp_path / "MTSCEnabled").write_bytes(b"0")
  moved = migrate_renamed_params(str(tmp_path))
  assert moved == ["ngp_lon_brsc"]
  assert (tmp_path / "BRSCEnabled").read_bytes() == b"0"
  assert (tmp_path / "MTSCEnabled").read_bytes() == b"0"
  assert not (tmp_path / "ngp_lon_brsc").exists() and not (tmp_path / "ngp_lon_mtsc").exists()
  assert migrate_renamed_params(str(tmp_path)) == []


def test_slc_offset_string_becomes_seven_values_and_saved_values_win(tmp_path):
  (tmp_path / "ngp_lon_slc_offsets").write_text("5,5,10,x,0,-5,8")
  (tmp_path / "SLCOffset2").write_text("3.0")
  assert "SharedSLCOffsets" in migrate_renamed_params(str(tmp_path))
  read = {p.name: p.read_text() for p in tmp_path.iterdir()}
  assert read == {"SLCOffset1": "5.0", "SLCOffset2": "3.0", "SLCOffset3": "10.0", "SLCOffset5": "0.0", "SLCOffset6": "-5.0",
                  "SLCOffset7": "8.0"}
  assert migrate_renamed_params(str(tmp_path)) == []
