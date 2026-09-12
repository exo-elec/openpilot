"""Device panel: identity, calibration, language, power.

Port of settings.cc's DevicePanel. Every action that cannot be undone asks
first, and every one of them re-checks engagement *after* the dialog closes
rather than only before it -- the car can be engaged in the seconds a
confirmation is on screen, and rebooting then is the worst possible outcome.
"""

from __future__ import annotations

from openpilot.selfdrive.ui.eop.components.controls import ParamStore
from openpilot.selfdrive.ui.eop.components.dialogs import (
  alert_dialog,
  confirm_dialog,
  selection_dialog,
)
from openpilot.selfdrive.ui.eop.components.list_controls import (
  ButtonControl,
  LabelControl,
  ListWidget,
)
from openpilot.selfdrive.ui.eop.qt import QtWidgets, Signal

# Params cleared by a calibration reset. Listed here rather than inline so the
# set is visible in one place -- dropping one leaves the car driving on half
# of a stale calibration, which is worse than not resetting at all.
CALIBRATION_PARAMS = (
  "CalibrationParams",
  "LiveTorqueParameters",
  "LiveParameters",
  "LiveParametersV2",
  "LiveDelay",
)

# UI language file -> the 2-letter code the TTS engine wants. Both Chinese
# variants speak the same language even though they are written differently.
LANGUAGE_TO_TTS = {
  "main_en": "en", "main_de": "de", "main_fr": "fr", "main_pt-BR": "pt",
  "main_es": "es", "main_tr": "tr", "main_ar": "ar", "main_nl": "nl",
  "main_pl": "pl", "main_zh-CHS": "zh", "main_zh-CHT": "zh",
  "main_ja": "ja", "main_ko": "ko", "main_th": "th",
}

POWER_STYLE = """
QPushButton#rebootBtn { height: 60px; border-radius: 12px; background-color: #393939; }
QPushButton#rebootBtn:pressed { background-color: #4a4a4a; }
QPushButton#powerOffBtn { height: 60px; border-radius: 12px; background-color: #E22C2C; }
QPushButton#powerOffBtn:pressed { background-color: #FF2424; }
"""


class DevicePanel(ListWidget):
  training_guide_requested = Signal()

  def __init__(self, store: ParamStore | None = None, languages=None, parent=None):
    super().__init__(spacing=50, parent=parent)
    self._store = store if store is not None else ParamStore()
    self._languages = languages if languages is not None else supported_languages()
    self._engaged = False
    self._offroad = True
    self.setStyleSheet(POWER_STYLE)

    self.dongle = LabelControl("Dongle ID", self._text_param("DongleId", "N/A"))
    self.add_row(self.dongle)
    self.serial = LabelControl("Serial", self._text_param("HardwareSerial", "N/A"))
    self.add_row(self.serial)

    self.reset_calib = ButtonControl("Reset Calibration", "RESET")
    self.reset_calib.clicked.connect(self._reset_calibration)
    self.reset_calib.description_shown.connect(self._refresh_calibration_text)
    self.add_row(self.reset_calib)

    self.training = ButtonControl("Review Training Guide", "REVIEW")
    self.training.clicked.connect(self._review_training)
    self.add_row(self.training)

    self.language = ButtonControl("Change Language", "CHANGE")
    self.language.clicked.connect(self._change_language)
    self.add_row(self.language)

    power = QtWidgets.QHBoxLayout()
    power.setSpacing(30)
    self.reboot_btn = QtWidgets.QPushButton("Reboot")
    self.reboot_btn.setObjectName("rebootBtn")
    self.reboot_btn.clicked.connect(self._reboot)
    self.power_off_btn = QtWidgets.QPushButton("Power Off")
    self.power_off_btn.setObjectName("powerOffBtn")
    self.power_off_btn.clicked.connect(self._power_off)
    power.addWidget(self.reboot_btn)
    power.addWidget(self.power_off_btn)
    self.add_layout(power)
    self.add_stretch()

  # ---- state ------------------------------------------------------------

  def set_driving_state(self, engaged: bool, offroad: bool) -> None:
    self._engaged = engaged
    self._offroad = offroad
    # Reset Calibration stays available onroad because it guards itself on
    # engagement, which is the condition that actually matters. Everything
    # else here is a parked-only action.
    for row in (self.training, self.language):
      row.set_enabled(offroad)
    self.power_off_btn.setVisible(offroad)

  def _text_param(self, key: str, fallback: str) -> str:
    raw = self._store.get_text(key)
    return raw if raw else fallback

  # ---- actions ----------------------------------------------------------

  def _guarded(self, question: str, confirm_text: str, refusal: str) -> bool:
    """Confirm an action that must not happen while openpilot is driving.

    Engagement is re-checked after the dialog closes, not only before it. The
    car can be engaged during the seconds the question is on screen, and
    acting on the stale answer is how a reboot lands mid-drive.
    """
    if self._engaged:
      alert_dialog(refusal, self)
      return False
    if not confirm_dialog(question, confirm_text, self):
      return False
    if self._engaged:
      alert_dialog(refusal, self)
      return False
    return True

  def _reset_calibration(self) -> None:
    if not self._guarded("Are you sure you want to reset calibration?",
                         "Reset", "Disengage to Reset Calibration"):
      return
    for key in CALIBRATION_PARAMS:
      self._store.remove(key)
    self._store.put_bool("OnroadCycleRequested", True)
    self._refresh_calibration_text()

  def _review_training(self) -> None:
    if confirm_dialog("Are you sure you want to review the training guide?",
                      "Review", self):
      self.training_guide_requested.emit()

  def _change_language(self) -> None:
    names = sorted(self._languages)
    current_file = self._store.get_text("LanguageSetting")
    current = next((n for n in names if self._languages[n] == current_file), "")
    chosen = selection_dialog("Select a language", names, current, self)
    if not chosen:
      return
    lang_file = self._languages[chosen]
    self._store.put_text("LanguageSetting", lang_file)
    # Kept in step deliberately: EOPLanguage drives text-to-speech, and a UI
    # in Thai reading alerts aloud in English is worse than either alone.
    self._store.put_text("EOPLanguage", LANGUAGE_TO_TTS.get(lang_file, "en"))
    _restart_ui()

  def _reboot(self) -> None:
    if self._guarded("Are you sure you want to reboot?", "Reboot",
                     "Disengage to Reboot"):
      self._store.put_bool("DoReboot", True)

  def _power_off(self) -> None:
    if self._guarded("Are you sure you want to power off?", "Power Off",
                     "Disengage to Power Off"):
      self._store.put_bool("DoShutdown", True)

  # ---- calibration description ------------------------------------------

  def _refresh_calibration_text(self) -> None:
    """Built only when the description is opened, not on every tick: it
    deserialises three capnp blobs out of Params."""
    self.reset_calib.set_description(calibration_description(self._store))


def calibration_description(store: ParamStore) -> str:
  parts = ["openpilot requires the device to be mounted within 4° left or right and within 5° up or 9° down."]

  angles = store.get_calibration_angles()
  if angles is not None:
    pitch, yaw = angles
    parts.append(" Your device is pointed {:.1f}° {} and {:.1f}° {}.".format(
      abs(pitch), "down" if pitch > 0 else "up",
      abs(yaw), "left" if yaw > 0 else "right"))

  lag = store.get_calibration_percent("LiveDelay")
  if lag is not None:
    parts.append("\n\nSteering lag calibration is complete."
                 if lag >= 100 else
                 f"\n\nSteering lag calibration is {lag}% complete.")

  torque = store.get_torque_percent()
  if torque is not None:
    parts.append(" Steering torque response calibration is complete."
                 if torque >= 100 else
                 f" Steering torque response calibration is {torque}% complete.")

  parts.append("\n\nCalibration resets rarely needed. Will restart openpilot if car is on.")
  return "".join(parts)


def supported_languages() -> dict[str, str]:
  """Display name -> translation file stem, from translations/languages.json."""
  import json
  from pathlib import Path
  path = Path(__file__).resolve().parents[3] / "translations" / "languages.json"
  try:
    return json.loads(path.read_text())
  except (OSError, ValueError):
    # A missing or unreadable catalogue must not take the settings screen
    # down; English always exists because it is the source language.
    return {"English": "main_en"}


def _restart_ui() -> None:
  """Exit with the code the launcher treats as "restart me".

  A language change cannot be applied in place: Qt loads a QTranslator at
  startup and retranslating a live widget tree is not something Qt supports
  for text already set. The C++ used the same exit code.
  """
  from openpilot.selfdrive.ui.eop.qt import QApplication
  app = QApplication.instance()
  if app is not None:
    app.exit(18)
