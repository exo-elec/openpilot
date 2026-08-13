#!/usr/bin/env python3
"""
SteamD — Single Source of External Vehicle Control

Unified teleoperation daemon:
  - UDP VR streaming (unicast H264 to headset)
  - UDP teleoperation input (Pico / Quest / OpenArm compatible)
  - Local joystick / keyboard debug input
  - External control arbitration with local-driver override
  - Direct carControl publishing

Replaces: teleoprtc + webrtcd + bodyteleop + joystickd + vr_streamd + vr_teleop
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import subprocess
import time
from pathlib import Path

from aiohttp import web

import cereal.messaging as messaging

from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.basedir import BASEDIR

from openpilot.selfdrive.steamd.config import SteamDConfig
from openpilot.selfdrive.steamd.camera_client import StreamConfig, MultiCameraClient
from openpilot.selfdrive.steamd.video_streamer import UdpVideoStreamer
from openpilot.selfdrive.steamd.inputs import JoystickInput, KeyboardInput, UdpInput, ControlCommand
from openpilot.selfdrive.steamd.arbiter import ControlArbiter
from openpilot.selfdrive.steamd.publisher import CarControlPublisher
from openpilot.selfdrive.steamd.audit import AuditLog
from openpilot.selfdrive.steamd.geofence import Geofence
from openpilot.selfdrive.steamd.stereo_correction import StereoCorrection

logger = logging.getLogger("SteamD")


class SteamD:
  """SteamD main daemon."""

  def __init__(self, config: SteamDConfig | None = None):
    self.config = config or SteamDConfig()
    self.params = Params()

    # Control pipeline
    joystick_enabled = self.config.enable_joystick_input and self.params.get_bool("SteamDJoystickInput")
    self.joystick_input = JoystickInput() if joystick_enabled else None
    self.keyboard_input = KeyboardInput() if self.config.enable_keyboard_input else None
    self.udp_input = UdpInput(
      listen_addr=self.config.udp_listen_addr,
      listen_port=self.config.udp_listen_port,
    ) if self.config.enable_udp_input else None
    self.arbiter = ControlArbiter()
    self.publisher = CarControlPublisher(self.config)
    self.audit = AuditLog()

    # UDP video streamer
    self.udp_streamer: UdpVideoStreamer | None = None
    self._udp_camera_client: MultiCameraClient | None = None

    # SubMaster for carState (local override detection) + GPS (geofence)
    self.sm = messaging.SubMaster(["carState", "gpsLocationExternal"])

    # Geofence
    self.geofence = Geofence(self.params.get("SteamDGeofencePolygon", b"").decode())

    # Web server (status page only)
    self.app: web.Application | None = None
    self.runner: web.AppRunner | None = None

    # Auth
    self._auth_token = self._load_auth_token()

    # State
    self._shutdown = False
    self.rk = Ratekeeper(100, print_delay_threshold=None)

  # ------------------------------------------------------------------ #
  # Lifecycle
  # ------------------------------------------------------------------ #

  async def initialize(self):
    logger.info("Initializing SteamD...")
    await self._init_web_server()
    logger.info("SteamD initialized")

  async def _init_web_server(self):
    self.app = web.Application()
    self.app.router.add_get("/", self._handle_index)
    self.app.router.add_get("/ping", self._handle_ping)
    self.app.router.add_get("/status", self._handle_status)
    self.app.router.add_post("/control", self._handle_http_control)
    self.app.router.add_static("/static", Path(__file__).parent / "static")

    self.runner = web.AppRunner(self.app)
    await self.runner.setup()

    site = web.TCPSite(
      self.runner,
      self.config.web_host,
      self.config.web_port,
      ssl_context=self._ssl_context() if self.config.use_ssl else None,
    )
    await site.start()
    proto = "https" if self.config.use_ssl else "http"
    logger.info(f"Web server on {proto}://{self.config.web_host}:{self.config.web_port}")

  def _load_auth_token(self) -> str:
    token = self.params.get("SteamDAuthToken")
    if token:
      return token.decode() if isinstance(token, bytes) else token
    import secrets
    token = secrets.token_urlsafe(32)
    self.params.put("SteamDAuthToken", token.encode())
    logger.info("Generated new SteamD auth token (check params to share with client)")
    return token

  def _check_auth(self, request: web.Request, payload: dict | None = None) -> bool:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
      return auth_header[7:] == self._auth_token
    if payload is not None and payload.get("token") == self._auth_token:
      return True
    return False

  def _ssl_context(self) -> ssl.SSLContext:
    cert_dir = Path(BASEDIR) / "selfdrive" / "steamd" / "certs"
    cert_dir.mkdir(exist_ok=True)
    cert_path = cert_dir / "cert.pem"
    key_path = cert_dir / "key.pem"

    if not cert_path.exists() or not key_path.exists():
      logger.info("Generating SSL certificate...")
      san = "DNS:localhost"
      try:
        import socket
        hostname = socket.gethostname()
        local_ip = socket.getaddrinfo(hostname, None, socket.AF_INET)[0][4][0]
        san = f"DNS:localhost,IP:127.0.0.1,IP:{local_ip}"
      except Exception:
        pass
      subprocess.run(
        [
          "openssl", "req", "-x509", "-newkey", "rsa:4096",
          "-nodes", "-out", str(cert_path), "-keyout", str(key_path),
          "-days", "365",
          "-subj", "/C=US/ST=California/O=openpilot/OU=steamd",
          "-addext", f"subjectAltName={san}",
        ],
        check=True,
        capture_output=True,
      )

    ctx = ssl.SSLContext(protocol=ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_path), str(key_path))
    return ctx

  # ------------------------------------------------------------------ #
  # HTTP handlers
  # ------------------------------------------------------------------ #

  async def _handle_ping(self, request: web.Request) -> web.Response:
    return web.Response(text="pong")

  async def _handle_status(self, request: web.Request) -> web.Response:
    """Return current SteamD status for web dashboard polling."""
    cs = self.sm["carState"] if self.sm else None
    udp_last = self.udp_input.last_recv_time if self.udp_input else 0.0
    status = {
      "steamd_connected": True,
      "udp_input_alive": (time.monotonic() - udp_last) < self.config.control_timeout_sec if self.udp_input else False,
      "udp_last_ms": int((time.monotonic() - udp_last) * 1000) if self.udp_input else -1,
      "engaged": getattr(self.udp_input, "_engaged", False) if self.udp_input else False,
      "vehicle_engaged": getattr(self.arbiter, "_engaged", False),
      "v_ego_ms": round(getattr(cs, "vEgo", 0.0), 2) if cs else 0.0,
      "steering_angle_deg": round(getattr(cs, "steeringAngleDeg", 0.0), 1) if cs else 0.0,
      "gear": str(getattr(cs, "gearShifter", "N")) if cs else "N",
      "gas": round(getattr(cs, "gas", 0.0), 3) if cs else 0.0,
      "brake": round(getattr(cs, "brake", 0.0), 3) if cs else 0.0,
      "left_blinker": bool(getattr(cs, "leftBlinker", False)) if cs else False,
      "right_blinker": bool(getattr(cs, "rightBlinker", False)) if cs else False,
      "view_mode": getattr(self.udp_streamer, "_hud", None) and getattr(self.udp_streamer._hud, "view_mode", "road") if self.udp_streamer else "road",
      "video_streaming": self.udp_streamer is not None,
      "stream_target": f"{self.config.udp_stream_target_addr}:{self.config.udp_stream_target_port}" if self.config.udp_stream_target_addr else None,
    }
    return web.json_response(status)

  async def _handle_http_control(self, request: web.Request) -> web.Response:
    try:
      payload = await request.json()
    except Exception:
      return web.json_response({"error": "Invalid JSON"}, status=400)
    if not self._check_auth(request, payload):
      return web.json_response({"error": "Unauthorized"}, status=401)
    try:
      self._on_http_control(payload)
      return web.json_response({"status": "ok"})
    except Exception:
      logger.exception("HTTP control error")
      return web.json_response({"error": "HTTP control failed"}, status=500)

  def _on_http_control(self, data: dict):
    """Callback for HTTP control messages (emergency stop, etc.)."""
    if data.get("disengage"):
      self.audit.log_session("disengage", details="remote disengage via HTTP")
      self.arbiter.force_kill("http_disengage")

  # ------------------------------------------------------------------ #
  # Control loop
  # ------------------------------------------------------------------ #

  async def _sleep_remaining(self, loop_start: float, interval: float = 0.01):
    elapsed = time.monotonic() - loop_start
    sleep_for = max(0.0, interval - elapsed)
    await asyncio.sleep(sleep_for)

  async def control_loop(self):
    logger.info("Control loop started")
    while not self._shutdown:
      loop_start = time.monotonic()
      self.sm.update(0)
      cs = self.sm["carState"]

      # 1. Local override always wins
      if self.arbiter.check_local_override(cs):
        self.audit.log_override(self.arbiter._override_reason or "unknown", v_ego=cs.vEgo)
        self.publisher.drop_pubmaster()
        await self._sleep_remaining(loop_start)
        continue

      # Ignition safety gate
      ignition_on = getattr(cs, "ignition", False) or self.params.get_bool("EOPIgnitionOn")
      if not ignition_on:
        self.publisher.drop_pubmaster()
        await self._sleep_remaining(loop_start)
        continue

      # Geofence gate
      if self.geofence.enabled:
        gps = self.sm["gpsLocationExternal"]
        lat = getattr(gps, "latitude", None)
        lon = getattr(gps, "longitude", None)
        allowed, reason = self.geofence.check_position(lat, lon)
        if not allowed:
          self.audit.log_override(f"geofence: {reason}")
          self.publisher.drop_pubmaster()
          self.arbiter.force_kill("geofence")
          await self._sleep_remaining(loop_start)
          continue

      # 2. Check if we are authorized to publish
      if not self.arbiter.may_publish():
        self.publisher.drop_pubmaster()
        await self._sleep_remaining(loop_start)
        continue

      # 3. Gather external commands
      cmd: ControlCommand | None = None

      if self.joystick_input:
        joy_cmd = self.joystick_input.poll()
        if joy_cmd:
          cmd = joy_cmd
          self.arbiter.on_command(cmd)

      if self.keyboard_input:
        kb_cmd = self.keyboard_input.poll()
        if kb_cmd:
          cmd = kb_cmd
          self.arbiter.on_command(cmd)

      if self.udp_input:
        udp_cmd = self.udp_input.poll(
          steer_source=self.config.openarmx_steer_source,
          max_roll_deg=self.config.openarmx_max_roll_deg,
          max_yaw_deg=self.config.openarmx_max_yaw_deg,
        )
        if udp_cmd:
          cmd = udp_cmd
          self.arbiter.on_command(cmd)
          if self.udp_streamer:
            if cmd.view_mode:
              self.udp_streamer.set_view_mode(cmd.view_mode)
              # Toggle PiP: tele when in road/wide view, wide when in tele view
              if cmd.view_mode == "tele":
                self.udp_streamer.set_pip_source("wide")
              else:
                self.udp_streamer.set_pip_source("tele")
            self.udp_streamer.set_assist(getattr(cmd, "assist", False))

      # Racing-game telemetry overlay (update every cycle)
      if self.udp_streamer:
        cs = self.sm["carState"]
        v_ego = getattr(cs, "vEgo", 0.0)
        yaw_rate = getattr(cs, "yawRate", 0.0)
        lat_accel_approx = v_ego * yaw_rate
        self.udp_streamer.set_telemetry({
          "vEgo": v_ego,
          "steeringAngleDeg": getattr(cs, "steeringAngleDeg", 0.0),
          "gear": str(getattr(cs, "gearShifter", "N")),
          "gas": float(getattr(cs, "gas", 0.0)),
          "brake": float(getattr(cs, "brake", 0.0)),
          "engaged": getattr(cmd, "engage", False) if cmd else False,
          "latAccel": lat_accel_approx,
          "longAccel": getattr(cs, "aEgo", 0.0),
          "battery": float(getattr(cs, "fuelGauge", 0.0)),
          "charging": bool(getattr(cs, "charging", False)),
          "regenBraking": bool(getattr(cs, "regenBraking", False)),
          "leftBlinker": bool(getattr(cs, "leftBlinker", False)),
          "rightBlinker": bool(getattr(cs, "rightBlinker", False)),
          "leftBlindspot": bool(getattr(cs, "leftBlindspot", False)),
          "rightBlindspot": bool(getattr(cs, "rightBlindspot", False)),
        })

      # 4. Evaluate link loss
      link_killed, link_elapsed_ms = self.arbiter.process_link_loss(self.config.control_timeout_sec)
      if link_killed:
        self.audit.log_link_loss(elapsed_ms=link_elapsed_ms, v_ego=cs.vEgo)
        if abs(cs.vEgo) > 0.5:
          self.publisher.send_safe_stop(2000.0)
        else:
          self.publisher.drop_pubmaster()
          self.arbiter.force_kill("link_loss")
        await self._sleep_remaining(loop_start)
        continue
      if link_elapsed_ms > 0:
        self.audit.log_link_loss(elapsed_ms=link_elapsed_ms, v_ego=cs.vEgo)
        self.publisher.send_safe_stop(link_elapsed_ms)
        await self._sleep_remaining(loop_start)
        continue

      # 5. Build actuators
      if cmd is not None:
        if isinstance(cmd.accel, tuple):
          gas, brake = cmd.accel
          accel = gas * self.config.max_accel_mps2 - brake * self.config.max_decel_mps2
        else:
          accel = float(cmd.accel)

        accel = self.arbiter.safe_accel(accel, self.config.max_accel_mps2, self.config.max_decel_mps2)
        steer = max(-1.0, min(1.0, float(cmd.steer)))

        self.audit.log_command(
          source=cmd.source,
          steer=steer,
          accel=accel,
          gas=cmd.accel[0] if isinstance(cmd.accel, tuple) else None,
          brake=cmd.accel[1] if isinstance(cmd.accel, tuple) else None,
          engaged=True,
          v_ego=cs.vEgo,
        )
        self.publisher.send(steer=steer, accel=accel, enabled=True)
      else:
        self.publisher.send_zero()

      await self._sleep_remaining(loop_start)

  # ------------------------------------------------------------------ #
  # Main entry
  # ------------------------------------------------------------------ #

  async def run(self):
    await self.initialize()

    if self.config.enable_udp_stream and self.config.udp_stream_target_addr:
      self._start_udp_streamer()

    control_task = asyncio.create_task(self.control_loop())
    try:
      while not self._shutdown:
        await asyncio.sleep(1)
    except asyncio.CancelledError:
      pass
    finally:
      self._shutdown = True
      control_task.cancel()
      await self.shutdown()

  def _start_udp_streamer(self):
    try:
      self._udp_camera_client = MultiCameraClient()
      available = self._udp_camera_client.discover()
      logger.info(f"UDP streamer cameras: {available}")

      corrector = StereoCorrection()
      cfg = StreamConfig(
        output_width=self.config.udp_stream_width,
        output_height=self.config.udp_stream_height,
        fps=self.config.udp_stream_fps,
        bitrate_kbps=self.config.udp_stream_bitrate_kbps,
        target_addr=self.config.udp_stream_target_addr,
        target_port=self.config.udp_stream_target_port,
        encoder=self.config.udp_stream_encoder,
      )
      self.udp_streamer = UdpVideoStreamer(cfg, stereo=available.get("stereo", False), corrector=corrector)
      # Default PiP: tele if detected, else wide (ExoPilot 01M never detects tele)
      if available.get("tele", False):
        self.udp_streamer.set_pip_source("tele")
        logger.info("UDP streamer: tele camera detected — default PiP = tele")
      else:
        self.udp_streamer.set_pip_source("wide")
        logger.info("UDP streamer: no tele camera — default PiP = wide")
      self.udp_streamer.run_in_thread(self._udp_camera_client, corrector)
    except Exception:
      logger.exception("Failed to start UDP streamer")

  async def shutdown(self):
    logger.info("Shutting down SteamD...")
    self.publisher.drop_pubmaster()
    if self.udp_streamer:
      self.udp_streamer.shutdown()
    if self._udp_camera_client:
      self._udp_camera_client.close()
    if self.udp_input:
      self.udp_input.stop()
    if self.runner:
      await self.runner.cleanup()
    logger.info("SteamD shutdown complete")

  # ------------------------------------------------------------------ #
  # Index HTML (VR interface)
  # ------------------------------------------------------------------ #

  async def _handle_index(self, request: web.Request) -> web.Response:
    mode = request.query.get("mode", "auto")
    ua = request.headers.get("User-Agent", "").lower()
    is_vr = any(x in ua for x in ("oculus", "quest", "pico", "meta", "vr"))

    if mode == "vr" or (mode == "auto" and is_vr):
      template = "vr.html"
    else:
      template = "monitor.html"

    path = Path(__file__).parent / "templates" / template
    if not path.exists():
      return web.Response(status=404, text="Template not found")
    return web.Response(content_type="text/html", text=path.read_text())


async def main():
  logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
  steamd = SteamD(SteamDConfig(web_port=5000, enable_udp_input=True, enable_udp_stream=True))
  await steamd.run()


if __name__ == "__main__":
  asyncio.run(main())
