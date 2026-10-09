#!/usr/bin/env python3
"""
Uploader stub — EOP: No cloud uploads.

Logs remain on device only. This module is kept for compatibility
but does nothing.
"""
import threading
import time

from openpilot.common.swaglog import cloudlog


def main(exit_event: threading.Event = None) -> None:
  if exit_event is None:
    exit_event = threading.Event()

  cloudlog.info("uploader: EOP offline mode — no cloud uploads")

  while not exit_event.is_set():
    time.sleep(60)


if __name__ == "__main__":
  main()
