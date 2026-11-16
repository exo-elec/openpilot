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
    "exopilot.system.radar3d.radar3d",
    "selfdrive.controls.radard",
    "selfdrive.selfdrived.selfdrived",
    # EOP perception daemons
    "selfdrive.modeld.modeld",
    "exopilot.selfdrive.stereod.stereod",
    "exopilot.selfdrive.reard.reard",
    "exopilot.selfdrive.monod.rknn",
    "exopilot.selfdrive.pointcloudd.pointcloudd",
    "exopilot.selfdrive.sided.sided",
    # EOP planning/control daemons
    "exopilot.selfdrive.coordinationd.coordinationd",
    "exopilot.selfdrive.pathd.avoidance",
    "exopilot.selfdrive.gridd.occupancy",
    # EOP support daemons
    "selfdrive.navigation.navd",
    "selfdrive.navigation.mapsd",
    "exopilot.selfdrive.navigation.nav_voice",
    "exopilot.selfdrive.surfaced.surfaced",
    "selfdrive.soundd.soundd",
    "exopilot.system.obd2d.obd2d",
    "exopilot.selfdrive.mapd.mapd",
    "exopilot.system.recordd.recordd",
    "exopilot.selfdrive.tripd.keys",
    # Vehicle interface & adaptive driving
    "system.socketd.vehicle",
    "exopilot.selfdrive.adaptd.adaptd",
    # Infrastructure
    "system.inferenced.inferenced",
    "system.inferenced.client",
    "system.subscribed.subscribed",
    "system.uvcd.uvcd",
    "exopilot.system.steamd.steamd",
    "system.manager.manager",
    # Platform status / logging daemons (always_run in process_config)
    "system.loggerd.deleter",
    "exopilot.system.hardwared.hardwared",
    "system.wdgd.wdgd",
    "system.rtcd.rtcd",
    "system.imud.imud",
    "system.micd.micd",
    "system.voiced.voiced",
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
