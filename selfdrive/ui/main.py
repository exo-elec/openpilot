#!/usr/bin/env python3
"""EOP UI entry point (ExoPilot 02M).

Standalone until P5, when it replaces the C++ `ui` process in
system/manager/process_config.py. Running it directly is the intended way to
work on it:

    PYTHONPATH=. python3 -m openpilot.selfdrive.ui.main --demo

`--demo` drives the view from a scripted state source instead of msgq, so the
UI can be worked on without a running backend or a built capnp.
"""

from __future__ import annotations

import argparse
import sys

from openpilot.selfdrive.ui.components.controls import ParamStore
from openpilot.selfdrive.ui.qt import QApplication, QtGui, QTranslator, run_app
from openpilot.selfdrive.ui.state import UIState
from openpilot.selfdrive.ui.styles.style_manager import (
  Component,
  StyleManager,
  Theme,
)
from openpilot.selfdrive.ui.views.offroad import OffroadView
from openpilot.selfdrive.ui.views.onroad import PANEL_H, PANEL_W, OnroadView


# Compiled translations live next to their .ts sources. The C++ UI embedded
# them in a Qt resource (`:/main_th`); there is no resource system here, so
# they are read from disk.
TRANSLATIONS_DIR = "selfdrive/ui/translations"

FONTS = (
  "Inter-Black", "Inter-Bold", "Inter-ExtraBold", "Inter-ExtraLight",
  "Inter-Medium", "Inter-Regular", "Inter-SemiBold", "Inter-Thin",
  "JetBrainsMono-Medium",
)


def load_fonts() -> int:
  """Register the bundled Inter faces. Returns how many loaded.

  Must be called after QApplication exists -- QFontDatabase segfaults
  without one, rather than raising.

  Qt silently falls back to the default sans for a missing family, which is
  what happens on a dev PC without the assets -- the UI is laid out with
  pixel sizes, so it stays usable, just not typographically right.
  """
  from pathlib import Path
  root = Path(__file__).resolve().parents[2] / "selfdrive" / "assets" / "fonts"
  loaded = 0
  for name in FONTS:
    path = root / f"{name}.ttf"
    if path.exists() and QtGui.QFontDatabase.addApplicationFont(str(path)) >= 0:
      loaded += 1
  return loaded


def load_translation(app, store) -> str:
  """Install the QTranslator for the configured language.

  Returns the translation file stem that was loaded, or "" for English --
  which needs no catalogue, because it is the source language.

  The translator must be installed **before** any widget is constructed:
  Qt resolves tr() at the moment a string is used, and a widget built first
  keeps the untranslated text it was given.
  """
  if store is None:
    return ""
  stem = store.get_text("LanguageSetting")
  if not stem or stem == "main_en":
    return ""

  from pathlib import Path
  qm = Path(__file__).resolve().parents[2] / TRANSLATIONS_DIR / f"{stem}.qm"
  translator = QTranslator(app)
  if not qm.exists() or not translator.load(str(qm)):
    # A missing or unreadable catalogue must not stop the UI coming up --
    # English is always a usable fallback. Kept on the app so it is not
    # garbage-collected out from under Qt.
    print(f"ui: could not load translation {qm}", file=sys.stderr)
    return ""
  app.installTranslator(translator)
  app._translator = translator
  return stem



def _demo_source():
  """Cycle blind-spot severities so the bands can be seen without a car."""
  from openpilot.selfdrive.ui.components.blind_spot import (
    CAUTION,
    CLEAR,
    WARNING,
  )
  from openpilot.selfdrive.ui.state import Snapshot
  from openpilot.selfdrive.ui.components.blind_spot import BlindSpotSeverity

  script = [
    BlindSpotSeverity(CLEAR, CLEAR),
    BlindSpotSeverity(CAUTION, CLEAR),
    BlindSpotSeverity(CLEAR, WARNING),
    BlindSpotSeverity(WARNING, CAUTION),
  ]
  i = 0
  while True:
    yield Snapshot(blind_spot=script[i % len(script)])
    i += 1


def main(argv: list[str] | None = None) -> int:
  ap = argparse.ArgumentParser(description="ExoPilot 02M UI")
  ap.add_argument("--demo", action="store_true",
                  help="drive the view from a scripted source, no msgq needed")
  args = ap.parse_args(argv)

  app = QApplication(sys.argv[:1])
  load_fonts()
  # Matches the C++ UI: no focus rectangle, Inter wherever QSS reaches.
  app.setStyleSheet("* { font-family: Inter; outline: none; }")

  store = ParamStore() if not args.demo else None
  # Before any widget: Qt resolves tr() when a string is used, so a widget
  # built before the translator is installed keeps its English text.
  load_translation(app, store)

  styles = StyleManager(Theme.DARK)
  styles.apply(app, Component.ONROAD)

  from openpilot.selfdrive.ui.qt import QtWidgets
  window = QtWidgets.QStackedWidget()
  window.setWindowTitle("ExoPilot 02M")
  view = OnroadView(live_camera=not args.demo)
  offroad = OffroadView() if not args.demo else None
  window.addWidget(view)
  if offroad is not None:
    window.addWidget(offroad)
  # Fixed, not resize(): every coordinate in the onroad view is absolute
  # against PANEL_W x PANEL_H, so a window the WM can reshape draws wrong
  # rather than adapting. 1600x600 is the only size this branch renders at,
  # as 1024x600 is the only one 01M and EOP10 render at.
  window.setFixedSize(PANEL_W, PANEL_H)
  window.show()

  if args.demo:
    from openpilot.selfdrive.ui.qt import QTimer
    source = _demo_source()
    timer = QTimer(view)
    timer.setInterval(1500)
    timer.timeout.connect(lambda: view.set_snapshot(next(source)))
    timer.start()
    view.set_snapshot(next(source))
  else:
    state = UIState(parent=window)
    state.updated.connect(view.set_snapshot)
    state.updated.connect(lambda snap: _on_frame(view, state, snap))
    # Settings are only reachable while parked -- pulling the driving view off
    # screen at speed is a safety defect, not a UX preference (section 5.6).
    if offroad is not None:
      state.offroad_transition.connect(
        lambda is_offroad: window.setCurrentIndex(1 if is_offroad else 0))
    state.start()

  return run_app(app)


def _on_frame(view, state, snap) -> None:
  """Per-frame work that needs more than the Snapshot.

  The model geometry is a few thousand floats and only the driving view reads
  it, so it is fetched here rather than carried in every Snapshot -- and only
  while onroad.
  """
  view.poll_camera()
  if not snap.started:
    return
  width, height = view.camera_size()
  view.set_model_frame(state.read_model_frame(width, height), snap)


if __name__ == "__main__":
  raise SystemExit(main())
