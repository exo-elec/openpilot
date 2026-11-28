"""Offline mapd (third_party/mapd): vendored binary, cereal slots, services, process entry, data path."""
import hashlib
import os
from pathlib import Path

import pytest

from openpilot.common.basedir import BASEDIR

MAPD_SHA256 = "0bc3564ec634486d11e95b76e884fd37b783c66933405cec1bd59f179fa09e94"  # v2.4.0, see third_party/mapd/README.md
MAPD_DIR = os.path.join(BASEDIR, "third_party", "mapd")


def _binary(arch: str) -> bytes:
  return Path(MAPD_DIR, arch, "mapd").read_bytes()


@pytest.mark.parametrize("arch", ["larch64", "aarch64"])
def test_vendored_binary_is_the_pinned_release(arch):
  data = _binary(arch)
  if data.startswith(b"version https://git-lfs"):
    pytest.skip("LFS object not fetched")
  assert data[:4] == b"\x7fELF" and data[18] == 0xb7  # ELF, EM_AARCH64
  assert hashlib.sha256(data).hexdigest() == MAPD_SHA256
  assert os.access(os.path.join(MAPD_DIR, arch, "mapd"), os.X_OK)


def test_readme_and_license_ship_with_it():
  readme = Path(MAPD_DIR, "README.md").read_text()
  assert MAPD_SHA256 in readme and "MIT" in readme
  assert "Permission is hereby granted" in Path(MAPD_DIR, "LICENSE").read_text()


def test_mapd_messages_take_the_reserved_slots_with_the_same_struct_ids():
  log = Path(BASEDIR, "cereal", "log.capnp").read_text()
  custom = Path(BASEDIR, "cereal", "custom.capnp").read_text()
  for field, ordinal, struct in (("mapdExtendedOut", 143, "MapdExtendedOut"), ("mapdIn", 144, "MapdIn"), ("mapdOut", 145, "MapdOut")):
    assert f"{field} @{ordinal} :Custom.{struct};" in log
  for struct, sid in (("MapdExtendedOut", "0xa30662f84033036c"), ("MapdIn", "0xc86a3d38d13eb3ef"), ("MapdOut", "0xa4f1eb3323f5f582")):
    assert f"struct {struct} @{sid} {{" in custom  # the ids CustomReserved17-19 had, as mapd's own schema uses


def test_services_are_registered():
  from cereal.services import SERVICE_LIST
  assert SERVICE_LIST['mapdOut'].frequency == 20.
  assert {'mapdIn', 'mapdExtendedOut'} <= set(SERVICE_LIST)


def test_paths_and_process_entry(monkeypatch):
  from openpilot.system.hardware.hw import Paths
  from openpilot.system.manager.manager import managed_processes  # the registry the manager runs, on every branch
  p = managed_processes['mapd']
  (binary,) = p.cmdline
  # the registry is built once at import, before the test's random OPENPILOT_PREFIX: compare without it
  monkeypatch.delenv("OPENPILOT_PREFIX", raising=False)
  assert p.cwd == Paths.mapd_root()
  assert os.path.dirname(os.path.dirname(binary)) == MAPD_DIR and os.path.basename(binary) == "mapd"
  assert p.enabled == os.path.isfile(binary)

  class P:
    def __init__(self, on):
      self.on = on

    def get_bool(self, key):
      return self.on and key == "MapdEnabled"

  assert p.should_run(False, P(True), None) and not p.should_run(True, P(False), None)  # also offroad, for downloads


MENU_SHA256 = "7033b85d9a7ea4a500cb5134f0daa6192afb13f66069e6378e6ad4d22a74e022"  # settings/download_menu.json at v2.4.0


def test_download_menu_is_the_one_built_into_mapd():
  # The settings page lists areas from this file; mapd resolves the same paths from its embedded copy.
  data = Path(MAPD_DIR, "download_menu.json").read_bytes()
  assert hashlib.sha256(data).hexdigest() == MENU_SHA256
  assert MENU_SHA256 in Path(MAPD_DIR, "README.md").read_text()
  import json
  menu = json.loads(data)
  assert set(menu) == {"nation", "us_state"}
  assert menu["nation"]["KR"]["full_name"] == "South Korea"
  assert menu["nation"]["US"]["submenu"] == "us_state"  # the page lists the states instead
  assert all("full_name" in v for group in menu.values() for v in group.values())


def test_area_params_are_registered():
  from openpilot.common.params import Params
  keys = Params().all_keys()
  assert {b"MapdAreas", b"MapdAutoUpdate", b"MapdLastDownload"} <= set(keys)
