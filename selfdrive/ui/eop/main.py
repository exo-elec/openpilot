#!/usr/bin/env python3
"""EOP UI entry point (ExoPilot 01M).

Replaces the C++ `ui` process. Run it directly to work on it:

    PYTHONPATH=. python3 -m openpilot.selfdrive.ui.eop.main --demo

`--demo` drives the view from a scripted state source instead of msgq, so the
UI can be worked on without a running backend or a built capnp.
"""

from __future__ import annotations

import argparse
import sys

from openpilot.selfdrive.ui.eop.components.controls import ParamStore
from openpilot.selfdrive.ui.eop.components.theme import SCREEN_H, SCREEN_W
from openpilot.selfdrive.ui.eop.qt import QApplication, QtGui, run_app
from openpilot.selfdrive.ui.eop.state import UIState
from openpilot.selfdrive.ui.eop.views.window import MainWindow

# window.cc exits with this to ask the launcher to start it again, which is
# how a language change takes effect -- Qt cannot retranslate a live widget
# tree.
RESTART_EXIT_CODE = 18

FONTS = (
  "Inter-Black", "Inter-Bold", "Inter-ExtraBold", "Inter-ExtraLight",
  "Inter-Medium", "Inter-Regular", "Inter-SemiBold", "Inter-Thin",
  "JetBrainsMono-Medium",
)


def load_fonts() -> int:
  """Register the bundled Inter faces. Returns how many loaded.

  Qt silently falls back to the default sans for a missing family, which is
  what happens on a dev PC without the assets -- the UI is laid out with
  pixel sizes, so it stays usable, just not typographically right.
  """
  from pathlib import Path
  root = Path(__file__).resolve().parents[3] / "selfdrive" / "assets" / "fonts"
  loaded = 0
  for name in FONTS:
    path = root / f"{name}.ttf"
    if path.exists() and QtGui.QFontDatabase.addApplicationFont(str(path)) >= 0:
      loaded += 1
  return loaded


def _demo_snapshots():
  """A short scripted drive, so every layer can be seen without a car."""
  from openpilot.selfdrive.ui.eop.components.blind_spot import (
    CAUTION,
    CLEAR,
    WARNING,
    BlindSpotSeverity,
  )
  from openpilot.selfdrive.ui.eop.state import NavManeuver, Snapshot, UIStatus

  base = dict(started=True, is_metric=True, cruise_available=True,
              cruise_set=True, set_speed=100.0, speed_limit_ms=25.0,
              nav=NavManeuver(valid=True, maneuver_type="turn", modifier="left",
                              primary_text="Sukhumvit Road", distance_m=420.0))
  script = [
    Snapshot(status=UIStatus.DISENGAGED, v_ego=0.0, **base),
    Snapshot(status=UIStatus.ENGAGED, v_ego=18.0, **base),
    Snapshot(status=UIStatus.ENGAGED, v_ego=25.0, left_blinker=True,
             blind_spot=BlindSpotSeverity(left=WARNING), **base),
    Snapshot(status=UIStatus.OVERRIDE, v_ego=22.0,
             blind_spot=BlindSpotSeverity(right=CAUTION), **base),
    Snapshot(status=UIStatus.ENGAGED, v_ego=27.0,
             blind_spot=BlindSpotSeverity(CLEAR, CLEAR),
             alert_text1="Take Control", alert_text2="Turn exceeds limit",
             alert_severity="warning", alert_size="mid", **base),
  ]
  i = 0
  while True:
    yield script[i % len(script)]
    i += 1


def main(argv: list[str] | None = None) -> int:
  ap = argparse.ArgumentParser(description="ExoPilot 01M UI")
  ap.add_argument("--demo", action="store_true",
                  help="drive the view from a scripted source, no msgq needed")
  args = ap.parse_args(argv)

  app = QApplication(sys.argv[:1])
  load_fonts()
  # Matches window.cc: no focus rectangle, and Inter everywhere QSS reaches.
  app.setStyleSheet("* { font-family: Inter; outline: none; }")

  store = ParamStore() if not args.demo else ParamStore(_DemoParams())
  window = MainWindow(store, live_camera=not args.demo)
  window.setWindowTitle("ExoPilot 01M")
  window.resize(SCREEN_W, SCREEN_H)
  window.show()

  if args.demo:
    from openpilot.selfdrive.ui.eop.qt import QTimer
    source = _demo_snapshots()

    def tick():
      snap = next(source)
      window.set_started(snap.started)
      window.set_snapshot(snap)

    timer = QTimer(window)
    timer.setInterval(2000)
    timer.timeout.connect(tick)
    timer.start()
    tick()
  else:
    state = UIState(parent=window)
    state.updated.connect(window.set_snapshot)
    state.updated.connect(lambda snap: _on_frame(window, state, snap))
    state.offroad_transition.connect(lambda offroad: window.set_started(not offroad))
    state.start()

  return run_app(app)


def _on_frame(window: MainWindow, state: UIState, snap) -> None:
  """Per-frame work that needs more than the Snapshot.

  The model geometry is a few thousand floats and only the driving view reads
  it, so it is fetched here rather than carried in every Snapshot -- and only
  while onroad.
  """
  if not snap.started:
    return
  onroad = window.home.onroad
  onroad.poll_camera()
  width, height = onroad.camera_size()
  onroad.set_model_frame(state.read_model_frame(width, height), snap)


class _DemoParams:
  """In-memory stand-in so --demo needs no Params and writes nothing."""

  def __init__(self):
    self.d = {"HasAcceptedTerms": "2", "CompletedTrainingVersion": "1"}

  def get(self, key):
    return self.d.get(key, "")

  def put(self, key, value):
    self.d[key] = value

  def get_bool(self, key):
    return bool(self.d.get(key, False))

  def put_bool(self, key, value):
    self.d[key] = value

  def remove(self, key):
    self.d.pop(key, None)


if __name__ == "__main__":
  raise SystemExit(main())
