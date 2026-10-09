from typing import cast

import pyray as rl
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.wrap_text import wrap_text
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.button import gui_button, ButtonStyle


class SetupWidget(Widget):
  """Setup widget for EOP — no cloud account pairing."""

  def __init__(self):
    super().__init__()
    self._open_settings_callback = None

  def set_open_settings_callback(self, callback):
    self._open_settings_callback = callback

  def _render(self, rect: rl.Rectangle):
    self._render_eop_setup(rect)

  def _render_eop_setup(self, rect: rl.Rectangle):
    """Render EOP setup prompt — no cloud services."""

    rl.draw_rectangle_rounded(rl.Rectangle(rect.x, rect.y, rect.width, 590), 0.02, 20, rl.Color(51, 51, 51, 255))

    x = rect.x + 64
    y = rect.y + 48
    w = rect.width - 128

    # Title
    font = gui_app.font(cast(FontWeight, FontWeight.BOLD))
    rl.draw_text_ex(font, "EOP Setup", rl.Vector2(x, y), 75, 0, rl.WHITE)
    y += 113  # 75 + 38 spacing

    # Description
    desc = "EnhancedOpenPilot (EOP) is ready to use. No cloud account required."
    light_font = gui_app.font(cast(FontWeight, FontWeight.LIGHT))
    wrapped = wrap_text(light_font, desc, 50, int(w))
    for line in wrapped:
      rl.draw_text_ex(light_font, line, rl.Vector2(x, y), 50, 0, rl.WHITE)
      y += 50

    y += 30
    desc2 = "All AI processing is local via Hailo-8. No data leaves your device."
    wrapped2 = wrap_text(light_font, desc2, 40, int(w))
    for line in wrapped2:
      rl.draw_text_ex(light_font, line, rl.Vector2(x, y), 40, 0, rl.Color(200, 200, 200, 255))
      y += 40

    button_rect = rl.Rectangle(x, y + 50, w, 128)
    if gui_button(button_rect, "Open Settings", button_style=ButtonStyle.PRIMARY):
      if self._open_settings_callback:
        self._open_settings_callback()
