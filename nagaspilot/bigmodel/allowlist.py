"""Which phone models the comma will run. The comma never trusts a model because the phone announces it.

An entry pins a model by sha256 to the layout the comma parses it with: input and output shapes, the output slices and `frame_skip`. A phone's ModelSpec
is accepted only when its sha256 is listed and every pinned field is identical. The split contract needs no entry: its layout is the comma's own metadata.

`allowed_models.json` ships empty: no combined model runs until a build adds one (name, sha256 and the spec fields, taken from a model that was
replayed against a recorded route). Standard library only.
"""
from __future__ import annotations

import json
from pathlib import Path

from nagaspilot.bigmodel.spec import ModelSpec

PINNED = ('frame_skip', 'input_shapes', 'output_shapes', 'output_slices')
DEFAULT_PATH = Path(__file__).resolve().parent / 'allowed_models.json'


class NotAllowed(Exception):
  pass


class AllowList:
  def __init__(self, entries: dict[str, dict] | None = None):
    self.entries = entries or {}      # sha256 -> {"name": ..., plus the pinned spec fields in ModelSpec.to_dict() form}

  @classmethod
  def load(cls, path: Path = DEFAULT_PATH) -> AllowList:
    try:
      raw = json.loads(Path(path).read_text())
    except (OSError, ValueError):
      return cls()
    return cls({m['sha256']: m for m in raw.get('models', []) if isinstance(m, dict) and 'sha256' in m})

  def check(self, spec: ModelSpec) -> str:
    """The model's name, or NotAllowed with the reason."""
    entry = self.entries.get(spec.sha256)
    if entry is None:
      raise NotAllowed(f'model {spec.sha256[:12]} is not on the allow-list')
    have = spec.to_dict()
    for field in PINNED:
      if have.get(field) != entry.get(field):
        raise NotAllowed(f"model {spec.sha256[:12]} differs from the allowed one in {field}")
    return str(entry.get('name', spec.sha256[:12]))

  def __bool__(self) -> bool:
    return bool(self.entries)
