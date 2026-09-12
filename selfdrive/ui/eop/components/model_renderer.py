"""Lane lines, road edges, the planned path and lead markers.

Port of model.cc. The maths is the same: model output is in car space, and
each point is projected to screen through the camera intrinsics composed with
the live calibration, then the projected points are stitched into polygons.

Two structural differences from the C++.

The projection is done with numpy on whole arrays rather than a call per
point. model.cc calls mapToScreen() once per sample per line -- roughly 33
points x 6 lines x 2 edges per frame -- which is cheap in C++ and would not be
in a Python loop at 20 Hz. Projecting a (3, N) block at a time is the same
arithmetic in one call.

And the renderer takes a ModelFrame of plain arrays rather than reaching into
a SubMaster. state.py owns messaging; this draws what it is given, which also
means the geometry can be tested without capnp.
"""

from __future__ import annotations

import numpy as np

from openpilot.selfdrive.ui.eop.qt import (
  QColor,
  QLinearGradient,
  QPainter,
  QPointF,
  QPolygonF,
  Qt,
  QWidget,
)
from openpilot.selfdrive.ui.eop.state import ModelFrame, Snapshot

# Points projected this far outside the view are dropped rather than drawn.
# Some slack is needed: a polygon with one vertex just off screen still has
# visible edges, so clipping exactly at the border would chop the path short.
CLIP_MARGIN = 500

MIN_DRAW_DISTANCE = 10.0
MAX_DRAW_DISTANCE = 100.0

# Half-width in metres used to give each line some thickness in car space, so
# it narrows with distance the way the road does.
LANE_LINE_HALF_W = 0.025
PATH_HALF_W = 0.9

# 0.1 is one UI_FREQ_HZ half-second, matching model.cc's transition_speed.
GRADIENT_BLEND_STEP = 0.1

_THROTTLE_COLORS = (
  QColor.fromHslF(148.0 / 360.0, 0.94, 0.51, 0.4),
  QColor.fromHslF(112.0 / 360.0, 1.0, 0.68, 0.35),
  QColor.fromHslF(112.0 / 360.0, 1.0, 0.68, 0.0),
)
_NO_THROTTLE_COLORS = (
  QColor.fromHslF(148.0 / 360.0, 0.0, 0.95, 0.4),
  QColor.fromHslF(112.0 / 360.0, 0.0, 0.95, 0.35),
  QColor.fromHslF(112.0 / 360.0, 0.0, 0.95, 0.0),
)


def path_length_idx(line_x: np.ndarray, limit: float) -> int:
  """Last index whose x is still within `limit`.

  Mirrors model.cc's get_path_length_idx, including that it starts at 1 and
  so never returns less than 0 even for an empty tail.
  """
  if line_x.size < 2:
    return 0
  within = np.nonzero(line_x[1:] <= limit)[0]
  return int(within[-1] + 1) if within.size else 0


def project(transform: np.ndarray, xyz: np.ndarray) -> np.ndarray:
  """Project device-frame points to screen. `xyz` is (3, N); returns (2, N).

  Points behind the camera plane come back as NaN rather than as a division
  blow-up, so a caller can drop them with one isfinite mask instead of
  guarding each point.
  """
  pt = transform @ xyz
  z = pt[2]
  with np.errstate(divide="ignore", invalid="ignore"):
    out = np.where(z > 0, pt[:2] / z, np.nan)
  return out


class ModelRenderer(QWidget):
  """Path overlay over the camera."""

  def __init__(self, parent=None):
    super().__init__(parent)
    self.setObjectName("modelPath")
    self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    self.setAttribute(Qt.WA_TranslucentBackground, True)
    self._frame: ModelFrame | None = None
    self._snap = Snapshot()
    self._blend = 1.0
    self._prev_allow_throttle = True

  def set_frame(self, frame: ModelFrame | None, snap: Snapshot) -> None:
    self._frame = frame
    self._snap = snap
    self.update()

  # ---- painting ---------------------------------------------------------

  def paintEvent(self, event):
    f = self._frame
    if f is None or not f.valid:
      return

    p = QPainter(self)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)

    max_distance = float(np.clip(f.position[0, -1] if f.position.size else 0.0,
                                 MIN_DRAW_DISTANCE, MAX_DRAW_DISTANCE))

    self._draw_lane_lines(p, f, max_distance)
    self._draw_path(p, f, max_distance)
    self._draw_blind_spot_lanes(p, f, max_distance)
    if f.longitudinal_control:
      self._draw_leads(p, f)

  def _clip_rect(self):
    return self.rect().adjusted(-CLIP_MARGIN, -CLIP_MARGIN, CLIP_MARGIN, CLIP_MARGIN)

  def _draw_lane_lines(self, p: QPainter, f: ModelFrame, max_distance: float) -> None:
    if not f.lane_lines:
      return
    max_idx = path_length_idx(f.lane_lines[0][0], max_distance)

    for line, prob in zip(f.lane_lines, f.lane_line_probs, strict=False):
      poly = self._band_polygon(f, line, LANE_LINE_HALF_W * prob, 0.0, max_idx)
      if poly.size() < 3:
        continue
      p.setBrush(QColor.fromRgbF(1.0, 1.0, 1.0, float(np.clip(prob, 0.0, 0.7))))
      p.drawPolygon(poly)

    for edge, std in zip(f.road_edges, f.road_edge_stds, strict=False):
      poly = self._band_polygon(f, edge, LANE_LINE_HALF_W, 0.0, max_idx)
      if poly.size() < 3:
        continue
      # Confidence is inverted: a high standard deviation means the edge is
      # uncertain, so it fades out rather than being drawn as a hard line.
      p.setBrush(QColor.fromRgbF(1.0, 0.0, 0.0, float(np.clip(1.0 - std, 0.0, 1.0))))
      p.drawPolygon(poly)

  def _draw_path(self, p: QPainter, f: ModelFrame, max_distance: float) -> None:
    # The path stops short of a lead car, so it does not appear to run
    # through the vehicle in front.
    if f.lead_valid:
      lead_d = f.lead_d * 2.0
      max_distance = float(np.clip(lead_d - min(lead_d * 0.35, 10.0), 0.0, max_distance))

    max_idx = path_length_idx(f.position[0], max_distance)
    poly = self._band_polygon(f, f.position, PATH_HALF_W, f.path_offset_z,
                              max_idx, allow_invert=False)
    if poly.size() < 3:
      return

    grad = QLinearGradient(0, self.height(), 0, 0)
    if f.experimental_mode:
      self._fill_acceleration_gradient(grad, f, poly)
    else:
      self._fill_throttle_gradient(grad, f)
    p.setBrush(grad)
    p.drawPolygon(poly)

  def _fill_acceleration_gradient(self, grad: QLinearGradient, f: ModelFrame,
                                  poly: QPolygonF) -> None:
    """Experimental mode colours the path by planned acceleration: green
    where it intends to speed up, red where it intends to slow."""
    height = self.height()
    # The first half of the polygon is the right-hand edge, bottom to top.
    max_len = min(poly.size() // 2, f.acceleration.size)
    i = 0
    while i < max_len:
      pt = poly.at(max_len - i - 1)
      y = pt.y()
      if 0 <= y <= height:
        stop = (height - y) / height
        accel = float(f.acceleration[i])
        hue = max(min(60.0 + accel * 35.0, 120.0), 0.0)
        # Rounded because drawPolygon is measurably slower with an
        # unrounded hue -- noted as such in model.cc.
        hue = int(hue * 100 + 0.5) / 100.0
        sat = min(abs(accel * 1.5), 1.0)
        light = _map_val(sat, 0.0, 1.0, 0.95, 0.62)
        alpha = _map_val(stop, 0.75 / 2.0, 0.75, 0.4, 0.0)
        grad.setColorAt(stop, QColor.fromHslF(hue / 360.0, sat, light, alpha))
      # Every other point is enough to describe the ramp, except keep the last.
      i += 2 if (i + 2) < max_len else 1

  def _fill_throttle_gradient(self, grad: QLinearGradient, f: ModelFrame) -> None:
    """Green while the planner will use throttle, grey while it will not,
    cross-fading rather than snapping between the two."""
    allow = f.allow_throttle or not f.longitudinal_control
    if allow != self._prev_allow_throttle:
      self._prev_allow_throttle = allow
      # Invert rather than reset, so a change mid-fade continues smoothly
      # from where it is instead of jumping back to the start.
      self._blend = max(1.0 - self._blend, 0.0)

    begin = _NO_THROTTLE_COLORS if allow else _THROTTLE_COLORS
    end = _THROTTLE_COLORS if allow else _NO_THROTTLE_COLORS
    if self._blend < 1.0:
      self._blend = min(self._blend + GRADIENT_BLEND_STEP, 1.0)

    for stop, b, e in ((0.0, begin[0], end[0]), (0.5, begin[1], end[1]),
                       (1.0, begin[2], end[2])):
      grad.setColorAt(stop, _blend_colors(b, e, self._blend))

  def _draw_blind_spot_lanes(self, p: QPainter, f: ModelFrame, max_distance: float) -> None:
    """Fill the adjacent lane red when the driver signals into an occupied
    blind spot. Needs four lines, so it is skipped on a model output that
    does not carry both road edges."""
    s = self._snap
    show_left = s.left_blinker and s.blind_spot.left > 0
    show_right = s.right_blinker and s.blind_spot.right > 0
    if not (show_left or show_right):
      return
    if len(f.lane_lines) < 4 or len(f.road_edges) < 2:
      return

    max_idx = path_length_idx(f.position[0], max_distance)
    pairs = []
    if show_left:
      pairs.append((f.road_edges[0], f.lane_lines[1]))
    if show_right:
      pairs.append((f.lane_lines[2], f.road_edges[1]))

    for left_line, right_line in pairs:
      poly = self._lane_polygon(f, left_line, right_line, f.path_offset_z, max_idx)
      if poly.size() < 3:
        continue
      grad = QLinearGradient(0, self.height(), 0, 0)
      for stop, alpha in ((0.0, 0.6), (0.5, 0.4), (1.0, 0.2)):
        grad.setColorAt(stop, QColor.fromHslF(0.0, 0.75, 0.5, alpha))
      p.setBrush(grad)
      p.drawPolygon(poly)

  def _draw_leads(self, p: QPainter, f: ModelFrame) -> None:
    for lead in f.leads:
      self._draw_lead(p, lead)

  def _draw_lead(self, p: QPainter, lead) -> None:
    """A chevron that grows and reddens as the gap closes."""
    speed_buff, lead_buff = 10.0, 40.0
    d_rel, v_rel = lead.d_rel, lead.v_rel

    fill_alpha = 0.0
    if d_rel < lead_buff:
      fill_alpha = 255 * (1.0 - d_rel / lead_buff)
      if v_rel < 0:
        # Closing fast is more urgent than merely being close.
        fill_alpha += 255 * (-v_rel / speed_buff)
      fill_alpha = min(fill_alpha, 255.0)

    xy = project(self._transform_for(lead), np.array([[lead.d_rel], [-lead.y_rel],
                                                      [lead.z]], dtype=np.float64))
    if not np.all(np.isfinite(xy)):
      return

    sz = float(np.clip((25 * 30) / (d_rel / 3 + 30), 15.0, 30.0)) * 2.35
    x = float(np.clip(xy[0, 0], 0.0, self.width() - sz / 2))
    y = min(float(xy[1, 0]), self.height() - sz * 0.6)
    g_xo, g_yo = sz / 5, sz / 10

    p.setBrush(QColor(218, 202, 37, 255))
    p.drawPolygon(_polygon([(x + sz * 1.35 + g_xo, y + sz + g_yo), (x, y - g_yo),
                            (x - sz * 1.35 - g_xo, y + sz + g_yo)]))
    p.setBrush(QColor(201, 34, 49, int(fill_alpha)))
    p.drawPolygon(_polygon([(x + sz * 1.25, y + sz), (x, y),
                            (x - sz * 1.25, y + sz)]))

  def _transform_for(self, _lead) -> np.ndarray:
    return self._frame.transform

  # ---- polygon construction ---------------------------------------------

  def _band_polygon(self, f: ModelFrame, line: np.ndarray, half_w: float,
                    z_off: float, max_idx: int,
                    allow_invert: bool = True) -> QPolygonF:
    """Widen a centre line by half_w and project both edges.

    Device y is positive to the right, so y - half_w is the left edge.

    Returns the right edge reversed followed by the left edge, which is the
    winding drawPolygon needs for a closed band.
    """
    n = min(max_idx + 1, line.shape[1])
    if n <= 0:
      return QPolygonF()
    x, y, z = line[0, :n], line[1, :n], line[2, :n] + z_off
    # Highly negative x projects above the frame and flickers; clip to the
    # camera's zy plane instead of drawing it.
    ahead = x >= 0
    if not np.any(ahead):
      return QPolygonF()
    x, y, z = x[ahead], y[ahead], z[ahead]

    left = project(f.transform, np.vstack([x, y - half_w, z]))
    right = project(f.transform, np.vstack([x, y + half_w, z]))
    both = np.isfinite(left).all(axis=0) & np.isfinite(right).all(axis=0)
    if not np.any(both):
      return QPolygonF()

    lefts, rights = [], []
    last_y = None
    for i in np.nonzero(both)[0]:
      ly = float(left[1, i])
      # A wide band inverts going over a crest, which draws as a bow-tie.
      # Dropping the inverted samples is what model.cc does.
      if not allow_invert and last_y is not None and ly > last_y:
        continue
      last_y = ly
      lefts.append(QPointF(float(left[0, i]), ly))
      rights.append(QPointF(float(right[0, i]), float(right[1, i])))

    if len(lefts) < 2:
      return QPolygonF()
    poly = QPolygonF()
    for pt in reversed(rights):
      poly.append(pt)
    for pt in lefts:
      poly.append(pt)
    return poly

  def _lane_polygon(self, f: ModelFrame, left_line: np.ndarray,
                    right_line: np.ndarray, z_off: float, max_idx: int) -> QPolygonF:
    """The region between two separate lines -- used for the adjacent lane."""
    poly = QPolygonF()
    for line, reverse in ((left_line, False), (right_line, True)):
      n = min(max_idx + 1, line.shape[1])
      if n <= 0:
        continue
      x, y, z = line[0, :n], line[1, :n], line[2, :n] + z_off
      ahead = x >= 0
      if not np.any(ahead):
        continue
      xy = project(f.transform, np.vstack([x[ahead], y[ahead], z[ahead]]))
      cols = np.nonzero(np.isfinite(xy).all(axis=0))[0]
      if reverse:
        cols = cols[::-1]
      for i in cols:
        poly.append(QPointF(float(xy[0, i]), float(xy[1, i])))
    return poly


def _polygon(points) -> QPolygonF:
  poly = QPolygonF()
  for x, y in points:
    poly.append(QPointF(x, y))
  return poly


def _map_val(x: float, in_lo: float, in_hi: float, out_lo: float, out_hi: float) -> float:
  """util.h's map_val: linear remap, clamped to the output range."""
  if in_hi == in_lo:
    return out_lo
  t = (x - in_lo) / (in_hi - in_lo)
  t = min(max(t, 0.0), 1.0)
  return out_lo + t * (out_hi - out_lo)


def _blend_colors(start: QColor, end: QColor, t: float) -> QColor:
  if t >= 1.0:
    return end
  return QColor.fromRgbF(
    (1 - t) * start.redF() + t * end.redF(),
    (1 - t) * start.greenF() + t * end.greenF(),
    (1 - t) * start.blueF() + t * end.blueF(),
    (1 - t) * start.alphaF() + t * end.alphaF(),
  )
