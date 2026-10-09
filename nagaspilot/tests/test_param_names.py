import re
import subprocess
from pathlib import Path

from nagaspilot.param_migration import RENAMES, migrate_renamed_params

ROOT = Path(__file__).resolve().parents[2]


def test_migration_moves_values_and_keeps_existing_new_value(tmp_path):
  (tmp_path / "SteamDAuthToken").write_text("secret")
  (tmp_path / "QuietMode").write_text("1")
  (tmp_path / "EOPQuietMode").write_text("0")  # already set under the new name: keep it
  moved = migrate_renamed_params(str(tmp_path))
  assert moved == ["SteamDAuthToken"]
  assert (tmp_path / "EOPSteamDAuthToken").read_text() == "secret"
  assert (tmp_path / "EOPQuietMode").read_text() == "0"
  assert not (tmp_path / "SteamDAuthToken").exists() and not (tmp_path / "QuietMode").exists()


def test_migration_is_idempotent_on_empty_dir(tmp_path):
  assert migrate_renamed_params(str(tmp_path)) == []
  assert migrate_renamed_params(str(tmp_path)) == []


def test_new_names_are_declared_and_old_names_are_gone():
  header = (ROOT / "common" / "params_keys.h").read_text()
  declared = set(re.findall(r'\{"([A-Za-z0-9_]+)",', header))
  assert set(RENAMES.values()) <= declared
  assert not set(RENAMES) & declared  # old names no longer registered
  assert all(new == "EOP" + old for old, new in RENAMES.items())


def test_no_old_param_name_is_quoted_in_code():
  files = subprocess.run(["git", "ls-files", "*.py", "*.cc", "*.h", "*.sh", "*.json"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
  skip = ("tinygrad_repo/", "third_party/", "opendbc_repo/", "docs/", "panda/", "nagaspilot/param_migration.py", "nagaspilot/tests/test_param_names.py")
  pat = re.compile(r"""(["'])(""" + "|".join(map(re.escape, RENAMES)) + r""")\1""")
  hits = []
  for f in files:
    if f and not f.startswith(skip):
      try:
        hits += [f"{f}: {m.group(2)}" for m in pat.finditer((ROOT / f).read_text(encoding="utf-8"))]
      except (UnicodeDecodeError, FileNotFoundError):
        pass
  assert not hits, hits[:10]
