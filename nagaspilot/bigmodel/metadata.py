"""The output layout the phone's reply is sliced with: NGP10's own model metadata, read once.

The reply is the raw vision output followed by the raw policy output, so the comma slices it exactly as `ModelState` slices
its own. The phone's model has the same contract as the small one (docs/NAVPILOT_BIGMODEL.md D1); a `big_` metadata file,
when one exists, wins over the small model's.
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parents[2] / 'selfdrive' / 'modeld' / 'models'


@dataclass(frozen=True)
class Contract:
  vision_slices: dict
  policy_slices: dict
  vision_floats: int
  policy_floats: int


def _read(path: Path) -> tuple[dict, int]:
  with open(path, 'rb') as f:
    meta = pickle.load(f)
  return meta['output_slices'], int(meta['output_shapes']['outputs'][1])


def load(models_dir: Path = MODELS_DIR) -> Contract | None:
  """The contract, or None when no metadata is built (a big model cannot be sliced, so none is expected)."""
  try:
    found = {}
    for name in ('vision', 'policy'):
      for prefix in ('big_', ''):
        path = models_dir / f'{prefix}driving_{name}_metadata.pkl'
        if path.is_file():
          found[name] = _read(path)
          break
      else:
        return None
    return Contract(found['vision'][0], found['policy'][0], found['vision'][1], found['policy'][1])
  except (OSError, KeyError, IndexError, TypeError, ValueError, pickle.UnpicklingError, EOFError):
    return None
