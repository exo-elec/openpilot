#!/usr/bin/env python3
"""EOP10 / 01M / 02M entry point: the shared pathd with a MonoTrackFeed.

EOP10's RKNN monod publishes positions only (vx, vy, sigma left at 0), so the feed adds Kalman velocities.
Registered under the name `pathadjustd` (never `pathd`, which is EOP10's own daemon) behind `ngp_pathd_enabled`.
"""
from nagaspilot.runtime.mono_track_feed import MonoTrackFeed
from nagaspilot.runtime.pathd import run


def main():
  run(MonoTrackFeed())


if __name__ == "__main__":
  main()
