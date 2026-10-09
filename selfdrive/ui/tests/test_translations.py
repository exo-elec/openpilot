"""The UI actually applies translations.

The failure this guards is silent and was live for a while: the C++ UI
installed a QTranslator in main.cc, the Python port did not, and the .ts
files kept shipping. "Change Language" wrote LanguageSetting, restarted the
UI, and it came back in English -- a settings row that did nothing, with no
error anywhere.

Two halves have to hold for translation to work at all, so both are tested:
the catalogue must load, and the widgets must actually route their strings
through tr(). A bare string literal never consults the translator.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from openpilot.selfdrive.ui.components.controls import ParamStore
from openpilot.selfdrive.ui.main import load_translation
from openpilot.selfdrive.ui.qt import QApplication

TRANSLATIONS = Path(__file__).resolve().parents[1] / "translations"


class FakeParams:
  def __init__(self, lang=""):
    self.d = {"LanguageSetting": lang}

  def get(self, k):
    return self.d.get(k, "")

  def put(self, k, v):
    self.d[k] = v

  def get_bool(self, k):
    return bool(self.d.get(k, False))

  def put_bool(self, k, v):
    self.d[k] = v

  def remove(self, k):
    self.d.pop(k, None)


def _compile_catalogues():
  """lrelease the .ts sources, as the SConscript does. .qm files are build
  artefacts and gitignored, so a fresh checkout has none."""
  ts = sorted(TRANSLATIONS.glob("*.ts"))
  if not ts:
    pytest.skip("no .ts sources")
  try:
    subprocess.run(["lrelease", "-silent", *[str(p) for p in ts]],
                   check=True, capture_output=True, timeout=120)
  except (OSError, subprocess.CalledProcessError):
    pytest.skip("lrelease unavailable (qttools5-dev-tools)")


@pytest.fixture(scope="module")
def app():
  _compile_catalogues()
  return QApplication.instance() or QApplication(sys.argv[:1])


class TestCatalogueLoading:
  def test_a_real_language_loads(self, app):
    assert load_translation(app, ParamStore(FakeParams("main_th"))) == "main_th"

  def test_english_needs_no_catalogue(self, app):
    # English is the source language: there is nothing to look up, and
    # reporting a failure for it would be noise.
    assert load_translation(app, ParamStore(FakeParams("main_en"))) == ""
    assert load_translation(app, ParamStore(FakeParams(""))) == ""

  def test_a_missing_catalogue_does_not_stop_the_ui(self, app):
    # Falling back to English beats refusing to boot.
    assert load_translation(app, ParamStore(FakeParams("main_nonexistent"))) == ""

  def test_no_params_is_survivable(self, app):
    assert load_translation(app, None) == ""

  def test_every_declared_language_has_a_source(self, app):
    """languages.json is what the Change Language dialog lists. An entry with
    no .ts file is an option that silently does nothing when picked."""
    import json
    declared = json.loads((TRANSLATIONS / "languages.json").read_text())
    for name, stem in declared.items():
      assert (TRANSLATIONS / f"{stem}.ts").exists(), f"{name} ({stem}) has no .ts"


class TestWidgetsRouteThroughTr:
  """Loading a catalogue is useless if the widgets hold bare literals."""

  def _panels(self, app, lang):
    load_translation(app, ParamStore(FakeParams(lang)))
    from openpilot.selfdrive.ui.views.panels.device import DevicePanel
    from openpilot.selfdrive.ui.views.panels.toggles import TogglesPanel
    store = ParamStore(FakeParams(lang))
    self._keep = (DevicePanel(store), TogglesPanel(store))
    return self._keep

  def test_device_panel_titles_are_translated(self, app):
    device, _ = self._panels(app, "main_th")
    assert device.reset_calib.title_label.text() == "รีเซ็ตการคาลิเบรท"
    assert device.reboot_btn.text() == "รีบูต"

  def test_toggle_titles_are_translated(self, app):
    _, toggles = self._panels(app, "main_th")
    assert toggles.toggles["IsMetric"].title_label.text() == "ใช้ระบบเมตริก"

  def test_a_deliberately_untranslated_string_passes_through(self, app):
    # "Dongle ID" is left as-is in the Thai catalogue -- a proper noun, not a
    # miss. Asserting it stays English keeps a future "fix" from mangling it.
    device, _ = self._panels(app, "main_th")
    assert device.dongle.title_label.text() == "Dongle ID"
