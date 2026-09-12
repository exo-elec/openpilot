"""ExoPilot panel: the EOP-specific settings.

Built from settings/descriptor.py, which is generated from eop_panel.cc --
259 EOP params exist and hand-transcribing any part of that is how a settings
screen silently loses controls. tests/test_params_coverage.py asserts every
EOP* key in params_keys.h is either here or explicitly excluded with a reason.

The C++ put all of these in one long scrolling panel. That is kept: 01M's
design is what a driver already knows, and reorganising it into pages is the
02M change, not this one. The descriptor's page grouping is used as section
headings inside the single scroll instead, which adds structure without
moving anything.
"""

from __future__ import annotations

from openpilot.selfdrive.ui.eop.components.controls import ControlRow, ParamStore
from openpilot.selfdrive.ui.eop.components.list_controls import ListWidget
from openpilot.selfdrive.ui.eop.qt import QtWidgets

# Human headings for the descriptor's page keys. Kept here rather than in the
# descriptor because the descriptor is generated, and regenerating it should
# never silently revert a wording change.
SECTION_TITLES = {
  "device": "Device",
  "lateral": "Steering",
  "cruise": "Cruise",
  "perception": "Perception",
  "map": "Map & GPS",
  "safety": "Safety",
  "vehicle": "Vehicle",
  "dvr": "Recording",
  "voice": "Voice",
}
SECTION_ORDER = ["device", "lateral", "cruise", "safety", "perception",
                 "map", "vehicle", "dvr", "voice"]

HEADING_STYLE = """
  color: #FFFFFF;
  font-size: 26px;
  font-weight: 600;
  padding: 28px 50px 8px 50px;
  background-color: #202020;
"""


def ordered_pages(pages):
  """Descriptor pages in SECTION_ORDER, with anything unlisted appended.

  Appending rather than dropping matters: a page added to eop_panel.cc and
  regenerated but not listed here would otherwise vanish from settings
  without any test noticing.
  """
  by_name = {p.name: p for p in pages}
  out = [by_name[n] for n in SECTION_ORDER if n in by_name]
  out += [p for p in pages if p.name not in SECTION_ORDER]
  return out


class ExoPilotPanel(ListWidget):
  def __init__(self, store: ParamStore | None = None, pages=None, parent=None):
    super().__init__(parent=parent)
    from openpilot.selfdrive.ui.eop.settings.descriptor import PAGES
    self._store = store if store is not None else ParamStore()

    self.control_rows: list[ControlRow] = []
    for page in ordered_pages(pages if pages is not None else PAGES):
      heading = QtWidgets.QLabel(SECTION_TITLES.get(page.name, page.name.title()))
      heading.setStyleSheet(HEADING_STYLE)
      self.add_row(heading)
      for control in page.controls:
        row = ControlRow(control, self._store)
        self.add_row(row)
        self.control_rows.append(row)
    self.add_stretch()

  def refresh(self) -> None:
    for row in self.control_rows:
      row.refresh()
