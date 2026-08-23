#!/usr/bin/env python3
"""Tests for modeld's ``_resolve_model`` platform-aware model-file lookup.

Regression coverage for a real bug: the search loop used to check the
``rknn`` subdir before ever consulting the platform-aware ``exts`` order,
so once both ``models/rknn/`` and ``models/onnx/`` held real files (as they
do after adopting bukapilot's KA2 pair), dev-PC runs would silently resolve
to the ``.rknn`` file instead of ``.onnx``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch  # noqa: TID251

import pytest

from openpilot.selfdrive.modeld import modeld


@pytest.fixture
def repo_models(tmp_path: Path, monkeypatch):
  (tmp_path / "rknn").mkdir()
  (tmp_path / "onnx").mkdir()
  monkeypatch.setattr(modeld, "_REPO_MODELS", tmp_path)
  return tmp_path


def test_prefers_onnx_on_dev_pc_when_both_formats_present(repo_models):
  (repo_models / "rknn" / "driving_vision.rknn").write_text("mock")
  (repo_models / "onnx" / "driving_vision.onnx").write_text("mock")

  with patch("openpilot.system.hardware.HARDWARE.get_os_version", return_value=None):
    resolved = modeld._resolve_model("driving_vision")

  assert resolved == repo_models / "onnx" / "driving_vision.onnx"


def test_prefers_rknn_on_arm_when_both_formats_present(repo_models):
  (repo_models / "rknn" / "driving_vision.rknn").write_text("mock")
  (repo_models / "onnx" / "driving_vision.onnx").write_text("mock")

  with patch("openpilot.system.hardware.HARDWARE.get_os_version", return_value="aarch64"):
    resolved = modeld._resolve_model("driving_vision")

  assert resolved == repo_models / "rknn" / "driving_vision.rknn"


def test_falls_back_to_available_format_when_preferred_missing(repo_models):
  (repo_models / "rknn" / "driving_vision.rknn").write_text("mock")

  with patch("openpilot.system.hardware.HARDWARE.get_os_version", return_value=None):
    resolved = modeld._resolve_model("driving_vision")

  assert resolved == repo_models / "rknn" / "driving_vision.rknn"


def test_returns_none_when_no_format_present(repo_models):
  with patch("openpilot.system.hardware.HARDWARE.get_os_version", return_value=None):
    assert modeld._resolve_model("driving_vision") is None
