#!/usr/bin/env python3
"""
Smoke test: every critical EOP daemon module must be importable without crashing.

These tests caught the capnp schema bugs (duplicate ID, duplicate ordinal,
underscore field names) and missing stubs (numpy_fast, commonmodel_pyx,
transformations functions, GNSSMeasurement) that blocked all daemon startup.

Run with: pytest selfdrive/test/test_daemon_imports.py -v
"""
import os
import importlib
import pytest


@pytest.fixture(autouse=True)
def stub_params_pyx():
    """Ensure params_pyx stubs are used so imports work on dev PC without .so build."""
    os.environ.setdefault("OPENPILOT_STUB_PARAMS_PYX", "1")


CRITICAL_DAEMONS = [
    # Core controls (upstream + EOP overlay)
    "selfdrive.controls.controlsd",
    "selfdrive.controls.plannerd",
    "selfdrive.controls.radar3d",
    "selfdrive.selfdrived.selfdrived",
    # EOP perception daemons
    "selfdrive.modeld.modeld",
    "selfdrive.stereod.stereod",
    "selfdrive.reard.reard",
    "selfdrive.monod.monod",
    "selfdrive.pointcloudd.pointcloudd",
    "selfdrive.sided.sided",
    # EOP planning/control daemons
    "selfdrive.coordinationd.coordinationd",
    "selfdrive.pathd.pathd",
    "selfdrive.gridd.gridd",
    # EOP support daemons
    "selfdrive.navd.navd",
    "selfdrive.surfaced.surfaced",
    "selfdrive.soundd.soundd",
    "selfdrive.obd2d.obd2d",
    "selfdrive.mapd.mapd",
    "selfdrive.recordd.recordd",
    "selfdrive.tripd.tripd",
    # Vehicle interface & adaptive driving
    "selfdrive.vehicled.vehicled",
    "selfdrive.adaptd.adaptd",
    # Infrastructure
    "system.inferenced.inferenced",
    "system.inferenced.client",
    "system.subscribed.subscribed",
    "system.uvcd.uvcd",
    "selfdrive.steamd.steamd",
    "system.manager.manager",
    # Platform status / logging daemons (always_run in process_config)
    "system.loggerd.deleter",
    "system.hardware.hardwared",
    "system.wdgd.wdgd",
    "system.rtcd.rtcd",
    "system.imud.imud",
    "system.micd.micd",
    "system.spkd.spkd",
    "system.mcapd.mcapd",
    "system.bluetoothd.bluetoothd",
    "system.socketd.socketd",
    "system.thermald.thermald",
    "system.stated.stated",
]


@pytest.mark.parametrize("module_path", CRITICAL_DAEMONS)
def test_daemon_imports(module_path):
    """Each daemon module must be importable without any exception."""
    importlib.import_module(module_path)
