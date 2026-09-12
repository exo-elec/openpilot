"""Software panel: version, updates, target branch, uninstall.

Port of software_settings.cc. The updater is a separate daemon; this panel
only pokes it with signals and reads the state it publishes through Params,
which is why nearly all the logic here is reading params rather than doing
anything.
"""

from __future__ import annotations

import signal
import subprocess
from datetime import datetime, timezone

from openpilot.selfdrive.ui.eop.components.controls import ParamStore
from openpilot.selfdrive.ui.eop.components.dialogs import (
  confirm_dialog,
  selection_dialog,
)
from openpilot.selfdrive.ui.eop.components.list_controls import (
  ButtonControl,
  LabelControl,
  ListWidget,
)
from openpilot.selfdrive.ui.eop.qt import QtWidgets

UPDATER_PATTERN = "system.updated.updated"

# Branches pinned to the top of the selection list because they are the ones
# anybody actually picks; everything else follows in whatever order the
# updater reported.
PREFERRED_BRANCHES = ("devel-staging", "devel", "nightly", "nightly-dev", "master")

ONROAD_NOTE = "Updates are only downloaded while the car is off."


def signal_updater(sig: int) -> bool:
  """Signal the updater daemon. SIGUSR1 means check, SIGHUP means download.

  pkill rather than a pidfile because that is what the updater's own
  interface is. Returns whether a process was signalled, so the caller can
  tell "nothing happened" from "asked, waiting".
  """
  try:
    result = subprocess.run(["pkill", f"-{sig}", "-f", UPDATER_PATTERN],
                            check=False, capture_output=True)
  except (OSError, ValueError):
    return False
  return result.returncode == 0


def time_ago(stamp: str) -> str:
  """Render an ISO timestamp as "3 hours ago". Params stores it without a
  zone, and it is written in UTC, so the zone is supplied here."""
  if not stamp:
    return "never"
  try:
    when = datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)
  except ValueError:
    return "never"
  seconds = (datetime.now(timezone.utc) - when).total_seconds()
  if seconds < 60:
    return "now"
  for limit, div, unit in ((3600, 60, "minute"), (86400, 3600, "hour"),
                           (2592000, 86400, "day")):
    if seconds < limit:
      n = int(seconds // div)
      return f"{n} {unit}{'s' if n != 1 else ''} ago"
  n = int(seconds // 2592000)
  return f"{n} month{'s' if n != 1 else ''} ago"


def order_branches(available: str, current: str, target: str) -> list[str]:
  """Available branches with the interesting ones first.

  The current branch leads, then the well-known ones, then the rest. Each is
  moved rather than copied, so a branch never appears twice in the list.
  """
  branches = [b.strip() for b in available.split(",") if b.strip()]
  for name in (*reversed(PREFERRED_BRANCHES), current):
    if name and name in branches:
      branches.remove(name)
      branches.insert(0, name)
  if target and target not in branches:
    # The updater has not reported the branch the device is already pointed
    # at. Showing the list without it would make the current setting
    # invisible and un-reselectable.
    branches.append(target)
  return branches


class SoftwarePanel(ListWidget):
  def __init__(self, store: ParamStore | None = None, parent=None):
    super().__init__(parent=parent)
    self._store = store if store is not None else ParamStore()
    self._onroad = False

    self.onroad_note = QtWidgets.QLabel(ONROAD_NOTE)
    self.onroad_note.setStyleSheet("font-size: 22px; padding: 12px 50px;")
    self.onroad_note.hide()
    self.add_row(self.onroad_note)

    self.version = LabelControl("Current Version", "")
    self.add_row(self.version)

    self.download = ButtonControl("Download", "CHECK")
    self.download.clicked.connect(self._on_download)
    self.add_row(self.download)

    self.install = ButtonControl("Install Update", "INSTALL")
    self.install.clicked.connect(self._on_install)
    self.add_row(self.install)

    self.target_branch = ButtonControl("Target Branch", "SELECT")
    self.target_branch.clicked.connect(self._on_select_branch)
    self.add_row(self.target_branch)
    # A tested branch is pinned on purpose: letting a user move a release
    # device onto master from a settings screen is how a car ends up running
    # untested driving code.
    self.target_branch.setVisible(not self._store.get_bool("IsTestedBranch"))

    self.uninstall = ButtonControl("Uninstall", "UNINSTALL")
    self.uninstall.clicked.connect(self._on_uninstall)
    self.add_row(self.uninstall)
    self.add_stretch()

  # ---- state ------------------------------------------------------------

  def set_offroad(self, offroad: bool) -> None:
    self._onroad = not offroad
    self.refresh()

  def refresh(self) -> None:
    self.onroad_note.setVisible(self._onroad)
    # The updater only runs offroad, so offering the button onroad would be
    # offering something that cannot work.
    self.download.setVisible(not self._onroad)

    self._refresh_download()
    self.target_branch.set_value(self._store.get_text("UpdaterTargetBranch"))

    self.version.set_value(self._store.get_text("UpdaterCurrentDescription"))
    self.version.set_description(self._store.get_text("UpdaterCurrentReleaseNotes"))

    update_ready = self._store.get_bool("UpdateAvailable")
    self.install.setVisible(not self._onroad and update_ready)
    self.install.set_value(self._store.get_text("UpdaterNewDescription"))
    self.install.set_description(self._store.get_text("UpdaterNewReleaseNotes"))
    self.update()

  def _refresh_download(self) -> None:
    state = self._store.get_text("UpdaterState")
    if state and state != "idle":
      # Mid-check or mid-download: show what it is doing and do not let a
      # second press stack another request on top.
      self.download.set_enabled(False)
      self.download.set_value(state)
      return

    failed = _as_int(self._store.get_text("UpdateFailedCount")) > 0
    if failed:
      self.download.set_button_text("CHECK")
      self.download.set_value("failed to check for update")
    elif self._store.get_bool("UpdaterFetchAvailable"):
      self.download.set_button_text("DOWNLOAD")
      self.download.set_value("update available")
    else:
      last = time_ago(self._store.get_text("LastUpdateTime"))
      self.download.set_button_text("CHECK")
      self.download.set_value(f"up to date, last checked {last}")
    self.download.set_enabled(True)

  def showEvent(self, event):
    super().showEvent(event)
    # Re-enabled on entry: it is disabled on press to stop double-taps, and
    # nothing else would ever turn it back on if the reboot did not happen.
    self.install.set_enabled(True)
    self.refresh()

  # ---- actions ----------------------------------------------------------

  def _on_download(self) -> None:
    self.download.set_enabled(False)
    checking = self.download.button.text() == "CHECK"
    signal_updater(signal.SIGUSR1 if checking else signal.SIGHUP)

  def _on_install(self) -> None:
    self.install.set_enabled(False)
    # The updater stages the new version and the reboot swaps to it; there is
    # no separate install step to run here.
    self._store.put_bool("DoReboot", True)

  def _on_select_branch(self) -> None:
    branches = order_branches(
      self._store.get_text("UpdaterAvailableBranches"),
      self._store.get_text("GitBranch"),
      self._store.get_text("UpdaterTargetBranch"),
    )
    if not branches:
      return
    current = self._store.get_text("UpdaterTargetBranch")
    chosen = selection_dialog("Select a branch", branches, current, self)
    if not chosen:
      return
    self._store.put_text("UpdaterTargetBranch", chosen)
    self.target_branch.set_value(chosen)
    signal_updater(signal.SIGUSR1)

  def _on_uninstall(self) -> None:
    if confirm_dialog("Are you sure you want to uninstall?", "Uninstall", self):
      self._store.put_bool("DoUninstall", True)


def _as_int(text: str) -> int:
  try:
    return int(text)
  except (TypeError, ValueError):
    return 0
