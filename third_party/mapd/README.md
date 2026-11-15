# mapd (pfeiferj/openpilot-mapd), vendored

Offline OpenStreetMap daemon, also used by sunnypilot and FrogPilot. Map tiles are downloaded once per country or
state (`mapdIn` download) and then read offline. It publishes `mapdOut` (speed limit, next limit, curve speeds, road
name) and `mapdExtendedOut` (download progress, path) and takes `mapdIn`.

- Source: https://github.com/pfeiferj/openpilot-mapd tag `v2.4.0` (commit 7a5910ca6acbbe44602087dbbe272969136feb7a)
- Binary: the release asset `mapd`, linux/arm64, statically linked. The same file is in `larch64/` (comma devices)
  and `aarch64/` (other arm64 Linux), the arch names SConstruct uses; there is none for x86_64 / Darwin.
- sha256 `0bc3564ec634486d11e95b76e884fd37b783c66933405cec1bd59f179fa09e94` (GitHub's asset digest). Committed as a plain git file: .lfsconfig points LFS at upstream's store, which this fork cannot write.
- License: MIT, see LICENSE. Map data: https://map-data.pfeifer.dev, fetched only when a download is requested.
- Files: tiles under `Paths.mapd_root()` (`/data/media/0/osm` on a device, where mapd itself looks); settings in the
  `MapdSettings` param.

To update: change the tag, replace both binaries, update the sha256 here and in exopilot/tests/test_mapd_install.py.
