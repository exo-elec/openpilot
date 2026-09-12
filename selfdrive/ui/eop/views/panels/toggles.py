"""Toggles panel: the core openpilot behaviour switches.

Port of settings.cc's TogglesPanel. The list and its wording are openpilot's,
unchanged -- these are the settings a driver coming from stock openpilot
expects to find, in the order they expect to find them.
"""

from __future__ import annotations

from openpilot.selfdrive.ui.eop.components.controls import ParamStore
from openpilot.selfdrive.ui.eop.components.list_controls import (
  ButtonParamControl,
  ListWidget,
  ToggleControl,
)
from openpilot.selfdrive.ui.eop.qt import Signal

# param, title, description, restart-required
TOGGLES = [
  ("OpenpilotEnabledToggle", "Enable openpilot",
   "Adaptive cruise control and lane keep assist. Pay attention at all times.", True),
  ("ExperimentalMode", "Experimental Mode", "", False),
  ("DisengageOnAccelerator", "Disengage on Accelerator",
   "Pressing the accelerator will disengage openpilot.", False),
  ("IsLdwEnabled", "Lane Departure Warnings",
   "Alert when drifting over a lane line without turn signal above 50 km/h.", False),
  ("RecordAudio", "Record Microphone Audio",
   "Include audio in dashcam recording.", True),
  ("IsMetric", "Use Metric System", "Display speed in km/h instead of mph.", False),
]

PERSONALITIES = ["Aggressive", "Standard", "Relaxed"]

RESTART_NOTE = " Changing this setting will restart openpilot if the car is powered on."

E2E_DESCRIPTION = "openpilot defaults to driving in chill mode. Experimental mode enables alpha-level features that aren't ready for chill mode."
NO_EXPERIMENTAL = "Experimental mode unavailable — car uses stock ACC for longitudinal control."


class TogglesPanel(ListWidget):
  """The stock openpilot toggles, plus driving personality."""

  def __init__(self, store: ParamStore | None = None, parent=None):
    super().__init__(parent=parent)
    self._store = store if store is not None else ParamStore()
    self.toggles: dict[str, ToggleControl] = {}

    for param, title, desc, needs_restart in TOGGLES:
      locked = self._locked(param)
      full_desc = desc + RESTART_NOTE if needs_restart and not locked else desc
      row = ToggleControl(param, title, full_desc, self._store,
                          confirm=param == "ExperimentalMode")
      row.set_enabled(not locked)
      if needs_restart and not locked:
        # openpilot has to be restarted for these to take effect, and the
        # manager does that itself when it sees the request.
        row.toggled.connect(lambda _c: self._store.put_bool("OnroadCycleRequested", True))
      self.add_row(row)
      self.toggles[param] = row

      if param == "DisengageOnAccelerator":
        self.personality = ButtonParamControl(
          "LongitudinalPersonality", "Driving Personality",
          "How closely openpilot follows lead cars. Standard recommended.",
          PERSONALITIES, self._store)
        self.add_row(self.personality)

    self.add_stretch()

  def _locked(self, param: str) -> bool:
    """A `<Param>Lock` param pins a setting: a fleet or a study can fix a
    behaviour and have the UI show it as fixed rather than silently reverting
    what the driver changes."""
    try:
      return self._store.get_bool(param + "Lock")
    except Exception:
      # Lock keys are optional and mostly undeclared, so a missing one means
      # "not locked" rather than being a defect the way a missing real param
      # would be.
      return False

  # ---- live state -------------------------------------------------------

  def set_engaged(self, engaged: bool) -> None:
    """Restart-requiring toggles are frozen while engaged, so a setting
    change cannot cycle openpilot out from under a driver mid-drive."""
    for param, _title, _desc, needs_restart in TOGGLES:
      if needs_restart and not self._locked(param):
        self.toggles[param].set_enabled(not engaged)

  def set_personality(self, index: int) -> None:
    self.personality.set_checked_button(index)

  def set_longitudinal_available(self, available: bool) -> None:
    """Experimental mode does nothing on a car where openpilot is not doing
    the accelerating, so the row says so rather than offering a switch that
    has no effect."""
    row = self.toggles["ExperimentalMode"]
    row.set_description(E2E_DESCRIPTION if available
                        else f"{NO_EXPERIMENTAL} {E2E_DESCRIPTION}")
    row.set_enabled(available)


class DeveloperPanel(ListWidget):
  """Debug access and alpha features. Port of developer_panel.cc."""

  ssh_requested = Signal()

  def __init__(self, store: ParamStore | None = None, parent=None):
    super().__init__(parent=parent)
    self._store = store if store is not None else ParamStore()

    self.adb = ToggleControl(
      "AdbEnabled", "Enable ADB",
      "USB/network debug access. See docs.exo-electronics.com for details.",
      self._store)
    self.add_row(self.adb)

    self.ssh = ToggleControl("SshEnabled", "Enable SSH", "", self._store)
    self.add_row(self.ssh)

    self.joystick = ToggleControl("JoystickDebugMode", "Joystick Debug Mode",
                                  "", self._store)
    self.add_row(self.joystick)

    self.long_maneuver = ToggleControl("LongitudinalManeuverMode",
                                       "Longitudinal Maneuver Mode", "", self._store)
    self.add_row(self.long_maneuver)

    self.alpha_long = ToggleControl(
      "ExperimentalLongitudinalEnabled",
      "openpilot Longitudinal Control (Alpha)",
      "WARNING: Disables stock AEB. Switches from car's ACC to openpilot longitudinal (alpha).",
      self._store, confirm=True)
    self.add_row(self.alpha_long)
    self.add_stretch()

    # Joystick and maneuver mode both take over control, so enabling either
    # must restart openpilot rather than taking effect mid-drive.
    for row in (self.joystick, self.long_maneuver, self.alpha_long):
      row.toggled.connect(lambda _c: self._store.put_bool("OnroadCycleRequested", True))

  def set_offroad(self, offroad: bool) -> None:
    # Every row here changes how the car is driven. None of them may be
    # touched while it is moving.
    for row in (self.adb, self.ssh, self.joystick, self.long_maneuver,
                self.alpha_long):
      row.set_enabled(offroad)
