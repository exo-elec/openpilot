#!/usr/bin/env python3
"""Racing-game style HUD overlay renderer for VR video stream."""

import logging

import cv2
import numpy as np

logger = logging.getLogger("SteamD.hud")


class HudRenderer:
  """Draws telemetry HUD overlays on streamed frames."""

  def __init__(self):
    self._telemetry: dict = {}
    self.view_mode = "road"
    self.assist_active = False

  def set_telemetry(self, data: dict):
    self._telemetry = data

  def set_view_mode(self, mode: str):
    self.view_mode = mode

  def set_assist(self, active: bool):
    self.assist_active = active

  def render(self, frame: np.ndarray) -> np.ndarray:
    """Apply all HUD overlays to frame."""
    frame = self._overlay_telemetry(frame)
    return frame

  def overlay_pip(self, frame: np.ndarray, pip: np.ndarray) -> np.ndarray:
    """Overlay wide camera picture-in-picture in bottom-left corner."""
    try:
      fh, fw = frame.shape[:2]
      pip_w = int(fw * 0.25)
      pip_h = int(pip_w * pip.shape[0] / pip.shape[1])
      pip_h = min(pip_h, int(fh * 0.25))
      pip_rs = cv2.resize(pip, (pip_w, pip_h), interpolation=cv2.INTER_LINEAR)

      x0 = 8
      y0 = fh - pip_h - 8

      roi = frame[y0:y0+pip_h, x0:x0+pip_w]
      blended = cv2.addWeighted(roi, 0.3, pip_rs, 0.7, 0)
      frame[y0:y0+pip_h, x0:x0+pip_w] = blended

      cv2.rectangle(frame, (x0, y0), (x0 + pip_w, y0 + pip_h), (0, 255, 255), 2)
    except Exception as e:
      logger.debug(f"PiP overlay error: {e}")
    return frame

  def overlay_assist(self, frame: np.ndarray, left: np.ndarray, right: np.ndarray, w: int, h: int) -> np.ndarray:
    """Overlay a centered, aligned stereo crop for depth-verification assist."""
    try:
      crop_w = int(w * 0.30)
      crop_h = int(h * 0.30)
      cx = w // 2
      cy = h // 2

      left_crop = left[cy - crop_h//2 : cy + crop_h//2, cx - crop_w//2 : cx + crop_w//2]
      right_crop = right[cy - crop_h//2 : cy + crop_h//2, cx - crop_w//2 : cx + crop_w//2]

      if left_crop.size == 0 or right_crop.size == 0:
        return frame

      overlay_w = int(w * 0.35)
      overlay_h = int(h * 0.35)
      left_ov = cv2.resize(left_crop, (overlay_w, overlay_h), interpolation=cv2.INTER_LINEAR)
      right_ov = cv2.resize(right_crop, (overlay_w, overlay_h), interpolation=cv2.INTER_LINEAR)
      assist = np.hstack([left_ov, right_ov])

      out_h, out_w = frame.shape[:2]
      y0 = (out_h - assist.shape[0]) // 2
      x0 = (out_w - assist.shape[1]) // 2

      roi = frame[y0:y0+assist.shape[0], x0:x0+assist.shape[1]]
      blended = cv2.addWeighted(roi, 0.3, assist, 0.7, 0)
      frame[y0:y0+assist.shape[0], x0:x0+assist.shape[1]] = blended

      cv2.rectangle(frame, (x0, y0), (x0 + assist.shape[1], y0 + assist.shape[0]), (0, 255, 0), 2)
    except Exception as e:
      logger.debug(f"Assist overlay error: {e}")
    return frame

  def _overlay_telemetry(self, frame: np.ndarray) -> np.ndarray:
    """Racing-game style telemetry HUD."""
    try:
      t = self._telemetry
      fh, fw = frame.shape[:2]
      bar_h = 52
      pad = 10

      # Dark translucent bar at top
      overlay = frame.copy()
      cv2.rectangle(overlay, (0, 0), (fw, bar_h), (0, 0, 0), -1)
      frame = cv2.addWeighted(frame, 1.0, overlay, 0.6, 0)

      # --- Speed (big, left side) ---
      speed_ms = t.get("vEgo", 0.0)
      speed_kph = abs(speed_ms) * 3.6
      speed_text = f"{speed_kph:.0f}"
      cv2.putText(frame, speed_text, (pad, bar_h - 10), cv2.FONT_HERSHEY_DUPLEX, 1.6, (0, 255, 0), 2, cv2.LINE_AA)
      cv2.putText(frame, "km/h", (pad + 110, bar_h - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1, cv2.LINE_AA)

      # --- Gear (next to speed) ---
      gear = t.get("gear", "N")
      gear_color = (0, 255, 255) if gear in ("D", "S") else (0, 128, 255)
      cv2.putText(frame, str(gear), (pad + 170, bar_h - 10), cv2.FONT_HERSHEY_DUPLEX, 1.4, gear_color, 2, cv2.LINE_AA)

      # --- Status / Mode (center) ---
      engaged = t.get("engaged", False)
      status = "AUTO" if engaged else "MANUAL"
      status_color = (0, 255, 0) if engaged else (0, 128, 255)
      cv2.putText(frame, status, (fw // 2 - 60, bar_h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.9, status_color, 2, cv2.LINE_AA)

      # --- Blinkers (left/right of top bar) ---
      left_blink = t.get("leftBlinker", False)
      right_blink = t.get("rightBlinker", False)
      if left_blink:
        cv2.putText(frame, "◄", (pad + 260, bar_h - 10), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 200, 255), 2, cv2.LINE_AA)
      if right_blink:
        cv2.putText(frame, "►", (fw - 200, bar_h - 10), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 200, 255), 2, cv2.LINE_AA)

      # --- Speedometer arc + big speed (left-center, gaming style) ---
      speed_kph = abs(t.get("vEgo", 0.0)) * 3.6
      speed_max = 240.0
      speed_ratio = min(speed_kph / speed_max, 1.0)
      gauge_cx = pad + 160
      gauge_cy = bar_h + 80
      gauge_r = 70
      cv2.ellipse(frame, (gauge_cx, gauge_cy), (gauge_r, gauge_r), 180, 0, 180, (40, 40, 40), 6)
      end_angle = int(180 * speed_ratio)
      arc_color = (0, 255, 0) if speed_ratio < 0.5 else (0, 255, 255) if speed_ratio < 0.8 else (0, 0, 255)
      if end_angle > 0:
        cv2.ellipse(frame, (gauge_cx, gauge_cy), (gauge_r, gauge_r), 180, 0, end_angle, arc_color, 6)
      cv2.putText(frame, f"{speed_kph:.0f}", (gauge_cx - 55, gauge_cy + 15), cv2.FONT_HERSHEY_DUPLEX, 2.0, (255, 255, 255), 3, cv2.LINE_AA)
      cv2.putText(frame, "km/h", (gauge_cx - 25, gauge_cy + 35), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)

      # --- Big Gear (center, below status) ---
      gear = t.get("gear", "N")
      gear_color = (0, 255, 255) if gear in ("D", "S") else (0, 128, 255)
      cv2.putText(frame, str(gear), (fw // 2 - 25, bar_h + 55), cv2.FONT_HERSHEY_DUPLEX, 2.2, gear_color, 3, cv2.LINE_AA)

      # --- Battery gauge (right side, vertical like GT7) ---
      battery = t.get("battery", 0.0)
      battery_pct = int(battery * 100)
      bat_x = fw - 40
      bat_y = bar_h + 20
      bat_w = 16
      bat_h = 120
      cv2.rectangle(frame, (bat_x, bat_y), (bat_x + bat_w, bat_y + bat_h), (40, 40, 40), 2)
      bat_fill_h = int(bat_h * battery)
      bat_color = (0, 255, 0) if battery > 0.3 else (0, 200, 255) if battery > 0.15 else (0, 0, 255)
      if bat_fill_h > 0:
        cv2.rectangle(frame, (bat_x, bat_y + bat_h - bat_fill_h), (bat_x + bat_w, bat_y + bat_h), bat_color, -1)
      cv2.putText(frame, f"{battery_pct}", (bat_x - 8, bat_y + bat_h + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, bat_color, 1, cv2.LINE_AA)
      if t.get("charging", False):
        cv2.putText(frame, "⚡", (bat_x + 2, bat_y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1, cv2.LINE_AA)

      # --- Throttle / Brake bars (right of battery, vertical) ---
      gas = t.get("gas", 0.0)
      brake = t.get("brake", 0.0)
      regen = t.get("regenBraking", False)
      bar_x = fw - 14
      bar_y = bar_h + 20
      bar_h_v = 120
      th_h = int(bar_h_v * gas)
      cv2.rectangle(frame, (bar_x, bar_y), (bar_x + 10, bar_y + bar_h_v), (40, 40, 40), 1)
      if th_h > 0:
        cv2.rectangle(frame, (bar_x, bar_y + bar_h_v - th_h), (bar_x + 10, bar_y + bar_h_v), (0, 255, 0), -1)
      br_h = int(bar_h_v * brake)
      brake_color = (0, 200, 255) if regen else (0, 0, 255)
      bar_x2 = bar_x + 14
      cv2.rectangle(frame, (bar_x2, bar_y), (bar_x2 + 10, bar_y + bar_h_v), (40, 40, 40), 1)
      if br_h > 0:
        cv2.rectangle(frame, (bar_x2, bar_y + bar_h_v - br_h), (bar_x2 + 10, bar_y + bar_h_v), brake_color, -1)

      # --- Blindspot warnings (left/right edge triangles) ---
      if t.get("leftBlindspot", False):
        pts = np.array([[0, fh // 2 - 20], [20, fh // 2], [0, fh // 2 + 20]], np.int32)
        cv2.fillPoly(frame, [pts], (0, 0, 255))
      if t.get("rightBlindspot", False):
        pts = np.array([[fw, fh // 2 - 20], [fw - 20, fh // 2], [fw, fh // 2 + 20]], np.int32)
        cv2.fillPoly(frame, [pts], (0, 0, 255))

      # --- Steering wheel (bottom center) ---
      steer_deg = t.get("steeringAngleDeg", 0.0)
      self._draw_steering_wheel(frame, steer_deg)

      # --- G-force ball (bottom right) ---
      lat_accel = t.get("latAccel", 0.0)
      long_accel = t.get("longAccel", 0.0)
      self._draw_gforce_ball(frame, long_accel, lat_accel)

      # --- View mode label (top-center, below status) ---
      view = self.view_mode.upper()
      cv2.putText(frame, view, (fw // 2 - 30, bar_h + 80), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)

    except Exception as e:
      logger.debug(f"Telemetry overlay error: {e}")
    return frame

  def _draw_steering_wheel(self, frame: np.ndarray, angle_deg: float):
    """Draw a virtual steering wheel at bottom-center."""
    try:
      fh, fw = frame.shape[:2]
      cx = fw // 2
      cy = fh - 60
      radius = 50
      angle_rad = np.radians(-angle_deg)

      cv2.circle(frame, (cx, cy), radius, (220, 220, 220), 3)
      cv2.circle(frame, (cx, cy), radius - 4, (40, 40, 40), 1)

      for spoke_angle in [0, 120, 240]:
        rad = np.radians(spoke_angle) + angle_rad
        x1 = int(cx + (radius - 10) * np.cos(rad))
        y1 = int(cy + (radius - 10) * np.sin(rad))
        x2 = int(cx + 10 * np.cos(rad))
        y2 = int(cy + 10 * np.sin(rad))
        cv2.line(frame, (x2, y2), (x1, y1), (180, 180, 180), 2)

      cv2.circle(frame, (cx, cy), 8, (100, 100, 100), -1)
      cv2.circle(frame, (cx, cy), 8, (200, 200, 200), 1)

      marker_rad = angle_rad - np.pi / 2
      mx = int(cx + (radius - 6) * np.cos(marker_rad))
      my = int(cy + (radius - 6) * np.sin(marker_rad))
      cv2.circle(frame, (mx, my), 4, (0, 0, 255), -1)

      cv2.putText(frame, f"{angle_deg:.0f}°", (cx - 25, cy + radius + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
    except Exception as e:
      logger.debug(f"Steering wheel overlay error: {e}")

  def _draw_gforce_ball(self, frame: np.ndarray, long_g: float, lat_g: float):
    """Draw a G-force ball (like iRacing) at bottom-right."""
    try:
      fh, fw = frame.shape[:2]
      cx = fw - 50
      cy = fh - 50
      radius = 36

      cv2.circle(frame, (cx, cy), radius, (40, 40, 40), -1)
      cv2.circle(frame, (cx, cy), radius, (120, 120, 120), 1)

      cv2.line(frame, (cx - radius, cy), (cx + radius, cy), (80, 80, 80), 1)
      cv2.line(frame, (cx, cy - radius), (cx, cy + radius), (80, 80, 80), 1)

      scale = 20.0
      dx = int(np.clip(-lat_g * scale, -radius + 4, radius - 4))
      dy = int(np.clip(-long_g * scale, -radius + 4, radius - 4))
      color = (0, 255, 0) if abs(lat_g) < 0.5 and abs(long_g) < 0.5 else (0, 255, 255) if abs(lat_g) < 1.0 else (0, 0, 255)
      cv2.circle(frame, (cx + dx, cy + dy), 5, color, -1)
      cv2.circle(frame, (cx + dx, cy + dy), 5, (255, 255, 255), 1)

      cv2.putText(frame, f"{abs(lat_g):.1f}G", (cx - 20, cy + radius + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1, cv2.LINE_AA)
    except Exception as e:
      logger.debug(f"G-force overlay error: {e}")
