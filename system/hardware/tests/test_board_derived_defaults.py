"""Nothing outside the per-branch board lists may name a board.

These are the places that used to carry an `rk3588` literal as a *default*.
None of them crashed on the other board -- they quietly produced the wrong
answer, which is why they need a test rather than being left to review.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

# The only files allowed to name a board: the per-branch lists themselves,
# the board package, the capnp enumerant (append-only), and tests asserting
# the other board is absent.
ALLOWED = {
  "SConstruct",
  "system/hardware/rk_device_id.py",
  "tools/convert_models_to_rknn.py",
  "cereal/log.capnp",
}


# Deliberate exemptions: a board name that is correct because it describes
# some *other* system, not the board this code runs on.
EXEMPT: set[tuple[str, str]] = set()


def _strip_prose(src: str) -> list[str]:
  """Source lines with # comments and triple-quoted blocks removed."""
  single, double = "'" * 3, '"' * 3
  out, in_doc = [], False
  for line in src.split("\n"):
    stripped = line.strip()
    quotes = stripped.count(double) + stripped.count(single)
    if in_doc:
      if quotes:
        in_doc = False
      continue
    if quotes == 1:
      in_doc = True
      continue
    if stripped.startswith(("#", double, single)):
      continue
    out.append(line)
  return out


def _enclosing_def(lines: list[str], idx: int) -> str:
  """Name of the def a line sits in, for exemption lookups."""
  for j in range(idx - 1, -1, -1):
    m = re.match(r"\s*def\s+(\w+)", lines[j])
    if m:
      return m.group(1)
  return ""


def _board_literals(path: Path, rel: str) -> list[tuple[int, str]]:
  """Lines assigning or defaulting to a board name, comments excluded."""
  out = []
  lines = path.read_text(errors="replace").split("\n")
  for i, line in enumerate(lines, 1):
    stripped = line.strip()
    if stripped.startswith(("#", "//", "*")):
      continue
    if (rel, _enclosing_def(lines, i - 1)) in EXEMPT:
      continue
    # `= "rk3588"` / `default='rk3588'` / `or "rk3588"` -- an assignment of a
    # board name, not a mention of one.
    if re.search(r"""(=|default=|or)\s*['"]rk3(588|576)['"]""", stripped):
      out.append((i, stripped[:100]))
  return out


@pytest.mark.parametrize("rel", [
  "selfdrive/locationd/calibration_storage.py",
  "selfdrive/locationd/camera_calibrationd.py",
  "selfdrive/gridd/camera_geometry.py",
  "selfdrive/gridd/multi_camera_fusion.py",
  "selfdrive/monod/calibration_fusion.py",
  "tools/sim/bridge/carla/carla_world.py",
])
def test_no_board_name_as_a_default(rel):
  """Each of these had one, and each silently mis-tagged data on the other
  board: a calibration saved under the wrong platform is read back against
  the wrong geometry."""
  path = REPO / rel
  if not path.exists():
    pytest.skip(f"{rel} not on this branch")
  found = _board_literals(path, rel)
  assert not found, (
    f"{rel} defaults to a board name: " +
    "; ".join(f"line {i}: {s}" for i, s in found))


def test_the_sim_platform_param_has_no_board_baked_in():
  """params_keys.h is branch-independent, so a board name in a default there
  is wrong on whichever branch does not carry that board."""
  keys = (REPO / "common/params_keys.h").read_text()
  m = re.search(r'\{"EOPSimPlatform",\s*\{[^}]*\}\}', keys)
  assert m, "EOPSimPlatform not declared"
  assert "rk3588" not in m.group(0) and "rk3576" not in m.group(0), m.group(0)


def test_devfreq_governors_are_discovered_not_hardcoded():
  """The old fallback was a list of RK3588 device-tree addresses, which name
  nothing on another board -- throttling then controlled nothing, silently."""
  from openpilot.system.thermald.thermald import _discover_devfreq_governors
  assert _discover_devfreq_governors("npu") == [], "no devfreq off-device"
  # Comments and docstrings may still *mention* the old address to explain
  # the fix; what must be gone is code depending on it.
  code = _strip_prose((REPO / "system/thermald/thermald.py").read_text())
  assert not any("ffa30000.npu" in ln for ln in code), \
    "RK3588 devfreq address still hardcoded in code"


def test_rknpu2_search_puts_the_running_board_first():
  """rknpu2 ships one runtime directory per SoC family. The old list named
  two of them, so a board in neither found no librknn_api at all."""
  from openpilot.system.hardware.rockchip._libloader import (
    _RKNPU2_ALL, _rknpu2_families,
  )
  families = _rknpu2_families()
  assert set(families) == set(_RKNPU2_ALL), "every family stays reachable"
  assert len(families) == len(set(families)), "no duplicates"


def test_fusion_config_defaults_to_the_running_board():
  """FusionConfig used to default platform to a board name, so fusion on the
  other board ran with 01M's optics. None means "ask the hardware".

  The optics themselves are covered by
  selfdrive/gridd/tests/test_camera_specs.py, which checks both boards
  against the HAL.
  """
  from openpilot.selfdrive.gridd.multi_camera_fusion import FusionConfig
  assert FusionConfig().platform is None


def test_only_exopilot_socs_are_in_the_rknpu2_search_path():
  """RK356X and friends are Rockchip parts but not ExoPilot hardware, and
  RK3688 (03M) is not supported yet. A stray entry here is a directory the
  loader will happily search and, if a stale library is present, load."""
  from openpilot.system.hardware.rockchip._libloader import _RKNPU2_FAMILY
  from openpilot.system.hardware.rk_device_id import SUPPORTED_SOCS
  assert set(_RKNPU2_FAMILY) == {"rk3588", "rk3576"}, _RKNPU2_FAMILY
  assert "rk3688" not in _RKNPU2_FAMILY, "03M is DoraPilot's, not this tree's"
  assert set(SUPPORTED_SOCS) <= set(_RKNPU2_FAMILY), \
    "this branch's board must have an rknpu2 runtime directory"


def test_this_branch_carries_exactly_one_board():
  """Each branch is one board. Two entries anywhere in the per-branch lists
  means something inherited the other branch's value in a rebase."""
  from openpilot.system.hardware.rk_device_id import SUPPORTED_SOCS
  from openpilot.tools.convert_models_to_rknn import RKNN_TARGETS
  assert len(SUPPORTED_SOCS) == 1, SUPPORTED_SOCS
  assert len(RKNN_TARGETS) == 1, RKNN_TARGETS
  assert SUPPORTED_SOCS[0] == RKNN_TARGETS[0], \
    "the board built for and the board detected must be the same one"
  socs = re.findall(r"ROCKCHIP_SOCS = \[([^\]]*)\]",
                    (REPO / "SConstruct").read_text())
  assert socs and socs[0].count(",") == 0, f"SConstruct: {socs}"
  assert SUPPORTED_SOCS[0] in socs[0], f"SConstruct {socs[0]} vs {SUPPORTED_SOCS}"

  # Jenkins picks the physical device to run the on-device stage against.
  # Drift here is the worst of the four: it runs this branch's code on the
  # other branch's hardware, silently and successfully enough to look fine.
  board = re.findall(r'EOP_BOARD = "([^"]*)"', (REPO / "Jenkinsfile").read_text())
  assert board == [SUPPORTED_SOCS[0]], \
    f"Jenkinsfile EOP_BOARD {board} vs {SUPPORTED_SOCS}"
