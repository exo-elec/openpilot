"""Param-bound control widgets.

One widget per Kind in the descriptor. Each owns exactly one param and reads
and writes it directly -- there is no intermediate settings model, because a
second copy of the values is a second thing to keep in sync.

`Params` is injected rather than imported at module scope: openpilot's Params
is a compiled extension, so importing it here would make the whole settings UI
unimportable without a built tree, and would put a hard dependency in front of
every test.
"""

from __future__ import annotations

from openpilot.selfdrive.ui.eop.qt import (
  Qt,
  QTimer,
  QtWidgets,
  QWidget,
  Signal,
)
from openpilot.selfdrive.ui.eop.settings.descriptor import Control, Kind

# Params writes go through a temp file plus fsync_dir (common/params.cc), so
# every one is a synchronous disk sync. A spin box wired straight to
# valueChanged would fsync on every step -- holding an arrow across a 0..160
# range in steps of 5 is 32 syncs. Coalesce instead.
WRITE_DEBOUNCE_MS = 400


# Params whose value is a serialised cereal Event, mapped to the union field
# the value actually lives in.
_LOG_FIELDS = {
  "CalibrationParams": "liveCalibration",
  "LiveDelay": "liveDelay",
  "LiveTorqueParameters": "liveTorqueParameters",
}


class ParamStore:
  """Minimal interface the controls need. Wraps openpilot's Params."""

  def __init__(self, params=None):
    if params is None:
      from openpilot.common.params import Params
      params = Params()
    self._p = params

  def get_bool(self, key: str) -> bool:
    return bool(self._p.get_bool(key))

  def put_bool(self, key: str, value: bool) -> None:
    self._p.put_bool(key, value)

  def get_number(self, key: str, default: float = 0.0) -> float:
    raw = self._p.get(key)
    if raw is None or raw == "":
      return default
    try:
      return float(raw)
    except (TypeError, ValueError):
      return default

  def put_number(self, key: str, value: float) -> None:
    text = str(int(value)) if float(value).is_integer() else str(value)
    self._p.put(key, text)

  # ---- text and removal -------------------------------------------------

  def get_text(self, key: str) -> str:
    """A string param, decoded. Params returns bytes for some keys and str
    for others depending on how they were written, and a caller that gets the
    wrong one silently renders b'abc' on screen -- the same mixed bytes/str
    defect already fixed once in system/hardware/rk_device_id.py."""
    raw = self._p.get(key)
    if raw is None:
      return ""
    if isinstance(raw, bytes):
      return raw.decode(errors="replace")
    return str(raw)

  def put_text(self, key: str, value: str) -> None:
    self._p.put(key, value)

  def remove(self, key: str) -> None:
    self._p.remove(key)

  # ---- capnp-valued params ----------------------------------------------

  def _log_param(self, key: str, field: str):
    """Deserialise a param whose value is a serialised cereal Event.

    Several calibration params are stored this way. A corrupt or half-written
    blob must not take the settings screen down, so a failure reads as
    "no value yet" -- which is also what it means the first time the car is
    driven.
    """
    raw = self._p.get(key)
    if not raw:
      return None
    if isinstance(raw, str):
      raw = raw.encode()
    try:
      from cereal import log
      with log.Event.from_bytes(raw) as event:
        return getattr(event, field)
    except Exception:
      return None

  def get_calibration_angles(self) -> tuple[float, float] | None:
    """(pitch, yaw) in degrees, or None while uncalibrated."""
    import math
    cal = self._log_param("CalibrationParams", "liveCalibration")
    if cal is None or str(getattr(cal, "calStatus", "")) == "uncalibrated":
      return None
    rpy = list(getattr(cal, "rpyCalib", []) or [])
    if len(rpy) < 3:
      return None
    return math.degrees(rpy[1]), math.degrees(rpy[2])

  def get_calibration_percent(self, key: str) -> int | None:
    value = self._log_param(key, _LOG_FIELDS.get(key, key))
    if value is None:
      return None
    return int(getattr(value, "calPerc", 0))

  def get_torque_percent(self) -> int | None:
    """None for a car that does not use learned torque params, so the row
    does not claim a calibration that will never run is 0% done."""
    torque = self._log_param("LiveTorqueParameters", "liveTorqueParameters")
    if torque is None or not getattr(torque, "useParams", False):
      return None
    return int(getattr(torque, "calPerc", 0))


class ControlRow(QWidget):
  """Title, description and a widget, laid out for a touch panel."""

  changed = Signal(str, object)   # key, new value

  def __init__(self, control: Control, store: ParamStore, parent=None):
    super().__init__(parent)
    self.control = control
    self._store = store

    row = QtWidgets.QHBoxLayout(self)
    row.setContentsMargins(16, 10, 16, 10)
    row.setSpacing(16)

    text = QtWidgets.QVBoxLayout()
    title = QtWidgets.QLabel(control.title)
    title.setObjectName("controlTitle")
    text.addWidget(title)
    if control.desc:
      desc = QtWidgets.QLabel(control.desc)
      desc.setObjectName("controlDesc")
      desc.setWordWrap(True)
      text.addWidget(desc)
    row.addLayout(text, 1)

    self._pending: float | None = None
    self._write_timer = QTimer(self)
    self._write_timer.setSingleShot(True)
    self._write_timer.setInterval(WRITE_DEBOUNCE_MS)
    self._write_timer.timeout.connect(self._flush)

    self.widget = self._build()
    row.addWidget(self.widget, 0, Qt.AlignRight | Qt.AlignVCenter)

  def _build(self) -> QWidget:
    c = self.control
    if c.kind is Kind.TOGGLE:
      w = QtWidgets.QCheckBox()
      w.setChecked(self._store.get_bool(c.key))
      w.toggled.connect(self._on_toggle)
      return w

    if c.kind is Kind.SPINBOX:
      integral = float(c.step).is_integer() and float(c.min).is_integer()
      # QSpinBox takes ints only, and PyQt5 enforces that strictly where
      # PySide would coerce -- so cast rather than relying on the binding.
      cast = int if integral else float
      w = QtWidgets.QSpinBox() if integral else QtWidgets.QDoubleSpinBox()
      w.setRange(cast(c.min), cast(c.max))
      w.setSingleStep(cast(c.step))
      if not integral:
        # Enough decimals for the smallest step the descriptor asks for.
        w.setDecimals(max(1, len(str(c.step).split(".")[-1])))
      if c.unit:
        w.setSuffix(f" {c.unit}")
      w.setValue(cast(self._store.get_number(c.key, c.min)))
      w.valueChanged.connect(self._on_number)
      return w

    # BUTTONS: a horizontal exclusive group, index stored as the param value.
    box = QWidget()
    lay = QtWidgets.QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    self._buttons = []
    current = int(self._store.get_number(c.key, 0))
    for i, label in enumerate(c.options):
      b = QtWidgets.QPushButton(label)
      b.setCheckable(True)
      b.setChecked(i == current)
      b.clicked.connect(lambda _checked=False, idx=i: self._on_button(idx))
      lay.addWidget(b)
      self._buttons.append(b)
    return box

  # ---- handlers ---------------------------------------------------------

  def _on_toggle(self, checked: bool) -> None:
    self._store.put_bool(self.control.key, checked)
    self.changed.emit(self.control.key, checked)

  def _on_number(self, value) -> None:
    self._pending = value
    self._write_timer.start()          # restarts on each step
    self.changed.emit(self.control.key, value)

  def _flush(self) -> None:
    if self._pending is not None:
      self._store.put_number(self.control.key, self._pending)
      self._pending = None

  def refresh(self) -> None:
    """Re-read the param and update the widget without re-emitting.

    Controls used to read their value once, at construction, so anything that
    changed a param elsewhere -- another page, a daemon, adb param_set -- left
    the control showing a stale value until the UI restarted. That is the same
    failure openpilot's own audit found in ParamSpinBoxControl::refresh().
    """
    c = self.control
    w = self.widget
    w.blockSignals(True)
    try:
      if c.kind is Kind.TOGGLE:
        w.setChecked(self._store.get_bool(c.key))
      elif c.kind is Kind.SPINBOX:
        integral = float(c.step).is_integer() and float(c.min).is_integer()
        cast = int if integral else float
        w.setValue(cast(self._store.get_number(c.key, c.min)))
      else:
        current = int(self._store.get_number(c.key, 0))
        for i, b in enumerate(self._buttons):
          b.setChecked(i == current)
    finally:
      w.blockSignals(False)

  def hideEvent(self, event):
    # Leaving a page must not lose an edit that is still inside the debounce.
    super().hideEvent(event)
    self._write_timer.stop()
    self._flush()

  def _on_button(self, index: int) -> None:
    for i, b in enumerate(self._buttons):
      b.setChecked(i == index)
    self._store.put_number(self.control.key, index)
    self.changed.emit(self.control.key, index)
