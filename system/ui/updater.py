#!/usr/bin/env python3
import sys
import pyray as rl
from enum import IntEnum

from openpilot.common.params import Params
from openpilot.system.hardware import HARDWARE
from typing import cast
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.wifi_manager import WifiManagerWrapper
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.button import gui_button, ButtonStyle
from openpilot.system.ui.widgets.label import gui_text_box, gui_label
from openpilot.system.ui.widgets.network import WifiManagerUI

# Constants
MARGIN = 50
BUTTON_HEIGHT = 160
BUTTON_WIDTH = 400
PROGRESS_BAR_HEIGHT = 72
TITLE_FONT_SIZE = 80
BODY_FONT_SIZE = 65
BACKGROUND_COLOR = rl.BLACK
PROGRESS_BG_COLOR = rl.Color(41, 41, 41, 255)
PROGRESS_COLOR = rl.Color(54, 77, 239, 255)


class Screen(IntEnum):
  PROMPT = 0
  WIFI = 1
  PROGRESS = 2


# Human-readable mapping for the UpdateStatus lifecycle written by system/updated.py.
UPDATE_STATUS_TEXT = {
  "checking": "Checking for updates...",
  "prepareDownload": "Downloading update...",
  "installing": "Installing update...",
  "success": "Update ready",
  "latest": "Software is up to date",
  "noInternet": "Waiting for network...",
  "fetchFailed": "Update check failed",
  "unsavedChanges": "Local changes block updates",
  "waiting": "Waiting...",
}


class Updater(Widget):
  def __init__(self, updater_path=None, manifest_path=None):
    super().__init__()
    # Legacy CLI args are accepted for compatibility but ignored; updates are
    # staged in the background by system/updated.py using Git + OverlayFS.
    self.updater = updater_path
    self.manifest = manifest_path
    self.current_screen = Screen.PROMPT

    self.progress_value = 0
    self.progress_text = "Loading..."
    self.show_reboot_button = False
    self.params = Params()
    self.wifi_manager = WifiManagerWrapper()
    self.wifi_manager_ui = WifiManagerUI(self.wifi_manager)

  def _read_status(self) -> tuple[str, bool]:
    status = self.params.get("UpdateStatus", encoding='utf8') or "waiting"
    available = self.params.get_bool("UpdateAvailable")
    return status, available

  def install_update(self):
    # The background daemon has already staged the update; the user just needs
    # to reboot so launch_chffrplus.sh can swap in the finalized overlay.
    self.current_screen = Screen.PROGRESS
    self.progress_value = 100
    self.progress_text = "Rebooting to finish update..."
    self.show_reboot_button = False
    HARDWARE.reboot()

  def render_prompt_screen(self, rect: rl.Rectangle):
    status, available = self._read_status()

    title = "Update Available" if available else "Software Update"
    title_rect = rl.Rectangle(MARGIN + 50, 250, rect.width - MARGIN * 2 - 100, TITLE_FONT_SIZE)
    gui_label(title_rect, title, TITLE_FONT_SIZE, font_weight=cast(FontWeight, FontWeight.BOLD))

    status_text = UPDATE_STATUS_TEXT.get(status, status)
    desc_text = (
      "An update has been downloaded and is ready to install. "
      "Reboot now to apply it."
      if available else
      f"Status: {status_text}"
    )

    desc_rect = rl.Rectangle(MARGIN + 50, 250 + TITLE_FONT_SIZE + 75, rect.width - MARGIN * 2 - 100, BODY_FONT_SIZE * 3)
    gui_text_box(desc_rect, desc_text, BODY_FONT_SIZE)

    button_y = rect.height - MARGIN - BUTTON_HEIGHT
    button_width = (rect.width - MARGIN * 3) // 2

    wifi_button_rect = rl.Rectangle(MARGIN, button_y, button_width, BUTTON_HEIGHT)
    if gui_button(wifi_button_rect, "Connect to Wi-Fi"):
      self.current_screen = Screen.WIFI
      return

    if available:
      install_button_rect = rl.Rectangle(MARGIN * 2 + button_width, button_y, button_width, BUTTON_HEIGHT)
      if gui_button(install_button_rect, "Reboot", button_style=ButtonStyle.PRIMARY):
        self.install_update()
        return
    else:
      back_button_rect = rl.Rectangle(MARGIN * 2 + button_width, button_y, button_width, BUTTON_HEIGHT)
      if gui_button(back_button_rect, "Back"):
        # Exit the updater; the background daemon continues checking.
        gui_app.close()
        return

  def render_wifi_screen(self, rect: rl.Rectangle):
    wifi_rect = rl.Rectangle(MARGIN + 50, MARGIN, rect.width - MARGIN * 2 - 100, rect.height - MARGIN * 2 - BUTTON_HEIGHT - 20)
    self.wifi_manager_ui.render(wifi_rect)

    back_button_rect = rl.Rectangle(MARGIN, rect.height - MARGIN - BUTTON_HEIGHT, BUTTON_WIDTH, BUTTON_HEIGHT)
    if gui_button(back_button_rect, "Back"):
      self.current_screen = Screen.PROMPT
      return

  def render_progress_screen(self, rect: rl.Rectangle):
    title_rect = rl.Rectangle(MARGIN + 100, 330, rect.width - MARGIN * 2 - 200, 100)
    gui_label(title_rect, self.progress_text, 90, font_weight=cast(FontWeight, FontWeight.SEMI_BOLD))

    bar_rect = rl.Rectangle(MARGIN + 100, 330 + 100 + 100, rect.width - MARGIN * 2 - 200, PROGRESS_BAR_HEIGHT)
    rl.draw_rectangle_rounded(bar_rect, 0.5, 10, PROGRESS_BG_COLOR)

    progress_width = (bar_rect.width * self.progress_value) / 100
    if progress_width > 0:
      progress_rect = rl.Rectangle(bar_rect.x, bar_rect.y, progress_width, bar_rect.height)
      rl.draw_rectangle_rounded(progress_rect, 0.5, 10, PROGRESS_COLOR)

    if self.show_reboot_button:
      reboot_rect = rl.Rectangle(MARGIN + 100, rect.height - MARGIN - BUTTON_HEIGHT, BUTTON_WIDTH, BUTTON_HEIGHT)
      if gui_button(reboot_rect, "Reboot"):
        HARDWARE.reboot()
        return

  def _render(self, rect: rl.Rectangle):
    if self.current_screen == Screen.PROMPT:
      self.render_prompt_screen(rect)
    elif self.current_screen == Screen.WIFI:
      self.render_wifi_screen(rect)
    elif self.current_screen == Screen.PROGRESS:
      self.render_progress_screen(rect)


def main():
  # Args are optional and ignored; kept for launcher compatibility.
  updater_path = sys.argv[1] if len(sys.argv) > 1 else None
  manifest_path = sys.argv[2] if len(sys.argv) > 2 else None

  try:
    gui_app.init_window("System Update")
    updater = Updater(updater_path, manifest_path)
    for _ in gui_app.render():
      updater.render(rl.Rectangle(0, 0, gui_app.width, gui_app.height))
  finally:
    gui_app.close()


if __name__ == "__main__":
  main()
