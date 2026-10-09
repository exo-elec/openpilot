"""Asynchronous, viewport-only OSM raster tiles for the compact Qt map.

Qt handles HTTP freshness/conditional cache requests. No prefetch or bulk/offline
area downloads: only visible tiles are requested. Provider can be configured
without changing firmware, and HTTP/image failures never block the UI thread.
"""
from __future__ import annotations

import math
import os
import struct
import time
from collections import OrderedDict
from dataclasses import dataclass

from PyQt5 import QtNetwork

from openpilot.selfdrive.ui.qt import QtCore, QObject, QImage, QTimer, Signal

TILE_SIZE = 256
ZOOM = 16
MAX_LATITUDE = 85.05112878
DEFAULT_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def world_pixel(latitude: float, longitude: float, zoom: int = ZOOM) -> tuple[float, float]:
  lat = max(-MAX_LATITUDE, min(MAX_LATITUDE, latitude))
  size = TILE_SIZE * 2 ** zoom
  sin_lat = math.sin(math.radians(lat))
  return ((longitude + 180.0) / 360.0 * size,
          (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * size)


def project(latitude: float, longitude: float, centre: tuple[float, float],
            width: float, height: float, zoom: int = ZOOM) -> tuple[float, float]:
  x, y = world_pixel(latitude, longitude, zoom)
  cx, cy = world_pixel(*centre, zoom)
  size = TILE_SIZE * 2 ** zoom
  dx = (x - cx + size / 2) % size - size / 2
  return width / 2 + dx, height / 2 + y - cy


@dataclass(frozen=True)
class Tile:
  key: tuple[int, int, int]
  x: float
  y: float


def visible_tiles(centre: tuple[float, float], width: float, height: float,
                  zoom: int = ZOOM) -> list[Tile]:
  cx, cy = world_pixel(*centre, zoom)
  left, top = cx - width / 2, cy - height / 2
  count = 2 ** zoom
  tiles = []
  for y in range(math.floor(top / TILE_SIZE), math.ceil((top + height) / TILE_SIZE)):
    if not 0 <= y < count:
      continue
    for x in range(math.floor(left / TILE_SIZE), math.ceil((left + width) / TILE_SIZE)):
      tiles.append(Tile((zoom, x % count, y), x * TILE_SIZE - left, y * TILE_SIZE - top))
  return tiles


class OsmTiles(QObject):
  changed = Signal()

  def __init__(self, parent=None, cache_dir: str | None = None):
    super().__init__(parent)
    self.url = os.environ.get("EOP_OSM_TILE_URL", DEFAULT_URL)
    self.attribution = os.environ.get("EOP_OSM_ATTRIBUTION", "© OpenStreetMap contributors")
    self._manager = QtNetwork.QNetworkAccessManager(self)
    cache = QtNetwork.QNetworkDiskCache(self)
    cache.setCacheDirectory(cache_dir or os.environ.get(
      "EOP_OSM_CACHE_DIR", os.path.join(
        QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.CacheLocation), "osm-tiles")))
    cache.setMaximumCacheSize(64 * 1024 * 1024)
    self._manager.setCache(cache)
    self._images: OrderedDict[tuple, QImage] = OrderedDict()
    self._pending: dict[tuple, object] = {}
    self._retry_after: dict[tuple, float] = {}
    self.failed = False

  def image(self, key: tuple[int, int, int]) -> QImage | None:
    image = self._images.get(key)
    if image is not None:
      self._images.move_to_end(key)
    return image

  def request(self, key: tuple[int, int, int]) -> None:
    if key in self._images or key in self._pending or len(self._pending) >= 4:
      return
    if self._retry_after.get(key, 0.0) > time.monotonic():
      return
    z, x, y = key
    try:
      url = QtCore.QUrl(self.url.format(z=z, x=x, y=y))
    except (KeyError, ValueError):
      self.failed = True
      return
    if url.scheme() != "https" or not url.host():
      self.failed = True
      return
    request = QtNetwork.QNetworkRequest(url)
    request.setRawHeader(b"User-Agent", b"ExoPilot-02M/1.0 (+https://github.com/exo-elec/openpilot)")
    request.setAttribute(QtNetwork.QNetworkRequest.CacheLoadControlAttribute,
                         QtNetwork.QNetworkRequest.PreferCache)
    request.setAttribute(QtNetwork.QNetworkRequest.RedirectPolicyAttribute,
                         QtNetwork.QNetworkRequest.NoLessSafeRedirectPolicy)
    reply = self._manager.get(request)
    self._pending[key] = reply
    timeout = QTimer(reply)
    timeout.setSingleShot(True)
    timeout.timeout.connect(reply.abort)
    timeout.start(10000)
    reply.finished.connect(lambda: self._finished(key, reply, timeout))

  def _finished(self, key, reply, timeout) -> None:
    timeout.stop()
    self._pending.pop(key, None)
    if reply.error() == QtNetwork.QNetworkReply.OperationCanceledError:
      reply.deleteLater()
      return
    image = QImage()
    # OSM raster tiles are PNGs; reject oversized payloads/dimensions before
    # handing the data to an image decoder.
    data = bytes(reply.readAll())
    if (reply.error() == QtNetwork.QNetworkReply.NoError and len(data) <= 1024 * 1024
        and data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24
        and struct.unpack(">II", data[16:24]) == (TILE_SIZE, TILE_SIZE)):
      image.loadFromData(data)
    if not image.isNull():
      self._images[key] = image
      self._images.move_to_end(key)
      while len(self._images) > 64:
        self._images.popitem(last=False)
      self._retry_after.pop(key, None)
      self.failed = False
    else:
      self.failed = True
      self._retry_after[key] = time.monotonic() + 30.0
      while len(self._retry_after) > 64:
        self._retry_after.pop(next(iter(self._retry_after)))
    reply.deleteLater()
    self.changed.emit()

  def suspend(self) -> None:
    """A hidden panel is not a tile downloader."""
    for reply in list(self._pending.values()):
      reply.abort()
