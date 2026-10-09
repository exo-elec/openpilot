"""Code-boundary rules for the NagasPilot/ExoPilot layer (see docs/BOUNDARIES.md)."""
import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NP = ROOT / "nagaspilot"

# Policy/perception/diagnostic modules are pure: no daemon, process-config or param I/O.
PURE_DIRS = ("controls",)
FORBIDDEN_PREFIXES = ("openpilot.selfdrive", "openpilot.system", "openpilot.common.params", "cereal.messaging")

# NGP/EOP-prefixed files may live outside nagaspilot/ only at these reviewed UI hooks.
ALLOWED_OUTSIDE = {"selfdrive/ui/qt/offroad/ngp_panel.cc", "selfdrive/ui/qt/offroad/ngp_panel.h", "selfdrive/ui/qt/offroad/ngp_controls.h",
                   "selfdrive/ui/qt/offroad/eop_panel.cc", "selfdrive/ui/qt/offroad/eop_panel.h", "selfdrive/assets/images/eop_qr.png"}
SKIP_TOP = {".git", "tinygrad_repo", "third_party", "opendbc_repo", "panda", "msgq_repo", "rednose_repo", "teleoprtc_repo", "nagaspilot"}


def _imports(path: Path):
  for node in ast.walk(ast.parse(path.read_text())):
    if isinstance(node, ast.Import):
      yield from (a.name for a in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
      yield node.module


def test_pure_modules_do_not_import_runtime():
  bad = []
  for d in PURE_DIRS:
    for f in (NP / d).glob("*.py"):
      bad += [f"{f.relative_to(ROOT)} imports {m}" for m in _imports(f) if m.startswith(FORBIDDEN_PREFIXES)]
  assert not bad, "\n".join(bad)


def test_no_ngp_or_eop_modules_outside_nagaspilot():
  files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
  stray = [f for f in files if f and f.split("/")[0] not in SKIP_TOP and f not in ALLOWED_OUTSIDE
           and (Path(f).name.startswith(("ngp_", "eop_")) or "/ngp_" in f or "/eop_" in f)
           and "/tests/" not in f]
  assert not stray, f"move under nagaspilot/: {stray}"


def test_no_release_number_in_identifiers():
  # Release labels (10, 01M...) belong to branch names and docs, not code identifiers.
  bad = []
  for d in PURE_DIRS:
    for f in (NP / d).glob("*.py"):
      for node in ast.walk(ast.parse(f.read_text())):
        name = getattr(node, "name", None)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and name and ("NGP10" in name or "EOP10" in name):
          bad.append(f"{f.name}:{name}")
  assert not bad, bad


def test_dlon_modes_match_cereal_enum():
  # longitudinal_planner writes ngpDlonModeKind = mode.lower(); every DLON mode (and the
  # initial 'Disabled') must therefore be an enumerant of LongitudinalPlan.NgpDlonModeKind.
  import re
  schema = (ROOT / "cereal" / "log.capnp").read_text()
  body = re.search(r"enum NgpDlonModeKind \{(.*?)\}", schema, re.S).group(1)
  enumerants = set(re.findall(r"(\w+) @\d+;", body))
  src = (NP / "controls" / "ngp_dlon.py").read_text()
  modes = set(re.findall(r'^\s+[A-Z]+ = "(\w+)"$', src.split("class NGPDLONMode")[1].split("class NGPDriveMode")[0], re.M))
  assert {m.lower() for m in modes | {"Disabled"}} <= enumerants
