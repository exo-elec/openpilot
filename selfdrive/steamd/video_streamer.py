#!/usr/bin/env python3
"""UDP unicast H264 video streamer for VR headset."""

import logging
import subprocess
import threading

import numpy as np

from openpilot.selfdrive.steamd.camera_client import StreamConfig
from openpilot.selfdrive.steamd.hud_renderer import HudRenderer
from openpilot.selfdrive.steamd.stereo_correction import StereoCorrection

logger = logging.getLogger("SteamD.video_streamer")


class UdpVideoStreamer:
  """FFmpeg-based H264 UDP unicast streamer (headset / monitor compatible)."""

  def __init__(self, cfg: StreamConfig, stereo: bool = True, corrector: StereoCorrection | None = None):
    self.cfg = cfg
    self.stereo = stereo
    self.corrector = corrector
    self._process: subprocess.Popen | None = None
    self._running = False
    self._thread: threading.Thread | None = None
    self._hud = HudRenderer()
    self._pip_source = "wide"  # "wide" or "tele"

  def set_view_mode(self, mode: str):
    if mode in ("wide", "rear", "left", "right", "road", "stereo"):
      self._hud.set_view_mode(mode)
      logger.info(f"UdpVideoStreamer: view mode → {mode}")

  def set_assist(self, active: bool):
    self._hud.set_assist(active)
    logger.info(f"UdpVideoStreamer: assist overlay → {active}")

  def set_pip_source(self, source: str):
    if source in ("wide", "tele"):
      self._pip_source = source

  def set_telemetry(self, data: dict):
    self._hud.set_telemetry(data)

  def start(self) -> bool:
    w = self.cfg.output_width
    h = self.cfg.output_height
    out_w = w * 2 if self.stereo else w
    out_h = h

    if not self.cfg.target_addr:
      logger.error("UdpVideoStreamer: target_addr not set — cannot start")
      return False

    cmd = [
      "ffmpeg",
      "-y", "-hide_banner", "-loglevel", "error",
      "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "bgr24",
      "-s", f"{out_w}x{out_h}", "-r", str(self.cfg.fps),
      "-thread_queue_size", "512",
      "-i", "-",
      "-an",
      "-c:v", self.cfg.encoder,
      "-preset", self.cfg.preset,
      "-tune", self.cfg.tune,
      "-b:v", f"{self.cfg.bitrate_kbps}k",
      "-maxrate", f"{int(self.cfg.bitrate_kbps * 1.5)}k",
      "-bufsize", f"{self.cfg.bitrate_kbps * 2}k",
      "-g", str(self.cfg.gop),
      "-pix_fmt", "yuv420p",
      "-fflags", "nobuffer",
      "-flags", "low_delay",
      "-f", "mpegts",
      f"udp://{self.cfg.target_addr}:{self.cfg.target_port}?pkt_size=1316&buffer_size=65535",
    ]
    try:
      self._process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
      )
      logger.info(f"UdpVideoStreamer: FFmpeg started → udp://{self.cfg.target_addr}:{self.cfg.target_port}")
      return True
    except Exception as e:
      logger.error(f"UdpVideoStreamer: FFmpeg start failed: {e}")
      return False

  def write(self, frame: np.ndarray) -> bool:
    if self._process is None or self._process.stdin is None:
      return False
    try:
      self._process.stdin.write(frame.tobytes())
      return True
    except BrokenPipeError:
      logger.warning("UdpVideoStreamer: FFmpeg pipe broken")
      return False
    except Exception as e:
      logger.error(f"UdpVideoStreamer: write error: {e}")
      return False

  def stop(self):
    if self._process:
      try:
        if self._process.stdin:
          self._process.stdin.close()
        self._process.wait(timeout=3.0)
      except Exception:
        self._process.kill()
      finally:
        self._process = None

  @property
  def alive(self) -> bool:
    return self._process is not None and self._process.poll() is None

  def run_in_thread(self, camera_client, corrector: StereoCorrection | None = None):
    """Run streaming loop in a background thread."""
    self._running = True
    self._thread = threading.Thread(target=self._stream_loop, args=(camera_client, corrector), daemon=True)
    self._thread.start()

  def _stream_loop(self, camera_client, corrector: StereoCorrection | None = None):
    if not self.start():
      return
    frames_streamed = 0
    dropped_frames = 0
    try:
      while self._running:
        main_left, main_right = camera_client.recv(self._hud.view_mode)
        # PiP: prefer tele where available, fallback to wide (ExoPilot 01M has no tele camera)
        pip_left, pip_right = camera_client.recv(self._pip_source)
        if pip_left is None and pip_right is None and self._pip_source == "tele":
          pip_left, pip_right = camera_client.recv("wide")
        pip = pip_left if pip_left is not None else pip_right

        if main_left is None and main_right is None:
          dropped_frames += 1
          continue

        composed = self._compose_frame(main_left, main_right, pip, corrector)
        if self.write(composed):
          frames_streamed += 1
        else:
          dropped_frames += 1
          if not self.alive:
            logger.warning("UdpVideoStreamer: encoder died, restarting")
            self.stop()
            if not self.start():
              break
    except Exception:
      logger.exception("UdpVideoStreamer stream loop error")
    finally:
      self.stop()
      camera_client.close()
      logger.info(f"UdpVideoStreamer stopped: {frames_streamed} frames, {dropped_frames} dropped")

  def _compose_frame(self, left, right, pip, corrector: StereoCorrection | None = None):
    w = self.cfg.output_width
    h = self.cfg.output_height
    stereo = left is not None and right is not None

    if stereo:
      if corrector is not None and (corrector.has_calibration or corrector.scale_factor < 0.99):
        try:
          left, right = corrector.correct(left, right)
        except Exception as e:
          logger.debug(f"UdpVideoStreamer: correction error: {e}")
      left_rs = self._resize(left, w, h)
      right_rs = self._resize(right, w, h)
      frame = np.hstack([left_rs, right_rs])
    elif left is not None:
      left_rs = self._resize(left, w, h)
      frame = np.hstack([left_rs, left_rs])
    else:
      frame = np.zeros((h, w * 2, 3), dtype=np.uint8)

    if pip is not None:
      frame = self._hud.overlay_pip(frame, pip)

    if self._hud.assist_active and stereo:
      frame = self._hud.overlay_assist(frame, left, right, w, h)

    frame = self._hud.render(frame)
    return frame

  @staticmethod
  def _resize(frame: np.ndarray, w: int, h: int) -> np.ndarray:
    if frame.shape[1] == w and frame.shape[0] == h:
      return frame
    try:
      import cv2
      return cv2.resize(frame, (w, h), interpolation=cv2.INTER_LINEAR)
    except Exception:
      from PIL import Image
      return np.array(Image.fromarray(frame).resize((w, h), Image.BILINEAR))

  def shutdown(self):
    self._running = False
    if self._thread:
      self._thread.join(timeout=2.0)
    self.stop()
