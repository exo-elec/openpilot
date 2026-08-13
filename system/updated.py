#!/usr/bin/env python3

# EOP safe-update daemon.
#
# Background service that waits for network access and checks for Git updates
# every 10 minutes while offroad. The update is staged in an OverlayFS-backed
# directory so the running BASEDIR is never modified. If an update succeeds, the
# finalized tree is swapped in at the next boot by launch_chffrplus.sh.
#
# This daemon is intentionally OS-agnostic: EOP10 runs on the SOM supplier's
# Ubuntu image, so there is no AGNOS/NEOS image-flashing path here. Only the
# Git-based application update flow is implemented.

import os
import datetime
import subprocess
import psutil
import shutil
import signal
import fcntl
import time
import threading
from pathlib import Path
from typing import List, Tuple, Optional

from openpilot.common.basedir import BASEDIR
from openpilot.common.compat import UTC
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.selfdrived.alertmanager import set_offroad_alert
from openpilot.system.hardware import HARDWARE, TICI

LOCK_FILE = os.getenv("UPDATER_LOCK_FILE", "/tmp/safe_staging_overlay.lock")
STAGING_ROOT = os.getenv("UPDATER_STAGING_ROOT", "/data/safe_staging")

OVERLAY_UPPER = os.path.join(STAGING_ROOT, "upper")
OVERLAY_METADATA = os.path.join(STAGING_ROOT, "metadata")
OVERLAY_MERGED = os.path.join(STAGING_ROOT, "merged")
FINALIZED = os.path.join(STAGING_ROOT, "finalized")

DAYS_NO_CONNECTIVITY_MAX = 14     # do not allow to engage after this many days
DAYS_NO_CONNECTIVITY_PROMPT = 10  # send an offroad prompt after this many days


class WaitTimeHelper:
  def __init__(self, proc):
    self.proc = proc
    self.ready_event = threading.Event()
    self.shutdown = False
    signal.signal(signal.SIGTERM, self.graceful_shutdown)
    signal.signal(signal.SIGINT, self.graceful_shutdown)
    signal.signal(signal.SIGHUP, self.update_now)

  def graceful_shutdown(self, signum: int, frame) -> None:
    cloudlog.info("caught SIGINT/SIGTERM, dismounting overlay at next opportunity")

    child_procs = self.proc.children(recursive=True)
    for p in child_procs:
      p.send_signal(signum)

    self.shutdown = True
    self.ready_event.set()

  def update_now(self, signum: int, frame) -> None:
    cloudlog.info("caught SIGHUP, running update check immediately")
    self.ready_event.set()

  def sleep(self, t: float) -> None:
    self.ready_event.wait(timeout=t)


def run(cmd: List[str], cwd: Optional[str] = None, low_priority: bool = False):
  if low_priority:
    cmd = ["nice", "-n", "19"] + cmd
  return subprocess.check_output(cmd, cwd=cwd, stderr=subprocess.STDOUT, encoding='utf8')


def set_consistent_flag(consistent: bool) -> None:
  os.sync()
  consistent_file = Path(os.path.join(FINALIZED, ".overlay_consistent"))
  if consistent:
    consistent_file.touch()
  elif not consistent:
    consistent_file.unlink(missing_ok=True)
  os.sync()


def set_params(new_version: bool, failed_count: int, exception: Optional[str]) -> None:
  params = Params()

  params.put("UpdateFailedCount", str(failed_count))

  last_update = datetime.datetime.now(UTC)
  if failed_count == 0:
    t = last_update.isoformat()
    params.put("LastUpdateTime", t.encode('utf8'))
  else:
    try:
      t = params.get("LastUpdateTime", encoding='utf8')
      last_update = datetime.datetime.fromisoformat(t)
    except (TypeError, ValueError):
      pass

  if exception is None:
    params.delete("LastUpdateException")
  else:
    params.put("LastUpdateException", exception)

  # Write out release notes for new versions
  if new_version:
    try:
      with open(os.path.join(FINALIZED, "RELEASES.md"), "rb") as f:
        r = f.read().split(b'\n\n', 1)[0]
      try:
        params.put("ReleaseNotes", r.decode("utf-8"))
      except Exception:
        params.put("ReleaseNotes", r + b"\n")
    except Exception:
      params.put("ReleaseNotes", "")
  params.put_bool("UpdateAvailable", new_version)

  # Handle user prompt
  for alert in ("Offroad_UpdateFailed", "Offroad_ConnectivityNeeded", "Offroad_ConnectivityNeededPrompt"):
    set_offroad_alert(alert, False)

  now = datetime.datetime.now(UTC)
  dt = now - last_update
  if failed_count > 15 and exception is not None:
    set_offroad_alert("Offroad_UpdateFailed", True, extra_text=exception)
  elif dt.days > DAYS_NO_CONNECTIVITY_MAX and failed_count > 1:
    set_offroad_alert("Offroad_ConnectivityNeeded", True)
  elif dt.days > DAYS_NO_CONNECTIVITY_PROMPT:
    remaining = max(DAYS_NO_CONNECTIVITY_MAX - dt.days, 1)
    set_offroad_alert("Offroad_ConnectivityNeededPrompt", True, extra_text=f"{remaining} day{'' if remaining == 1 else 's'}.")


def setup_git_options(cwd: str) -> None:
  git_cfg = [
    ("core.trustctime", "false"),
    ("core.checkStat", "minimal"),
  ]
  for option, value in git_cfg:
    run(["git", "config", option, value], cwd)


def dismount_overlay() -> None:
  if os.path.ismount(OVERLAY_MERGED):
    cloudlog.info("unmounting existing overlay")
    args = ["umount", "-l", OVERLAY_MERGED]
    if TICI:
      args = ["sudo"] + args
    run(args)


def init_overlay() -> None:
  overlay_init_file = Path(os.path.join(BASEDIR, ".overlay_init"))

  if overlay_init_file.is_file():
    git_dir_path = os.path.join(BASEDIR, ".git")
    new_files = run(["find", git_dir_path, "-newer", str(overlay_init_file)])
    if not len(new_files.splitlines()):
      return
    else:
      cloudlog.info(".git directory changed, recreating overlay")

  cloudlog.info("preparing new safe staging area")

  params = Params()
  params.put_bool("UpdateAvailable", False)
  set_consistent_flag(False)
  dismount_overlay()
  if TICI:
    run(["sudo", "rm", "-rf", STAGING_ROOT])
  if os.path.isdir(STAGING_ROOT):
    shutil.rmtree(STAGING_ROOT)

  for dirname in [STAGING_ROOT, OVERLAY_UPPER, OVERLAY_METADATA, OVERLAY_MERGED]:
    os.mkdir(dirname, 0o755)

  if os.lstat(BASEDIR).st_dev != os.lstat(OVERLAY_MERGED).st_dev:
    raise RuntimeError("base and overlay merge directories are on different filesystems; not valid for overlay FS!")

  consistent_file = Path(os.path.join(BASEDIR, ".overlay_consistent"))
  if consistent_file.is_file():
    consistent_file.unlink()
  overlay_init_file.touch()

  os.sync()
  overlay_opts = f"lowerdir={BASEDIR},upperdir={OVERLAY_UPPER},workdir={OVERLAY_METADATA}"

  mount_cmd = ["mount", "-t", "overlay", "-o", overlay_opts, "none", OVERLAY_MERGED]
  if TICI:
    run(["sudo"] + mount_cmd)
    run(["sudo", "chmod", "755", os.path.join(OVERLAY_METADATA, "work")])
  else:
    run(mount_cmd)

  git_diff = run(["git", "diff"], OVERLAY_MERGED, low_priority=True)
  params.put("GitDiff", git_diff)
  cloudlog.info(f"git diff output:\n{git_diff}")


def finalize_update() -> None:
  params = Params()
  params.put("UpdateStatus", "installing")
  cloudlog.info("creating finalized version of the overlay")
  set_consistent_flag(False)

  if os.path.exists(FINALIZED):
    shutil.rmtree(FINALIZED)
  shutil.copytree(OVERLAY_MERGED, FINALIZED, symlinks=True)

  run(["git", "reset", "--hard"], FINALIZED)
  run(["git", "submodule", "foreach", "--recursive", "git", "reset"], FINALIZED)

  set_consistent_flag(True)
  cloudlog.info("done finalizing overlay")


def check_git_fetch_result(fetch_txt: str) -> bool:
  err_msg = "Failed to add the host to the list of known hosts (/data/data/com.termux/files/home/.ssh/known_hosts).\n"
  return len(fetch_txt) > 0 and (fetch_txt != err_msg)


def check_for_update() -> Tuple[bool, bool]:
  setup_git_options(OVERLAY_MERGED)
  try:
    git_fetch_output = run(["git", "fetch", "--dry-run"], OVERLAY_MERGED, low_priority=True)
    return True, check_git_fetch_result(git_fetch_output)
  except subprocess.CalledProcessError:
    try:
      remote_url = subprocess.check_output(["git", "remote", "get-url", "origin"]).strip().decode('utf-8')
      subprocess.check_output(["git", "ls-remote", remote_url], stderr=subprocess.STDOUT)
      internet_connection = True
    except subprocess.CalledProcessError:
      internet_connection = False
    return internet_connection, False


def fetch_update(wait_helper: WaitTimeHelper) -> bool:
  params = Params()
  params.put("UpdateStatus", "checking")
  cloudlog.info("attempting git fetch inside staging overlay")

  setup_git_options(OVERLAY_MERGED)

  git_fetch_output = run(["git", "fetch"], OVERLAY_MERGED, low_priority=True)
  cloudlog.info("git fetch success: %s", git_fetch_output)

  cur_hash = run(["git", "rev-parse", "HEAD"], OVERLAY_MERGED).rstrip()
  upstream_hash = run(["git", "rev-parse", "@{u}"], OVERLAY_MERGED).rstrip()
  new_version = cur_hash != upstream_hash
  git_fetch_result = check_git_fetch_result(git_fetch_output)

  cloudlog.info(f"comparing {cur_hash} to {upstream_hash}")
  if new_version or git_fetch_result:
    cloudlog.info("Running update")

    if new_version:
      cloudlog.info("git reset in progress")
      r = [
        run(["git", "reset", "--hard", "@{u}"], OVERLAY_MERGED, low_priority=True),
        run(["git", "clean", "-xdf"], OVERLAY_MERGED, low_priority=True),
        run(["git", "submodule", "init"], OVERLAY_MERGED, low_priority=True),
        run(["git", "submodule", "update"], OVERLAY_MERGED, low_priority=True),
      ]
      cloudlog.info("git reset success: %s", '\n'.join(r))
      params.put("UpdateStatus", "prepareDownload")

    finalize_update()
    params.put("UpdateStatus", "success")
    cloudlog.info("EOP10 update successful!")
  else:
    params.put("UpdateStatus", "latest")
    cloudlog.info("nothing new from git at this time")

  return new_version


def check_git_saved() -> bool:
  try:
    if subprocess.check_output(['git', 'diff']) or subprocess.check_output(['git', 'diff', '--cached']):
      return False
    try:
      cherry_output = subprocess.check_output(['git', 'cherry', '-v'])
      if cherry_output:
        return False
    except subprocess.CalledProcessError:
      return True
    return True
  except subprocess.CalledProcessError:
    return False


MIN_DATE = datetime.datetime(year=2022, month=4, day=1)


def main() -> None:
  params = Params()

  if params.get_bool("DisableUpdates"):
    cloudlog.warning("updates are disabled by the DisableUpdates param")
    exit(0)

  ov_lock_fd = open(LOCK_FILE, 'w')
  try:
    fcntl.flock(ov_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
  except OSError as e:
    raise RuntimeError("couldn't get overlay lock; is another instance running?") from e

  proc = psutil.Process()
  if psutil.LINUX:
    proc.ionice(psutil.IOPRIO_CLASS_BE, value=7)

  while True:
    if datetime.datetime.today() > MIN_DATE:
      break
    time.sleep(1)

  if Path(os.path.join(STAGING_ROOT, "old_openpilot")).is_dir():
    cloudlog.event("update installed")

  if not params.get("InstallDate"):
    t = datetime.datetime.now(UTC).isoformat()
    params.put("InstallDate", t.encode('utf8'))

  overlay_init = Path(os.path.join(BASEDIR, ".overlay_init"))
  overlay_init.unlink(missing_ok=True)

  first_run = True
  last_fetch_time = 0.0
  update_failed_count = 0

  set_params(False, 0, None)

  wait_helper = WaitTimeHelper(proc)
  wait_helper.sleep(30)

  while not wait_helper.shutdown:
    update_now = wait_helper.ready_event.is_set()
    wait_helper.ready_event.clear()

    time_wrong = datetime.datetime.utcnow().year < 2022
    is_onroad = not params.get_bool("IsOffroad")
    if is_onroad or time_wrong:
      wait_helper.sleep(30)
      cloudlog.info("not running updater, not offroad")
      continue

    saved = check_git_saved()
    if not saved:
      params.put("UpdateStatus", "unsavedChanges" if time.monotonic() > 65 else "waiting")

    exception = None
    new_version = False
    update_failed_count += 1
    try:
      init_overlay()

      internet_ok, update_available = check_for_update()
      if internet_ok and not update_available:
        update_failed_count = 0

      if not internet_ok and saved:
        params.put("UpdateStatus", "noInternet")

      if saved and internet_ok and (update_now or time.monotonic() - last_fetch_time > 60*10):
        new_version = fetch_update(wait_helper)
        update_failed_count = 0
        last_fetch_time = time.monotonic()

        if first_run and not new_version and os.path.isdir(os.path.join(STAGING_ROOT, "neoupdate")):
          shutil.rmtree(os.path.join(STAGING_ROOT, "neoupdate"))
        first_run = False
    except subprocess.CalledProcessError as e:
      cloudlog.event(
        "update process failed",
        cmd=e.cmd,
        output=e.output,
        returncode=e.returncode
      )
      exception = f"command failed: {e.cmd}\n{e.output}"
      update_failed_count += 1
      overlay_init.unlink(missing_ok=True)
    except Exception as e:
      cloudlog.exception("uncaught updated exception, shouldn't happen")
      exception = str(e)
      overlay_init.unlink(missing_ok=True)

    try:
      if update_failed_count > 0 and internet_ok:
        params.put("UpdateStatus", "fetchFailed")
      set_params(new_version, update_failed_count, exception)
    except Exception:
      cloudlog.exception("uncaught updated exception while setting params, shouldn't happen")

    wait_helper.sleep(60 * 10)

  dismount_overlay()


if __name__ == "__main__":
  main()
