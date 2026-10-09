"""Live OSM navigation PiP in the existing floating side-panel system."""
from __future__ import annotations

from datetime import datetime, timedelta

from openpilot.selfdrive.ui.components.osm_tiles import OsmTiles, project, visible_tiles
from openpilot.selfdrive.ui.components.panels import SidePanel, panel
from openpilot.selfdrive.ui.qt import Qt, QColor, QPainter, QPen, QPointF, QPolygonF, QRectF
from openpilot.selfdrive.ui.state import NavMap, NavManeuver
from openpilot.selfdrive.ui.views.navigation import Maneuver, format_distance, format_eta


@panel("map")
class MapPanel(SidePanel):
  title = "navigation"
  compact = True

  def __init__(self, panel: str, parent=None, tiles=None):
    super().__init__(panel, parent)
    self.tiles = tiles or OsmTiles(self)
    self.tiles.changed.connect(self.update)

  def hideEvent(self, event):
    self.tiles.suspend()
    super().hideEvent(event)

  def paint_body(self, p: QPainter) -> None:
    nav = self.data.values.get("nav", NavManeuver())
    geo = self.data.values.get("map", NavMap())
    metric = self.data.values.get("is_metric", True)
    width, height = self.width(), self.height()

    p.setPen(QColor("#f0f4f4"))
    font = p.font()
    font.setPointSize(19)
    font.setBold(True)
    p.setFont(font)
    p.drawText(54, 58, format_distance(nav.distance_m, metric) if nav.valid else "No active route")
    font.setPointSize(11)
    font.setBold(False)
    p.setFont(font)
    instruction = nav.primary_text if nav.valid else "Choose a destination in NavPilot"
    instruction = p.fontMetrics().elidedText(instruction, Qt.ElideRight, max(1, width - 68))
    p.drawText(54, 80, instruction)

    if nav.valid:
      p.save()
      p.translate(30, 58)
      p.rotate(Maneuver.parse(f"{nav.maneuver_type} {nav.modifier}").turn_degrees)
      pen = QPen(QColor("#ffffff"), 3)
      pen.setCapStyle(Qt.RoundCap)
      p.setPen(pen)
      p.drawLine(0, 12, 0, -10)
      p.drawLine(0, -10, -6, -3)
      p.drawLine(0, -10, 6, -3)
      p.restore()

    map_rect = QRectF(12, 96, max(1, width - 24), max(1, height - 144))
    p.save()
    p.setClipRect(map_rect)
    p.fillRect(map_rect, QColor("#243135"))
    if geo.position_valid:
      centre = (geo.latitude, geo.longitude)
      for tile in visible_tiles(centre, map_rect.width(), map_rect.height()):
        image = self.tiles.image(tile.key)
        if image is not None:
          p.drawImage(QPointF(map_rect.left() + tile.x, map_rect.top() + tile.y), image)
        else:
          # paint_body is called only for a visible widget; never prefetch.
          self.tiles.request(tile.key)

      points = QPolygonF([QPointF(map_rect.left() + x, map_rect.top() + y)
                         for lat, lon in geo.coordinates
                         for x, y in [project(lat, lon, centre, map_rect.width(), map_rect.height())]])
      if len(points) >= 2:
        for colour, thickness in (("#ffffff", 9), ("#268cff", 5)):
          pen = QPen(QColor(colour), thickness)
          pen.setCapStyle(Qt.RoundCap)
          pen.setJoinStyle(Qt.RoundJoin)
          p.setPen(pen)
          p.drawPolyline(points)

      p.save()
      p.translate(map_rect.center())
      p.rotate(geo.bearing)
      p.setPen(QPen(QColor("#ffffff"), 2))
      p.setBrush(QColor("#1677ec"))
      if geo.bearing_valid:
        p.drawPolygon(QPolygonF([QPointF(0, -15), QPointF(10, 12),
                                 QPointF(0, 7), QPointF(-10, 12)]))
      else:
        p.drawEllipse(QPointF(0, 0), 8, 8)
      p.restore()
      if self.tiles.failed:
        p.setPen(QColor("#ffffff"))
        p.drawText(map_rect.adjusted(8, 8, -8, -28), Qt.AlignTop | Qt.AlignLeft, "Map tiles unavailable")
    else:
      p.setPen(QColor("#d5e0e2"))
      p.drawText(map_rect, Qt.AlignCenter, "Waiting for live location")

    # Visible attribution stays inside the map, above the trip-summary footer.
    attribution = QRectF(map_rect.left(), map_rect.bottom() - 20, map_rect.width(), 20)
    p.fillRect(attribution, QColor(0, 0, 0, 180))
    font.setPointSize(9)
    p.setFont(font)
    p.setPen(QColor("#ffffff"))
    p.drawText(attribution.adjusted(5, 0, -5, 0), Qt.AlignRight | Qt.AlignVCenter, self.tiles.attribution)
    p.restore()

    font.setPointSize(11)
    p.setFont(font)
    p.setPen(QColor("#d5e0e2"))
    summary = "Location unavailable" if not geo.position_valid else "Live location · north up"
    if nav.valid:
      eta = (datetime.now() + timedelta(seconds=min(nav.remaining_s, 7 * 86400))).strftime("%H:%M")
      summary = f"ETA {eta} · {format_eta(nav.remaining_s)} · {format_distance(nav.remaining_m, metric)}"
    p.drawText(QRectF(12, height - 40, width - 24, 30), Qt.AlignCenter,
               p.fontMetrics().elidedText(summary, Qt.ElideRight, max(1, width - 24)))
