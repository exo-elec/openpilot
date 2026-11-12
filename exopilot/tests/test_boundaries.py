"""Code-boundary rules for the ExoPilot layer (see docs/BOUNDARIES.md)."""
import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NP = ROOT / "exopilot"

FEATURE_DIRS = ("selfdrive/controls/lib",)

SKIP_TOP = {".git", "tinygrad_repo", "third_party", "opendbc_repo", "panda", "msgq_repo", "rednose_repo", "teleoprtc_repo", "teleoprtc"}
# Owner decision 2026-11-12: no product prefix on any name. Only the saved-param migration may spell the old names.
# ngp_x / x_ngp / x_ngp_y, ngpX / xNgpY / NGPFoo / NGP_X; release labels such as NGP10 or EOP10 are not prefixes.
PREFIXED = re.compile("|".join((r"(?<![A-Za-z0-9])(ngp|eop)_", r"_(ngp|eop)(?![A-Za-z0-9])",
                                 r"(?<![A-Z])(ngp|eop|Ngp|Eop|NGP|EOP)(?=[A-Z][a-z])", r"(?<![A-Za-z0-9])(NGP|EOP)_")))
CODE_SUFFIXES = (".py", ".cc", ".h", ".capnp", ".sh", ".json", ".yaml", ".yml", ".ts", ".qml")
MIGRATION_FILES = {"exopilot/common/param_migration.py", "exopilot/tests/test_param_migration.py", "exopilot/tests/test_boundaries.py"}


def _tracked():
  out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
  return [f for f in out if f and f.split("/")[0] not in SKIP_TOP]


def test_no_product_prefix_in_file_names():
  # Documents may name the product lines (NGP10_MINIMAL_EOP_PARITY.md); code, assets and scripts may not.
  bad = [f for f in _tracked() if not f.endswith(".md") and PREFIXED.search(Path(f).name.replace("-", "_"))]
  assert not bad, bad


def test_no_product_prefix_in_code():
  bad = []
  for f in _tracked():
    if not f.endswith(CODE_SUFFIXES) or f in MIGRATION_FILES or not (ROOT / f).is_file():
      continue
    for n, line in enumerate((ROOT / f).read_text(errors="replace").splitlines(), 1):
      if PREFIXED.search(line):
        bad.append(f"{f}:{n}: {line.strip()[:100]}")
  assert not bad, "\n".join(bad[:50])


def test_no_release_number_in_identifiers():
  # Release labels (10, 01M...) belong to branch names and docs, not code identifiers.
  bad = []
  for d in FEATURE_DIRS:
    for f in (NP / d).glob("*.py"):
      for node in ast.walk(ast.parse(f.read_text())):
        name = getattr(node, "name", None)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and name and ("NGP10" in name or "EOP10" in name):
          bad.append(f"{f.name}:{name}")
  assert not bad, bad


def test_dlon_modes_match_cereal_enum():
  # longitudinal_planner writes dlonModeKind = mode.lower(); every DLON mode (and the
  # initial 'Disabled') must therefore be an enumerant of LongitudinalPlan.DlonModeKind.
  import re
  schema = (ROOT / "cereal" / "log.capnp").read_text()
  body = re.search(r"enum DlonModeKind \{(.*?)\}", schema, re.S).group(1)
  enumerants = set(re.findall(r"(\w+) @\d+;", body))
  src = (NP / "selfdrive" / "controls" / "lib" / "dlon.py").read_text()
  modes = set(re.findall(r'^\s+[A-Z]+ = "(\w+)"$', src.split("class DLONMode")[1].split("class DriveMode")[0], re.M))
  assert {m.lower() for m in modes | {"Disabled"}} <= enumerants
