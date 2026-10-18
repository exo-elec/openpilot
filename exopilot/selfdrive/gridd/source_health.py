"""Source provenance and freshness for degraded stereo fusion."""


def fresh_stereo(timestamp: int, now: int, previous: int, *, source_valid: bool, fault: bool) -> bool:
  """Missing/old/faulted stereo gives no new geometry evidence."""
  return source_valid and not fault and previous < timestamp <= now and now-timestamp <= 200_000_000


def grid_source_flags(has_stereo: bool, objects: list[dict]) -> int:
  """GridObjects schema: stereo/left/right/tracked/model/nav bits 0..5."""
  flags = int(has_stereo)
  for obj in objects:
    source = obj.get('source', '')
    if source in ('side_left', 'stereo_left'):
      flags |= 2
    if source in ('side_right', 'stereo_right'):
      flags |= 4
    if obj.get('trackId', 0):
      flags |= 8
    if source == 'drive_vision':
      flags |= 16
  return flags
