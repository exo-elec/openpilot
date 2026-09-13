"""Every file path the UI computes from __file__ must actually resolve.

These are all `Path(__file__).parents[N] / ...` expressions, and every one of
them fails *silently*: the loaders return empty defaults so the UI still
comes up, just missing fonts, or an empty alert list, or no language options.
Nothing raises and nothing logs.

Two of these were wrong when this test was written. The offroad alert
catalogue index reached selfdrive/ instead of the repo root, so the path came
out as selfdrive/selfdrive/... and read as "no alerts are raised" -- exactly
what a healthy device looks like. And flattening selfdrive/ui/eop/ into
selfdrive/ui/ moved every module one directory shallower, which shifted every
index by one at once.
"""

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

UI = Path(__file__).resolve().parents[1]
REPO = UI.parents[1]


def test_repo_root_is_where_we_think():
  # If this is wrong every other assertion here is meaningless.
  assert (REPO / "common" / "params_keys.h").exists()
  assert (REPO / "selfdrive").is_dir()


def test_params_header_resolves():
  from openpilot.selfdrive.ui.settings.params_registry import _HEADER
  assert _HEADER.exists(), f"params_keys.h not found at {_HEADER}"


def test_alert_catalogue_actually_resolves():
  # Returns {} on a bad path, which is indistinguishable from "no alerts
  # configured" -- so assert on content, not on the call succeeding.
  from openpilot.selfdrive.ui.views.home import alert_catalogue
  catalogue = alert_catalogue()
  assert catalogue, "offroad alert catalogue read as empty"
  assert all(k.startswith("Offroad_") for k in catalogue)


def test_language_catalogue_resolves():
  from openpilot.selfdrive.ui.views.panels.device import supported_languages
  langs = supported_languages()
  # Falls back to {"English": "main_en"} on a bad path, so a bare truthiness
  # check would pass while the dialog offered exactly one language.
  assert len(langs) > 1, f"only got {langs} -- languages.json likely unresolved"
  assert langs.get("English") == "main_en"


def test_translations_directory_resolves():
  from openpilot.selfdrive.ui.main import TRANSLATIONS_DIR
  assert (REPO / TRANSLATIONS_DIR).is_dir()


def test_font_directory_resolves():
  from openpilot.selfdrive.ui.main import FONTS
  fonts = REPO / "selfdrive" / "assets" / "fonts"
  assert fonts.is_dir(), f"font directory not found at {fonts}"
  # Qt silently substitutes a default face for a missing family, so a wrong
  # path here costs the whole typography with no error.
  assert any((fonts / f"{name}.ttf").exists() for name in FONTS)


def test_declared_languages_all_have_sources():
  declared = json.loads((UI / "translations" / "languages.json").read_text())
  missing = [s for s in declared.values() if not (UI / "translations" / f"{s}.ts").exists()]
  assert not missing, f"languages.json lists catalogues with no .ts: {missing}"
