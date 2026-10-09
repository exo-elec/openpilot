"""Navigation PiP state, viewport geometry and nonblocking tile lifecycle."""
import math
import os
import time
from types import SimpleNamespace as Msg

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtNetwork

from openpilot.selfdrive.ui.components.map_panel import MapPanel
from openpilot.selfdrive.ui.components.osm_tiles import OsmTiles, project, visible_tiles, world_pixel
from openpilot.selfdrive.ui.components.panels import PanelData, PanelHost
from openpilot.selfdrive.ui.qt import QApplication, QImage, QObject, QtCore, Signal
from openpilot.selfdrive.ui.state import NavMap, NavManeuver, Snapshot, UIState
from openpilot.selfdrive.ui.views.onroad import OnroadView


@pytest.fixture(scope="module")
def app():
  return QApplication.instance() or QApplication([])


class SM:
  def __init__(self, **messages):
    self.messages = messages
    self.valid = dict.fromkeys(messages, True)
    self.recv_time = {key: time.monotonic() for key in messages}

  def __getitem__(self, key):
    return self.messages.get(key, Msg())


class OfflineTiles(QObject):
  changed = Signal()
  attribution = "© OpenStreetMap contributors"
  failed = False

  def __init__(self):
    super().__init__()
    self.requests = []
    self.suspended = False
    self.tile = QImage(256, 256, QImage.Format_RGB32)
    self.tile.fill(0xffdde3da)

  def image(self, key):
    return self.tile

  def request(self, key):
    self.requests.append(key)

  def suspend(self):
    self.suspended = True


def test_projection_centres_vehicle_and_wraps_dateline():
  assert project(13.7, 100.5, (13.7, 100.5), 384, 240) == pytest.approx((192, 120))
  x, _ = project(0, -179.999, (0, 179.999), 384, 240)
  assert 192 < x < 300  # nearby across the date line, not one whole world away
  assert all(math.isfinite(n) for n in world_pixel(90, 0))


def test_only_visible_tiles_and_valid_y_are_returned():
  tiles = visible_tiles((13.7, 100.5), 384, 240)
  assert 1 <= len(tiles) <= 6
  assert all(-256 < t.x < 384 and -256 < t.y < 240 for t in tiles)
  assert all(0 <= t.key[1] < 2 ** 16 and 0 <= t.key[2] < 2 ** 16 for t in tiles)
  assert all(t.key[2] >= 0 for t in visible_tiles((90, 180), 384, 240))


def test_map_prefers_fused_position_and_preserves_zero_bearing():
  sm = SM(gpsLocationExternal=Msg(hasFix=True, latitude=13.7, longitude=100.5, bearingDeg=0),
          fusedPosition=Msg(confidence=0.8, positionGeodetic=Msg(latitude=13.71, longitude=100.51)),
          navRoute=Msg(coordinates=[Msg(latitude=13.71, longitude=100.51),
                                    Msg(latitude=13.72, longitude=100.52)]))
  geo = UIState._read_map(sm, NavManeuver(valid=True))
  assert geo.position_valid and geo.bearing_valid
  assert (geo.latitude, geo.longitude) == (13.71, 100.51)
  assert geo.bearing == 0
  assert len(geo.coordinates) == 2
  sm.recv_time['fusedPosition'] -= 3
  assert UIState._read_map(sm, NavManeuver()).latitude == 13.7
  sm.recv_time['gpsLocationExternal'] -= 3
  assert not UIState._read_map(sm, NavManeuver()).position_valid


def test_invalid_fix_and_invalid_coordinates_do_not_move_map():
  sm = SM(gpsLocationExternal=Msg(hasFix=False, flags=0, latitude=13.7, longitude=100.5))
  assert not UIState._read_map(sm, NavManeuver()).position_valid
  sm.messages['gpsLocationExternal'] = Msg(hasFix=True, latitude=float('nan'), longitude=100.5)
  assert not UIState._read_map(sm, NavManeuver()).position_valid


def test_route_expires_with_navigation_not_rare_geometry_message():
  sm = SM(navInstruction=Msg(maneuverType='turn', maneuverModifier='left',
                            maneuverDistance=250, distanceRemaining=4300, timeRemaining=360),
          navRoute=Msg(coordinates=[Msg(latitude=13.7, longitude=100.5)]))
  sm.recv_time['navRoute'] -= 600
  nav = UIState._read_nav(sm)
  assert nav.valid and nav.remaining_m == 4300 and nav.remaining_s == 360
  assert UIState._read_map(sm, nav).coordinates
  sm.recv_time['navInstruction'] -= 6
  nav = UIState._read_nav(sm)
  assert not nav.valid
  assert not UIState._read_map(sm, nav).coordinates


def test_route_reader_filters_bad_points_and_bounds_work():
  points = [Msg(latitude=13.7, longitude=100.5)] * 5000
  points[0] = Msg(latitude=999, longitude=100.5)
  sm = SM(navRoute=Msg(coordinates=points))
  assert len(UIState._read_map(sm, NavManeuver(valid=True)).coordinates) == 4095


def test_map_is_default_compact_panel_and_swaps_keep_live_state(app):
  host = PanelHost()
  host.resize(1600, 500)
  host._layout()
  assert host.right_key == 'map'
  assert host.right.width() == 384 and host.right.height() == 360
  geo = NavMap(position_valid=True, latitude=13.7, longitude=100.5)
  host.set_data(PanelData({'map': geo}))
  host._on_swap()
  assert host.left_key == 'map'
  assert host.left.data.values['map'] == geo
  host._on_cycle('left', 'right')
  host._on_cycle('left', 'left')
  assert host.left_key == 'map' and host.left.data.values['map'] == geo
  host.set_blocked(True)
  host._on_cycle('left', 'right')
  assert host.left_key == 'map'
  host.deleteLater()


def test_onroad_passes_live_navigation_to_panels(app):
  view = OnroadView(live_camera=False)
  view.resize(1600, 600)
  view.show()
  QApplication.processEvents()
  snap = Snapshot(nav=NavManeuver(valid=True, distance_m=200, remaining_s=360),
                  nav_map=NavMap(position_valid=True, latitude=13.7, longitude=100.5))
  view.set_snapshot(snap)
  assert view.panels.right.data.values['map'] == snap.nav_map
  assert view.panels.right.data.values['nav'] == snap.nav
  view.deleteLater()


def test_map_paints_without_network_and_stops_hidden_loader(app):
  tiles = OfflineTiles()
  widget = MapPanel('right', tiles=tiles)
  widget.resize(384, 360)
  widget.set_data(PanelData({
    'map': NavMap(position_valid=True, latitude=13.7, longitude=100.5, bearing=20, bearing_valid=True,
                  coordinates=((13.7, 100.5), (13.701, 100.501))),
    'nav': NavManeuver(valid=True, maneuver_type='turn', modifier='left', primary_text='Main Road',
                       distance_m=250, remaining_m=4300, remaining_s=360),
  }))
  widget.show()
  QApplication.processEvents()
  assert not widget.grab().isNull()
  assert not tiles.requests
  widget.hide()
  assert tiles.suspended
  widget.deleteLater()


class Reply(QObject):
  finished = Signal()

  def __init__(self):
    super().__init__()
    self.cancelled = False

  def error(self):
    return QtNetwork.QNetworkReply.OperationCanceledError if self.cancelled else QtNetwork.QNetworkReply.NoError

  def readAll(self):
    return QtCore.QByteArray(b'not a tile')

  def abort(self):
    self.cancelled = True
    self.finished.emit()


def test_tile_requests_have_cache_identity_bounds_and_cancel(app, tmp_path, monkeypatch):
  tiles = OsmTiles(cache_dir=str(tmp_path))
  requests = []
  replies = []

  def get(request):
    requests.append(request)
    reply = Reply()
    replies.append(reply)
    return reply

  monkeypatch.setattr(tiles._manager, 'get', get)
  for x in range(8):
    tiles.request((16, x, 1))
  assert len(requests) == 4
  assert requests[0].url().scheme() == 'https'
  assert b'ExoPilot-02M' in bytes(requests[0].rawHeader(b'User-Agent'))
  assert requests[0].attribute(QtNetwork.QNetworkRequest.CacheLoadControlAttribute) == QtNetwork.QNetworkRequest.PreferCache
  tiles.suspend()
  assert all(reply.cancelled for reply in replies)
  assert not tiles._pending and not tiles.failed
  tiles.deleteLater()
