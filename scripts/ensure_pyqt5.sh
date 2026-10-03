#!/usr/bin/env bash
# Install the Python Qt binding into the active openpilot venv.
# PyPI has no Linux/aarch64 wheel for PyQt5. On RK boards, build its source
# distribution against Ubuntu's Qt 5 development packages installed by the
# platform installer. PyQt5-sip is installed by uv sync.

set -euo pipefail

PYTHON="${1:?usage: ensure_pyqt5.sh /path/to/python}"

if "$PYTHON" -c 'from PyQt5 import QtCore, QtDBus, QtGui, QtWidgets; from PyQt5.QtWidgets import QOpenGLWidget' >/dev/null 2>&1; then
  exit 0
fi

if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "PyQt5 is missing from this environment; run 'uv sync' and retry." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required to build PyQt5 into the openpilot venv." >&2
  exit 1
fi
if ! command -v qmake >/dev/null 2>&1; then
  echo "Qt 5 development tools are missing; install the platform packages first." >&2
  exit 1
fi

echo "Building PyQt5 5.15.11 for Linux/aarch64 against the system Qt 5..."
uv pip install --python "$PYTHON" --no-deps "PyQt5==5.15.11"
"$PYTHON" -c 'from PyQt5 import QtCore, QtDBus, QtGui, QtWidgets; from PyQt5.QtWidgets import QOpenGLWidget'
