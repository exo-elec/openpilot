"""Measure and ratchet this branch's modifications to upstream selfdrive/.

Usage: python3 nagaspilot/footprint.py --update   (rewrite the budget after reducing the footprint)

`selfdrive/ui` and `selfdrive/assets` are excluded: UI and assets are expected to change.
New files are free; only edits to, or deletions of, files that exist in the upstream
baseline count against the budget.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUDGET = Path(__file__).resolve().parent / "footprint_budget.json"
BASE = "c085b8af19438956c15592828bd082803f43dfaf"  # official openpilot v0.10.0
SCOPE = "selfdrive"
EXCLUDE = ("selfdrive/ui/", "selfdrive/assets/")


def _git(*args):
  return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def base_available():
  try:
    subprocess.run(["git", "cat-file", "-e", f"{BASE}^{{commit}}"], cwd=ROOT, check=True, capture_output=True)
    return True
  except subprocess.CalledProcessError:
    return False


def measure():
  modified, deleted = {}, []
  status = {}
  for line in _git("diff", "--no-renames", "--name-status", BASE, "HEAD", "--", SCOPE).splitlines():
    st, path = line.split("\t", 1)
    if not path.startswith(EXCLUDE):
      status[path] = st[0]
  for line in _git("diff", "--no-renames", "--numstat", BASE, "HEAD", "--", SCOPE).splitlines():
    added, removed, path = line.split("\t", 2)
    if status.get(path) == "M" and added != "-":
      modified[path] = [int(added), int(removed)]
  deleted = sorted(p for p, s in status.items() if s == "D")
  return {"base": BASE, "modified": dict(sorted(modified.items())), "deleted": deleted}


def violations(current, budget):
  out = []
  for path, (a, d) in current["modified"].items():
    allowed = budget["modified"].get(path)
    if allowed is None:
      out.append(f"new upstream file edited: {path} (+{a}/-{d})")
    elif a > allowed[0] or d > allowed[1]:
      out.append(f"{path} grew: +{a}/-{d} > budget +{allowed[0]}/-{allowed[1]}")
  out += [f"upstream file deleted: {p}" for p in current["deleted"] if p not in budget["deleted"]]
  return out


if __name__ == "__main__":
  if "--update" in sys.argv:
    BUDGET.write_text(json.dumps(measure(), indent=1) + "\n")
    print(f"wrote {BUDGET}")
  else:
    cur = measure()
    added = sum(v[0] for v in cur["modified"].values())
    removed = sum(v[1] for v in cur["modified"].values())
    print(f"{len(cur['modified'])} upstream files modified, {len(cur['deleted'])} deleted (+{added}/-{removed})")
