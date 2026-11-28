"""Tier B build-profile and native C++ Qt UI snapshot verification for 01M (RK3588 1024x600)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from openpilot.common import build_profile

ROOT = Path(__file__).resolve().parents[2]
UI_SNAPSHOT = ROOT / "selfdrive/ui/tests/ui_snapshot"


def test_01m_build_profile_targets_cpp_rk3588_1024x600():
  assert build_profile.UI_BACKEND == "cpp"
  assert build_profile.DEFAULT_SOC == "rk3588"
  assert build_profile.DISPLAY_SIZE == (1024, 600)


def test_01m_cpp_ui_snapshot_renders_panels_and_hides_advanced(tmp_path):
  if not UI_SNAPSHOT.exists():
    pytest.skip("ui_snapshot binary not built (run `scons selfdrive/ui/tests/ui_snapshot`)")

  env = os.environ.copy()
  env["QT_QPA_PLATFORM"] = "offscreen"
  env["OPENPILOT_PREFIX"] = f"sim_01m_ui_{os.getpid()}"

  # Visible settings panels render cleanly at 1024x600 with real non-blank UI content
  for panel in ("Device", "WiFi", "Bluetooth", "Driving", "Safety", "Display"):
    out = tmp_path / f"{panel}.png"
    proc = subprocess.run([str(UI_SNAPSHOT), "--panel", panel, "--out", str(out)],
                          cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"ui_snapshot failed on {panel}: {proc.stderr}"
    im = Image.open(out)
    assert im.size == (1024, 600), f"{panel} rendered at {im.size}, expected (1024, 600)"
    assert float(np.asarray(im).std()) > 10.0, f"{panel} rendered blank"

  # Driving panel contains Drive Mode row (01M Driving settings reorganization)
  out_dm = tmp_path / "Driving_drive_mode.png"
  proc_dm = subprocess.run([str(UI_SNAPSHOT), "--panel", "Driving", "--find", "Drive Mode", "--out", str(out_dm)],
                           cwd=ROOT, env=env, capture_output=True, text=True, check=False)
  assert proc_dm.returncode == 0, f"Drive Mode row missing from 01M Driving panel: {proc_dm.stderr}"

  # Advanced panel is hidden by default until 8 quick taps on Device -> Dongle ID
  out_adv = tmp_path / "Advanced.png"
  proc_adv = subprocess.run([str(UI_SNAPSHOT), "--panel", "Advanced", "--out", str(out_adv)],
                            cwd=ROOT, env=env, capture_output=True, text=True, check=False)
  assert proc_adv.returncode != 0 and 'no panel named "Advanced"' in proc_adv.stderr
