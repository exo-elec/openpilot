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
  "exopilot/selfdrive/camera_calibrationd/camera_calibrationd.py",
  "exopilot/selfdrive/gridd/camera_geometry.py",
  "exopilot/selfdrive/gridd/multi_camera_fusion.py",
  "exopilot/selfdrive/monod/calibration_fusion.py",
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
  m = re.search(r'\{"SimPlatform",\s*\{[^}]*\}\}', keys)
  assert m, "SimPlatform not declared"
  assert "rk3588" not in m.group(0) and "rk3576" not in m.group(0), m.group(0)


def test_devfreq_governors_are_discovered_not_hardcoded():
  """The old fallback was a list of RK3588 device-tree addresses, which name
  nothing on another board -- throttling then controlled nothing, silently."""
  from openpilot.system.thermald.thermald import _discover_devfreq_governors
  found = _discover_devfreq_governors("npu")
  if Path("/sys/class/devfreq").is_dir():  # on a board: whatever its NPU node is called, and only real nodes
    assert all(Path(p).is_file() and p.endswith(".npu/governor") for p in found), found
  else:
    assert found == [], "no devfreq off-device"
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
  exopilot/selfdrive/gridd/tests/test_camera_specs.py, which checks both boards
  against the HAL.
  """
  from exopilot.selfdrive.gridd.multi_camera_fusion import FusionConfig
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


def test_board_lists_agree_and_ci_targets_the_built_board():
  """Every branch carries both Rockchip adapters (shared pipeline, CLAUDE.md "Branch model"); the build picks
  one through common/build_profile.py. The lists of boards must agree, or a board is detected that cannot be
  built for (or the other way round), and CI must run on the board this branch is built for."""
  from openpilot.common.build_profile import DEFAULT_SOC
  from openpilot.system.hardware.rk_device_id import SUPPORTED_SOCS
  from openpilot.tools.convert_models_to_rknn import RKNN_TARGETS
  assert tuple(RKNN_TARGETS) == tuple(SUPPORTED_SOCS), (RKNN_TARGETS, SUPPORTED_SOCS)
  socs = re.findall(r"ROCKCHIP_SOCS = \[([^\]]*)\]", (REPO / "SConstruct").read_text())
  assert socs and tuple(re.findall(r"'([^']+)'", socs[0])) == tuple(SUPPORTED_SOCS), f"SConstruct: {socs}"
  assert DEFAULT_SOC in SUPPORTED_SOCS, DEFAULT_SOC

  # Jenkins picks the physical device to run the on-device stage against. Drift here runs this branch's code
  # on the other board, silently and successfully enough to look fine.
  board = re.findall(r'TARGET_BOARD = "([^"]*)"', (REPO / "Jenkinsfile").read_text())
  assert board == [DEFAULT_SOC], f"Jenkinsfile TARGET_BOARD {board} vs build_profile {DEFAULT_SOC}"