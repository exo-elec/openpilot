from contextlib import closing
from unittest.mock import patch  # noqa: TID251

import pytest

from openpilot.selfdrive.navigation import local_tiles
from openpilot.selfdrive.navigation.local_tiles import LocalTiles, open_tiles
from openpilot.selfdrive.navigation.pmtiles import PMTiles
from openpilot.selfdrive.navigation.tests.test_navigation import make_archive, road_tile


def test_local_archive_reads_like_the_hosted_one(tmp_path):
  path = tmp_path / 'map.pmtiles'
  path.write_bytes(make_archive(run=3, max_zoom=1))
  with closing(PMTiles(LocalTiles(path))) as archive:   # the archive upstream's tests serve over HTTP, from a file
    assert archive.get(1, 0, 1) == road_tile()
    assert archive.get(1, 1, 1) is None
  source = LocalTiles(path)
  with pytest.raises(OSError):
    source.read(source.size - 2, 4)
  source.close()


def test_open_tiles_prefers_the_local_archive(tmp_path, monkeypatch):
  path = tmp_path / 'map.pmtiles'
  monkeypatch.setenv('NAV_PMTILES', str(path))
  with patch('openpilot.selfdrive.navigation.tiles.RemoteTiles') as remote:
    open_tiles()
    remote.assert_called_once()                      # no local file: upstream's hosted archive
  path.write_bytes(make_archive())
  with closing(open_tiles()) as source:
    assert isinstance(source, LocalTiles) and local_tiles.local_archive() == path
