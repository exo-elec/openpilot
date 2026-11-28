"""Tier B build-profile, RK3576 CI/adapter, and wide C++ Qt UI snapshot verification for 02M (RK3576 1600x600)."""

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


def test_02m_build_profile_and_ci_target_cpp_rk3576_1600x600():
  assert build_profile.UI_BACKEND == "cpp"
  assert build_profile.DEFAULT_SOC == "rk3576"
  assert build_profile.DISPLAY_SIZE == (1600, 600)

  jenkins = (ROOT / "Jenkinsfile").read_text()
  assert "rk3576" in jenkins.lower()

  from openpilot.system.hardware.rk3576.hardware import RK3576Hardware
  assert RK3576Hardware().get_device_type() == "rk3576"


def test_02m_cpp_ui_snapshot_renders_wide_1600x600_panels_and_hides_advanced(tmp_path):
  if not UI_SNAPSHOT.exists():
    pytest.skip("ui_snapshot binary not built (run `scons selfdrive/ui/tests/ui_snapshot`)")

  env = os.environ.copy()
  env["QT_QPA_PLATFORM"] = "offscreen"
  env["OPENPILOT_PREFIX"] = f"sim_02m_ui_{os.getpid()}"

  for panel in ("Device", "WiFi", "Bluetooth", "Driving", "Safety", "Display"):
    out = tmp_path / f"{panel}.png"
    proc = subprocess.run([str(UI_SNAPSHOT), "--panel", panel, "--out", str(out)],
                          cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"ui_snapshot failed on {panel}: {proc.stderr}"
    with Image.open(out) as im:
      assert im.size == (1600, 600), f"{panel} rendered at {im.size}, expected (1600, 600)"
      assert float(np.asarray(im).std()) > 10.0, f"{panel} rendered blank"

  # Driving panel Drive Mode row & full-height render at 1600px wide
  out_full = tmp_path / "Driving_full.png"
  proc_full = subprocess.run([str(UI_SNAPSHOT), "--panel", "Driving", "--full", "--out", str(out_full)],
                             cwd=ROOT, env=env, capture_output=True, text=True, check=False)
  assert proc_full.returncode == 0
  with Image.open(out_full) as im_full:
    assert im_full.size[0] == 1600 and im_full.size[1] > 1200

  # Advanced panel stays hidden until unlocked
  out_adv = tmp_path / "Advanced.png"
  proc_adv = subprocess.run([str(UI_SNAPSHOT), "--panel", "Advanced", "--out", str(out_adv)],
                            cwd=ROOT, env=env, capture_output=True, text=True, check=False)
  assert proc_adv.returncode != 0 and 'no panel named "Advanced"' in proc_adv.stderr
