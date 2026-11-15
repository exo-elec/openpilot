"""GPS on the EOP boards: pigeond reads the u-blox receiver (ubloxRaw), ubloxd parses it into gpsLocationExternal.

Without ubloxd nothing publishes a position: mapd, navd, surfaced, coordinationd, steamd and the UI all read it.
"""


def test_ubloxd_runs_whenever_pigeond_does():
  from openpilot.system.manager.manager import managed_processes
  pigeond, ubloxd = managed_processes['pigeond'], managed_processes['ubloxd']
  assert ubloxd.cwd == "system/ubloxd" and ubloxd.cmdline == ["./ubloxd"]
  for started in (False, True):
    assert ubloxd.should_run(started, None, None) == pigeond.should_run(started, None, None)
