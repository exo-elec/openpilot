"""Qt imports for the EOP UI.

**PyQt5 only.** The target is Ubuntu 22.04 on Rockchip, i.e. Qt 5.15 -- the
same Qt the C++ UI ran on that device -- and `python3-pyqt5` is packaged for
jammy on arm64. Nagasware's UI is written against PyQt5, so the port started
with zero conversion.

This module exists so that every Qt name the UI uses comes from one place.
That is still worth having with a single binding: `QOpenGLWidget` moved
modules between Qt5 and Qt6, `exec_()` was renamed, and `QFontMetrics.width`
was replaced by `horizontalAdvance` -- so a future Qt move is an edit here
rather than a pass over the whole UI.

There is no PySide fallback. Carrying one meant every spelling had to be
checked against two bindings, and the abstraction leaked anyway: QDBus enums
are scoped in one and not the other, QSpinBox coerces floats in one and not
the other. Note that PyQt5 is GPLv3 or a paid Riverbank licence while
openpilot is MIT, so the licence question has to be answered before any of
this is distributed -- it is a deliberate, recorded choice for a research
project, not an oversight.
"""

from __future__ import annotations

from PyQt5 import QtCore, QtDBus, QtGui, QtWidgets
from PyQt5.QtWidgets import QOpenGLWidget

BINDING = "pyqt5"

# Aliased locally, not written back into QtCore. Assigning
# QtCore.Signal = QtCore.pyqtSignal would change PyQt5's namespace for every
# module in the process, not just this UI -- a side effect on a third-party
# package that nothing here needs.
Signal = QtCore.pyqtSignal
Slot = QtCore.pyqtSlot

Qt = QtCore.Qt

# QtCore
QCoreApplication = QtCore.QCoreApplication
QLocale = QtCore.QLocale
QObject = QtCore.QObject
QTranslator = QtCore.QTranslator
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
QDBusMessage = QtDBus.QDBusMessage

# QtWidgets
QApplication = QtWidgets.QApplication
QWidget = QtWidgets.QWidget

__all__ = [
  "BINDING", "QtCore", "QtGui", "QtWidgets", "Qt", "Signal", "Slot",
  "QCoreApplication", "QLocale", "QObject", "QTimer", "QTranslator",
  "QPoint", "QPointF", "QRect", "QRectF", "QSize",
  "QBrush", "QColor", "QFont", "QFontDatabase", "QFontMetrics", "QImage",
  "QLinearGradient", "QPainter", "QPainterPath", "QPen", "QPixmap",
  "QPolygonF", "QRadialGradient",
  "QApplication", "QWidget", "QOpenGLWidget", "run_app", "text_width",
  "translate",
  "QtDBus", "QDBusInterface", "QDBusConnection", "QDBusObjectPath",
  "QDBusArgument", "QDBusMessage",
]


def text_width(metrics, text: str) -> int:
  """QFontMetrics.horizontalAdvance(). Wrapped so a Qt older than 5.11, where
  it is spelled width(), has one place to fall back rather than a call site
  per HUD element."""
  return metrics.horizontalAdvance(text)


def run_app(app) -> int:
  """Enter the event loop."""
  return app.exec_()


def translate(context: str, text: str) -> str:
  """Translate `text` in `context`, for code that is not inside a QObject.

  A QObject subclass should call `self.tr(text)` instead -- Qt takes the
  context from the class name, which is what the .ts catalogue is keyed on.
  This is for free functions and dialogs, where the context has to be named
  explicitly to match the catalogue entry.
  """
  return QCoreApplication.translate(context, text)
