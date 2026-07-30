import pyray as rl

from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.wrap_text import wrap_text
from openpilot.system.ui.widgets import Widget


class PrimeWidget(Widget):
  """Widget for displaying EOP status — no cloud subscription."""

  BG_COLOR = rl.Color(51, 51, 51, 255)

  def _render(self, rect):
    self._render_eop_status(rect)

  def _render_eop_status(self, rect: rl.Rectangle):
    """Renders the EOP offline status widget."""

    rl.draw_rectangle_rounded(rect, 0.02, 10, self.BG_COLOR)

    x = rect.x + 56
    y = rect.y + 40
    w = rect.width - 112

    font = gui_app.font(FontWeight.BOLD)
    rl.draw_text_ex(font, "✓ OFFLINE", rl.Vector2(x, y), 41, 0, rl.Color(134, 255, 78, 255))
    rl.draw_text_ex(font, "EnhancedOpenPilot", rl.Vector2(x, y + 61), 75, 0, rl.WHITE)

    y += 160
    desc_font = gui_app.font(FontWeight.LIGHT)
    desc = "All processing is local. No cloud account required."
    wrapped = wrap_text(desc_font, desc, 45, int(w))
    for line in wrapped:
      rl.draw_text_ex(desc_font, line, rl.Vector2(x, y), 45, 0, rl.Color(200, 200, 200, 255))
      y += 45
