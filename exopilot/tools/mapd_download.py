#!/usr/bin/env python3
"""Download offline map tiles with mapd (third_party/mapd), then drive without a connection.

  exopilot/tools/mapd_download.py nation.KR                  # one country
  exopilot/tools/mapd_download.py us_state.OH,us_state.WV    # several areas (mapd's download menu paths)
  exopilot/tools/mapd_download.py --cancel

Needs mapd running (MapdEnabled) and a network connection for the download itself. Area keys are the ones in mapd's
download menu (nation.<ISO code>, us_state.<code>); progress comes from mapdExtendedOut.
"""
import argparse
import time

import cereal.messaging as messaging
from cereal import custom


def request(pm: messaging.PubMaster, kind, areas: str = "") -> None:
  msg = messaging.new_message('mapdIn')
  msg.mapdIn.type = kind
  msg.mapdIn.str = areas
  pm.send('mapdIn', msg)


def main() -> None:
  ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument('areas', nargs='?', help="comma-separated download menu paths, e.g. nation.KR")
  ap.add_argument('--cancel', action='store_true', help="stop the download in progress")
  ap.add_argument('--timeout', type=float, default=3600.0, help="give up waiting after this many seconds")
  args = ap.parse_args()
  if not args.cancel and not args.areas:
    ap.error("give the areas to download, or --cancel")

  pm = messaging.PubMaster(['mapdIn'])
  sm = messaging.SubMaster(['mapdExtendedOut'])
  time.sleep(0.5)  # let the subscriber connect before the one-shot message
  if args.cancel:
    request(pm, custom.MapdInputType.cancelDownload)
    print("cancel sent")
    return

  request(pm, custom.MapdInputType.download, args.areas)
  print(f"download requested: {args.areas}")
  started = False
  t0 = time.monotonic()
  while time.monotonic() - t0 < args.timeout:
    sm.update(1000)
    if not sm.updated['mapdExtendedOut']:
      continue
    p = sm['mapdExtendedOut'].downloadProgress
    if p.active:
      started = True
      print(f"\r{p.downloadedFiles}/{p.totalFiles} files", end="", flush=True)
    elif started:
      print("\ncancelled" if p.cancelled else "\ndone")
      return
  print("\ntimed out waiting for mapd (is MapdEnabled set and mapd running?)")


if __name__ == "__main__":
  main()
