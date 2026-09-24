#!/usr/bin/env python3
"""
radar4d — ESP32 corner-radar WiFi point-cloud producer (dev/02M only)

Publishes Custom.Radar4D to cereal 'radar4d' at 20Hz, consumed by
selfdrive/gridd/gridd.py (_fuse_radar4d). 02M is the only hardware with the
antenna for the corner-node WiFi AP; the corner nodes' critical link is BLE
(radar2d via bluetoothd), this point cloud is an add-on on top of it.

Source: 4 ESP32_RADAR dev/v2 nodes (ESP32-S3 + Calterah CAL77S244), each
forwarding its radar's Radar4D frames over UDP 47000 in chunks.
hal.drivers.radar.radar4d.RadarCornerReceiver reassembles and decodes them
(exopilot `hal` package: dev PC `pip3 install -e ../exopilot/hal`; on-device
the first-boot setup script installs it). If `hal` is not importable or the
port cannot be bound, the daemon idles (logged once), same convention as
radar3d.py.

Each corner's points are placed in the vehicle frame with the confirmed
corner-pose registry (radar_corner_geometry.load_corner_poses(), same as
gridd's BLE Radar2D path); a corner without a confirmed pose, or with an
unresolved strap (0xFF), is not published. Points are raw radar detections,
not tracks: trackId and existenceProb are 0. isStatic/dynProp come from the
ego-speed Doppler check in lib/radar4d_points.py (carState.vEgo).
"""

import time

import cereal.messaging as messaging
from openpilot.common.realtime import DT_MDL, Priority, Ratekeeper, config_realtime_process
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.controls.lib.radar4d_points import CornerCloudCache
from openpilot.selfdrive.controls.radar_corner_geometry import load_corner_poses

try:
  from hal.drivers.radar import RadarCornerReceiver
  HAL_AVAILABLE = True
except ImportError:
  HAL_AVAILABLE = False

FRAME_RATE_HZ = 20
IDLE_POLL_S = 30.0
RECV_TIMEOUT_S = 0.01       # recv_all() blocks at most this; the Ratekeeper paces
POSE_RELOAD_S = 1.0
POSE_WARN_S = 30.0


class Radar4DD:
  def __init__(self):
    self.pm = messaging.PubMaster(['radar4d'])
    self.sm = messaging.SubMaster(['carState'])
    self.receiver = None
    self.running = False
    self.cache = CornerCloudCache()
    self._poses = None
    self._pose_t = -POSE_RELOAD_S
    self._pose_warn_t = -POSE_WARN_S

    if not HAL_AVAILABLE:
      cloudlog.error("radar4d: hal package not installed -- no corner point cloud. " +
                     "Dev PC: pip3 install -e ../exopilot/hal.")
      return
    try:
      self.receiver = RadarCornerReceiver(timeout_s=RECV_TIMEOUT_S)
      self.receiver.open()
    except OSError as e:
      cloudlog.error(f"radar4d: cannot bind UDP {getattr(self.receiver, 'port', '?')}: {e}")
      self.receiver = None

  def _refresh_poses(self, now: float) -> None:
    if now - self._pose_t < POSE_RELOAD_S:
      return
    self._pose_t = now
    self._poses = load_corner_poses(require_confirmed=True)
    if self._poses is None and now - self._pose_warn_t >= POSE_WARN_S:
      cloudlog.warning("radar4d: waiting for a confirmed corner pose")
      self._pose_warn_t = now

  def _publish(self, points, fresh: bool) -> None:
    msg = messaging.new_message('radar4d')
    out = msg.radar4d.init('points', len(points))
    for i, p in enumerate(points):
      o = out[i]
      o.trackId = 0
      o.rangM = p.range_m
      o.azimuth = p.azimuth_deg
      o.elevation = p.elevation_deg
      o.vRel = p.v_rel
      o.snrDb = p.snr_db
      o.existenceProb = 0.0
      o.isStatic = p.is_static
      o.dynProp = 0 if p.is_static else 1
      o.aRel = float('nan')
    msg.valid = fresh
    self.pm.send('radar4d', msg)

  def run(self):
    self.running = True
    if self.receiver is None:
      while self.running:
        time.sleep(IDLE_POLL_S)
      return

    rk = Ratekeeper(FRAME_RATE_HZ, print_delay_threshold=None)
    while self.running:
      self.sm.update(0)
      now = time.monotonic()
      self._refresh_poses(now)
      try:
        self.cache.update(self.receiver.recv_all(), now)
      except OSError as e:
        cloudlog.warning(f"radar4d: UDP receive failed: {e}")
      v_ego = float(self.sm['carState'].vEgo) if self.sm.valid['carState'] else 0.0
      points = self.cache.points(self._poses, v_ego, now)
      self._publish(points, fresh=bool(self.cache.fresh_corners(now)))
      rk.keep_time()

  def stop(self):
    self.running = False
    if self.receiver is not None:
      self.receiver.close()


def main():
  set_daemon_affinity("radar4d")
  config_realtime_process(DT_MDL, Priority.CTRL_LOW)
  daemon = Radar4DD()
  try:
    daemon.run()
  except KeyboardInterrupt:
    daemon.stop()


if __name__ == '__main__':
  main()
