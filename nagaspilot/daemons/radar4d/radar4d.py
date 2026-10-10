#!/usr/bin/env python3
"""Canonical surround radar4d producer on all Exopilot boards.

BLE radarCornerTracks provides tracked 3D objects without point-cloud shapes.
Optional RK3576 WiFi supplies raw points alongside these tracks. One producer
owns radar4d and its flattened radar2d BSD compatibility view. Forward UART
radar3d remains separate. Confirmed corner poses are required for placement.
"""

import time

import cereal.messaging as messaging
from openpilot.common.realtime import DT_MDL, Priority, Ratekeeper, config_realtime_process
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.swaglog import cloudlog
from openpilot.nagaspilot.daemons.radar4d.radar4d_points import CornerCloudCache
from openpilot.selfdrive.controls.radar_corner_geometry import load_corner_poses
from openpilot.system.hardware import HARDWARE
from nagaspilot.controls.ngp_radar2d import ground_range
from openpilot.nagaspilot.daemons.radar4d.surround_tracks import surround_tracks

_radar_hal = HARDWARE.hal_import("drivers.radar")   # exopilot's hal, through the one seam
HAL_AVAILABLE = _radar_hal is not None and hasattr(_radar_hal, "RadarCornerReceiver")
RadarCornerReceiver = getattr(_radar_hal, "RadarCornerReceiver", None)

FRAME_RATE_HZ = 20
IDLE_POLL_S = 30.0
RECV_TIMEOUT_S = 0.01       # recv_all() blocks at most this; the Ratekeeper paces
POSE_RELOAD_S = 1.0
POSE_WARN_S = 30.0


class Radar4DD:
  def __init__(self):
    self.pm = messaging.PubMaster(['radar4d', 'radar2d'])
    self.sm = messaging.SubMaster(['carState', 'radarCornerTracks'])
    self.receiver = None
    self.running = False
    self.cache = CornerCloudCache()
    self._poses = None
    self._pose_t = -POSE_RELOAD_S
    self._pose_warn_t = -POSE_WARN_S

    if not HAL_AVAILABLE or HARDWARE.get_device_type() != "rk3576":
      cloudlog.info("radar4d: hal package not installed -- WiFi point cloud unavailable; BLE surround remains active. " +
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

  def _publish(self, points, fresh: bool, tracks=(), wifi_corners=()) -> None:
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
      o.corner = p.corner
      o.source = 2
    msg.radar4d.objects = list(tracks)
    msg.radar4d.bleCorners = sorted({t['corner'] for t in tracks})
    msg.radar4d.wifiCorners = list(wifi_corners)
    msg.valid = fresh
    self.pm.send('radar4d', msg)

  def _publish_planar(self, raw, now):
    msg = messaging.new_message('radar2d')
    if raw is not None:
      msg.radar2d = raw.as_builder()
      for obj in msg.radar2d.objects:
        obj.rangM = ground_range(obj.rangM, obj.elevationDeg)
        obj.elevationDeg = 0.0
    else:
      msg.radar2d.init('returns', 4)
      for side, entry in enumerate(msg.radar2d.returns):
        entry.side = side
        entry.vRel = float('nan')
    car_fresh = self.sm.valid['carState'] and now - self.sm.recv_time['carState'] <= 0.25
    if car_fresh:
      for entry in msg.radar2d.returns:
        presence = self.sm['carState'].leftBlindspot if entry.side < 2 else self.sm['carState'].rightBlindspot
        entry.present = entry.present or presence
    msg.valid = raw is not None or car_fresh
    self.pm.send('radar2d', msg)

  def run(self):
    self.running = True
    rk = Ratekeeper(FRAME_RATE_HZ, print_delay_threshold=None)
    while self.running:
      self.sm.update(0)
      now = time.monotonic()
      self._refresh_poses(now)
      try:
        if self.receiver is not None:
          self.cache.update(self.receiver.recv_all(), now)
      except OSError as e:
        cloudlog.warning(f"radar4d: UDP receive failed: {e}")
      v_ego = float(self.sm['carState'].vEgo) if self.sm.valid['carState'] else 0.0
      points = self.cache.points(self._poses, v_ego, now)
      ble_fresh = self.sm.valid['radarCornerTracks'] and now - self.sm.recv_time['radarCornerTracks'] <= 0.25
      raw = self.sm['radarCornerTracks'] if ble_fresh else None
      tracks = surround_tracks(raw.objects, self._poses) if raw is not None else []
      wifi_corners = self.cache.fresh_corners(now)
      self._publish(points, fresh=ble_fresh or bool(wifi_corners), tracks=tracks, wifi_corners=wifi_corners)
      self._publish_planar(raw, now)
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
