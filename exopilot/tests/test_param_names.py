import re
import subprocess
from pathlib import Path

from exopilot.param_migration import migrate_renamed_params
from exopilot.param_names import FORK_KEYS, OLD_PREFIX

ROOT = Path(__file__).resolve().parents[2]


def test_migration_moves_values_and_keeps_existing_new_value(tmp_path):
  (tmp_path / "EOPSteamDAuthToken").write_text("secret")
  (tmp_path / "EOPQuietMode").write_text("1")
  (tmp_path / "QuietMode").write_text("0")  # already set under the new name: keep it
  moved = migrate_renamed_params(str(tmp_path))
  assert moved == ["EOPSteamDAuthToken"]
  assert (tmp_path / "SteamDAuthToken").read_text() == "secret"
  assert (tmp_path / "QuietMode").read_text() == "0"
  assert not (tmp_path / "EOPSteamDAuthToken").exists() and not (tmp_path / "EOPQuietMode").exists()


def test_migration_is_idempotent_on_empty_dir(tmp_path):
  assert migrate_renamed_params(str(tmp_path)) == []
  assert migrate_renamed_params(str(tmp_path)) == []


def test_fork_keys_are_declared_without_prefix():
  header = (ROOT / "common" / "params_keys.h").read_text()
  declared = set(re.findall(r'\{"([A-Za-z0-9_]+)",', header))
  assert FORK_KEYS <= declared
  assert not any(k.startswith(OLD_PREFIX) and k[len(OLD_PREFIX):] in FORK_KEYS for k in declared)


def test_no_old_param_name_is_quoted_in_code():
  files = subprocess.run(["git", "ls-files", "*.py", "*.cc", "*.h", "*.sh", "*.json"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
  skip = ("tinygrad_repo/", "third_party/", "opendbc_repo/", "docs/", "panda/", "exopilot/param_migration.py", "exopilot/tests/test_param_names.py")
  pat = re.compile(r"""(["'])""" + OLD_PREFIX + r"(" + "|".join(map(re.escape, FORK_KEYS)) + r")\1")
  hits = []
  for f in files:
    if f and not f.startswith(skip):
      try:
        hits += [f"{f}: {m.group(0)}" for m in pat.finditer((ROOT / f).read_text(encoding="utf-8"))]
      except (UnicodeDecodeError, FileNotFoundError):
        pass
  assert not hits, hits[:10]
