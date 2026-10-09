"""Fusion optics come from the HAL: one description, both boards.

This was a hardcoded table keyed by board with exactly one board in it, so
the other board silently borrowed 01M's focal lengths and fields of view.
It had also drifted from the hardware it claimed to describe -- it gave the
road camera 60 deg at 1920x1080 where exopilot's HAL says 40 deg at
1920x1280.

build_camera_specs() takes the geometry as an argument, so these tests hand
it a stand-in directly. Nothing here patches module globals: needing to do
that would mean the function was reaching for state it should be given.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from openpilot.selfdrive.gridd.multi_camera_fusion import (
  FALLBACK_OPTICS,
  HAL_NAME_BY_ROLE,
  ROLE_RANGE_M,
  CameraRole,
  FusionConfig,
  build_camera_specs,
)


@dataclass
class FakeCameraGeometry:
  """Stands in for hal.platform.<board>_camera_geometry.

  Same three mappings the real module exposes, built from one table so a
  camera cannot be half-defined.
  """
  cameras: dict[str, tuple[float, int, tuple[int, int]]]
  LENS_MM: dict = field(init=False)
  FOV_DEG: dict = field(init=False)
  IMAGE_SIZE_PX: dict = field(init=False)

  def __post_init__(self):
    self.LENS_MM = {n: v[0] for n, v in self.cameras.items()}
    self.FOV_DEG = {n: v[1] for n, v in self.cameras.items()}
    self.IMAGE_SIZE_PX = {n: v[2] for n, v in self.cameras.items()}


# Values copied from exo-elec/exopilot hal/hal/platform/*_camera_geometry.py
RK3588_GEO = FakeCameraGeometry({
  "wide_road": (1.7, 119, (1920, 1280)),
  "road": (8.0, 40, (1920, 1280)),
  "stereo_left": (3.6, 71, (2560, 1440)),
  "stereo_right": (3.6, 71, (2560, 1440)),
})
RK3576_GEO = FakeCameraGeometry({
  "mono_wide": (1.7, 119, (1920, 1280)),
  "mono_narrow": (8.0, 40, (1920, 1280)),
  "mono_tele": (16.0, 20, (1920, 1280)),
  "stereo_left": (3.6, 71, (2560, 1440)),
  "stereo_right": (3.6, 71, (2560, 1440)),
})

BOARDS = [("rk3588", RK3588_GEO), ("rk3576", RK3576_GEO)]


@pytest.mark.parametrize("platform,geometry", BOARDS)
def test_every_role_is_filled_on_both_boards(platform, geometry):
  specs = build_camera_specs(platform, geometry)
  missing = set(CameraRole) - set(specs)
  assert not missing, f"{platform} has no camera for {missing}"


def test_the_boards_name_the_same_role_differently():
  """01M's road camera is 'road'; 02M's is 'mono_narrow'. Joining name to
  role is the entire reason this mapping exists."""
  assert HAL_NAME_BY_ROLE["rk3588"][CameraRole.ROAD] == "road"
  assert HAL_NAME_BY_ROLE["rk3576"][CameraRole.ROAD] == "mono_narrow"
  assert HAL_NAME_BY_ROLE["rk3588"][CameraRole.WIDE_ROAD] == "wide_road"
  assert HAL_NAME_BY_ROLE["rk3576"][CameraRole.WIDE_ROAD] == "mono_wide"


def test_optics_come_from_the_hal_not_the_old_table():
  """The drifted values must not come back: road is 40 deg at 1920x1280."""
  road = build_camera_specs("rk3588", RK3588_GEO)[CameraRole.ROAD]
  assert road["fov"] == 40.0, "60.0 was the old table's wrong value"
  assert (road["width"], road["height"]) == (1920, 1280), "1080 was wrong"


@pytest.mark.parametrize("platform,geometry", BOARDS)
def test_ranges_are_policy_and_survive_the_hal(platform, geometry):
  """min_m/max_m are tuning, not board data -- the HAL has no opinion on
  them, so they must still be applied."""
  for role, spec in build_camera_specs(platform, geometry).items():
    assert (spec["min_m"], spec["max_m"]) == ROLE_RANGE_M[role]


def test_a_role_the_hal_cannot_fill_is_dropped_not_guessed():
  """Fusing with three inputs beats inventing optics for a camera this
  board does not have."""
  partial = FakeCameraGeometry({"road": (8.0, 40, (1920, 1280))})
  assert set(build_camera_specs("rk3588", partial)) == {CameraRole.ROAD}


@pytest.mark.parametrize("platform,geometry", [
  ("rk3588", None),            # HAL not installed
  ("pc", RK3588_GEO),          # not a board we have a role mapping for
  ("rk9999", None),            # neither
])
def test_without_usable_geometry_it_still_runs_on_fallback_optics(platform, geometry):
  specs = build_camera_specs(platform, geometry)
  assert set(specs) == set(CameraRole)
  assert specs[CameraRole.ROAD]["fov"] == 40.0, "fallback must match the board too"


def test_the_fallback_matches_the_hal_for_the_boards_we_ship():
  """The fallback exists for dev PCs, but wrong numbers there mislead just
  as effectively -- it drifted once already."""
  for platform, geometry in BOARDS:
    for role, from_hal in build_camera_specs(platform, geometry).items():
      fallback = FALLBACK_OPTICS[role]
      assert from_hal["focal_mm"] == fallback["focal_mm"], (platform, role)
      assert from_hal["fov"] == fallback["fov"], (platform, role)
      assert (from_hal["width"], from_hal["height"]) == \
             (fallback["width"], fallback["height"]), (platform, role)


def test_fusion_config_asks_the_hardware_rather_than_naming_a_board():
  assert FusionConfig().platform is None
