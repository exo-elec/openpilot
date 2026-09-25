"""Tests for the board-resolved HAL module accessor.

Daemons used to import `hal.platform.rk3588_*` by name. That is wrong twice
over: on the other board it loads the wrong board's pin map, and on a branch
that does not carry RK3588 at all the import is simply absent. The accessor
makes the module follow whichever board is running.

The importer is a parameter, so these tests pass a stub. Editing sys.modules
instead would leak into every test that ran afterwards.
"""

from __future__ import annotations

import types


from openpilot.system.hardware.base import HardwareBase
from openpilot.system.hardware.pc.hardware import Pc
from openpilot.system.hardware.rockchip_base import RockchipHardware


class _FakeBoard(RockchipHardware):
  HAL_PREFIX = "rk9999"

  def get_device_type(self):
    return "rk9999"


def make_importer(available: dict[str, object]):
  """An import_module stand-in serving exactly `available`."""
  def _import(name):
    if name not in available:
      raise ImportError(f"No module named {name!r}")
    return available[name]
  return _import


PINS = types.SimpleNamespace(UART={"RADAR3D": {"device": "/dev/ttyS9", "baud": 115200}})
ONLY_PINS = make_importer({"hal.platform.rk9999_pins": PINS})


def test_resolves_the_running_boards_module():
  assert _FakeBoard.hal_module("pins", import_module=ONLY_PINS) is PINS
  uart = _FakeBoard.hal_module("pins", import_module=ONLY_PINS).UART
  assert uart["RADAR3D"]["baud"] == 115200


def test_the_board_prefix_is_what_gets_imported():
  """The whole point: the module name follows the board, not a literal."""
  seen = []

  def _record(name):
    seen.append(name)
    raise ImportError(name)

  _FakeBoard.hal_module("thermal", import_module=_record)
  assert seen == ["hal.platform.rk9999_thermal"]


def test_a_module_this_board_does_not_have_is_none_not_an_exception():
  """Boards differ in which hal modules exist. A missing one is an ordinary
  state the caller covers with its in-repo default, not an error."""
  assert _FakeBoard.hal_module("thermal", import_module=ONLY_PINS) is None


def test_absent_hal_package_is_none_not_importerror():
  """No `hal` installed at all -- the normal dev-PC and CI state."""
  nothing = make_importer({})
  assert _FakeBoard.hal_module("pins", import_module=nothing) is None


def test_board_without_a_prefix_resolves_nothing():
  """RockchipHardware itself is abstract w.r.t. board data -- it must not
  accidentally resolve some other board's module."""
  assert RockchipHardware.HAL_PREFIX == ""
  assert RockchipHardware.hal_module("pins", import_module=ONLY_PINS) is None


def test_non_rockchip_hardware_answers_none():
  """A dev PC has no hal package at all; daemons must still import."""
  assert Pc.hal_module("pins") is None


def test_every_hardware_class_answers_the_accessor():
  """The hook is on HardwareBase, so no platform can be missing it -- a
  daemon calling HARDWARE.hal_module() must never hit AttributeError."""
  assert hasattr(HardwareBase, "hal_module")
  for cls in (Pc, RockchipHardware, _FakeBoard):
    assert callable(cls.hal_module)


def test_the_real_board_declares_a_prefix():
  """Whichever board this branch carries must declare HAL_PREFIX, or every
  daemon silently falls back to its defaults on real hardware."""
  from openpilot.system.hardware.registry import PlatformRegistry
  rockchip_boards = 0
  for name in PlatformRegistry._platforms:
    cls = type(PlatformRegistry.create(name))
    if issubclass(cls, RockchipHardware):
      rockchip_boards += 1
      assert cls.HAL_PREFIX, f"{cls.__name__} declares no HAL_PREFIX"
      assert cls.HAL_PREFIX == cls().get_device_type()
  assert rockchip_boards == 1, "one board per branch"
