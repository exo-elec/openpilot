"""Qt binding shim.

The target is **Ubuntu 22.04 on RK3576**, i.e. Qt 5.15 — the same Qt the
existing C++ UI already runs on that device. Three bindings can supply it and
all three are packaged for jammy on arm64, so the choice is not about
availability:

- **PyQt5** (`python3-pyqt5`) — the default here. Nagasware's 48,545 LOC of UI
  is written against it, so the port starts with zero conversion, and this is
  a research project where the GPL is not yet a constraint.
- **PySide2** (`python3-pyside2`) — LGPLv3. The one to move to **before
  anything ships**, because PyQt5 is GPLv3 or a paid Riverbank licence and
  openpilot is MIT
  distributing a PyQt5 UI would force GPL on the combined
  work. See docs/eop10/EOP10_PORT_PLAN.md section 12.1.
- **PySide6** — Qt6, not in jammy, no aarch64 wheels. Dev machines newer than
  the target tend to have it, so it is accepted as a last resort.

Deferring the licence decision is cheap *only if the code does not accumulate
binding-specific spellings in the meantime*, which is what this module is for.
Write `Signal`, never `pyqtSignal`
take `QOpenGLWidget` from here, not from a
binding module
use unscoped enum access (`Qt.WA_TranslucentBackground`),
which is native in Qt5 and accepted by PySide6's forgiveness mode. Held to
that, switching to PySide2 later is an edit to this file rather than a pass
over the whole UI.

`EOP_QT_BINDING=pyqt5|pyside2|pyside6` forces a specific binding, for testing
that the code really is neutral.
"""

from __future__ import annotations

import os

_FORCED = os.environ.get("EOP_QT_BINDING", "").strip().lower()
_ORDER = [_FORCED] if _FORCED else ["pyqt5", "pyside2", "pyside6"]

BINDING = ""
_errors: list[str] = []

for _name in _ORDER:
  try:
    if _name == "pyqt5":
      from PyQt5 import QtCore, QtDBus, QtGui, QtWidgets  # type: ignore
      from PyQt5.QtWidgets import QOpenGLWidget  # type: ignore
      # Aliased locally, not written back into QtCore. Assigning
      # QtCore.Signal = QtCore.pyqtSignal would change PyQt5's namespace for
      # every module in the process, not just this UI -- a side effect on a
      # third-party package that nothing here needs.
      _Signal, _Slot = QtCore.pyqtSignal, QtCore.pyqtSlot
    elif _name == "pyside2":
      from PySide2 import QtCore, QtDBus, QtGui, QtWidgets  # type: ignore
      from PySide2.QtWidgets import QOpenGLWidget  # type: ignore
      _Signal, _Slot = QtCore.Signal, QtCore.Slot
    elif _name == "pyside6":
      from PySide6 import QtCore, QtDBus, QtGui, QtWidgets  # type: ignore
      from PySide6.QtOpenGLWidgets import QOpenGLWidget  # type: ignore
      _Signal, _Slot = QtCore.Signal, QtCore.Slot
    else:
      raise ImportError(f"unknown binding {_name!r}")
    BINDING = _name
    break
  except ImportError as e:
    _errors.append(f"{_name}: {e}")

if not BINDING:
  raise ImportError("no Qt binding available -- tried " + "; ".join(_errors))

Qt = QtCore.Qt
Signal = _Signal
Slot = _Slot

# QtCore
QObject = QtCore.QObject
QTimer = QtCore.QTimer
QPoint = QtCore.QPoint
QPointF = QtCore.QPointF
QRect = QtCore.QRect
QRectF = QtCore.QRectF
QSize = QtCore.QSize

# QtGui
QBrush = QtGui.QBrush
QColor = QtGui.QColor
QFont = QtGui.QFont
QFontDatabase = QtGui.QFontDatabase
QFontMetrics = QtGui.QFontMetrics
QImage = QtGui.QImage
QLinearGradient = QtGui.QLinearGradient
QPainter = QtGui.QPainter
QPainterPath = QtGui.QPainterPath
QPen = QtGui.QPen
QPixmap = QtGui.QPixmap
QPolygonF = QtGui.QPolygonF
QRadialGradient = QtGui.QRadialGradient

# QtDBus. The network and Bluetooth panels talk to NetworkManager and BlueZ,
# the same way the C++ did, and QtDBus rather than dbus-python because it
# dispatches on the Qt event loop -- a second loop in the UI process is a
# second thing that can block the frame.
QDBusInterface = QtDBus.QDBusInterface
QDBusConnection = QtDBus.QDBusConnection
QDBusObjectPath = QtDBus.QDBusObjectPath
QDBusArgument = QtDBus.QDBusArgument

# QtWidgets
QApplication = QtWidgets.QApplication
QWidget = QtWidgets.QWidget

__all__ = [
  "BINDING", "QtCore", "QtGui", "QtWidgets", "Qt", "Signal", "Slot",
  "QObject", "QTimer", "QPoint", "QPointF", "QRect", "QRectF", "QSize",
  "QBrush", "QColor", "QFont", "QFontDatabase", "QFontMetrics", "QImage",
  "QLinearGradient", "QPainter", "QPainterPath", "QPen", "QPixmap",
  "QPolygonF", "QRadialGradient",
  "QApplication", "QWidget", "QOpenGLWidget", "run_app", "text_width",
  "QtDBus", "QDBusInterface", "QDBusConnection", "QDBusObjectPath",
  "QDBusArgument",
]


def text_width(metrics, text: str) -> int:
  """QFontMetrics.horizontalAdvance(), which is what both Qt5.11+ and Qt6
  spell it. Wrapped so a future PySide2 on an older Qt5 has one place to fall
  back to width() rather than a call site per HUD element."""
  return metrics.horizontalAdvance(text)


def run_app(app) -> int:
  """Enter the event loop. Qt5 bindings spell it exec_(); PySide6 exec()."""
  return app.exec_() if hasattr(app, "exec_") else app.exec()
