"""A local Protomaps PMTiles basemap, read in place of the hosted one (offline maps, ExoPilot).

`RemoteTiles` (tiles.py, upstream) fetches byte ranges of comma's hosted archive. This reads the same ranges from a
file on the device, so mapsd draws without network: the archive is a Protomaps basemap (MVT) extract of the area,
e.g. `pmtiles extract https://build.protomaps.com/<date>.pmtiles map.pmtiles --bbox=<w,s,e,n> --maxzoom=15`.
Where: NAV_PMTILES, else <data>/navigation/map.pmtiles (<data> = /data/media/0, or comma_home on a PC).
"""
import os
from pathlib import Path

from openpilot.system.hardware import PC
from openpilot.system.hardware.hw import Paths


def local_archive() -> Path:
  env = os.environ.get("NAV_PMTILES")
  if env:
    return Path(env)
  return (Path(Paths.comma_home()) if PC else Path('/data/media/0')) / 'navigation' / 'map.pmtiles'


class LocalTiles:
  def __init__(self, path: Path):
    self.path = Path(path)
    self._file = open(self.path, 'rb')  # closed by close(), as RemoteTiles closes its session
    self.size = self.path.stat().st_size

  def read(self, offset: int, length: int) -> bytes:
    if offset < 0 or length < 0 or offset + length > self.size:
      raise OSError('Map tile range outside the archive')
    self._file.seek(offset)
    data = self._file.read(length)
    if len(data) != length:
      raise OSError('Truncated map archive')
    return data

  def prefetch(self, ranges):
    pass  # already on disk

  def close(self):
    self._file.close()


def open_tiles():
  """The local archive when there is one, else upstream's hosted one."""
  path = local_archive()
  if path.is_file():
    return LocalTiles(path)
  from openpilot.selfdrive.navigation.tiles import RemoteTiles
  return RemoteTiles()
