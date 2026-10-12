"""Write an allow-list entry for a combined model, from its ONNX file.

    python3 -m nagaspilot.bigmodel.tools.allow_model model.onnx --name "Cinque Terre V2" --evidence "route <id> replay, bench report <ref>" [--add]

Prints the entry (or, with --add, appends it to nagaspilot/bigmodel/allowed_models.json). The comma runs a combined model only if its sha256 is listed with
the layout it parses, so adding one is a release decision: --evidence is required and is recorded in the entry (the route the model was replayed
against and where the bench numbers are). The tool refuses what the comma cannot serve: a stateful graph (not served yet), frames other than the
comma's warp (2, 6, 128, 256), a hidden_state that is not the feature the graph feeds back, or a slice that overlaps it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nagaspilot.bigmodel import protocol as P
from nagaspilot.bigmodel.allowlist import DEFAULT_PATH, PINNED
from nagaspilot.bigmodel.queues import make_history
from nagaspilot.bigmodel.spec import ModelSpec, spec_from_onnx


def entry_for(spec: ModelSpec, name: str, evidence: str) -> dict:
  """The allow-list entry, or ValueError saying why this model cannot be served."""
  if spec.warped_shape != P.FRAME_SHAPE:
    raise ValueError(f'the model takes frames of {spec.warped_shape}, the comma warps {P.FRAME_SHAPE}')
  needed = ('desire', 'traffic_convention', 'action_t') if spec.stateful else ('desire_pulse', 'traffic_convention', 'action_t', 'features_buffer')
  for inp in needed:
    if inp not in spec.input_shapes:
      raise ValueError(f'the model has no {inp} input')
  make_history(spec)            # a queued graph needs a hidden_state to feed back; a stateful one needs matching state_/next_state_ pairs
  _ = spec.reply_slices         # raises if a slice overlaps the hidden state
  d = spec.to_dict()
  return {'name': name, 'sha256': spec.sha256, 'evidence': evidence, **{k: d[k] for k in PINNED}}


def main(argv: list[str] | None = None) -> int:
  ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
  ap.add_argument('onnx')
  ap.add_argument('--name', required=True)
  ap.add_argument('--evidence', required=True, help='the replay and bench evidence for allowing this model')
  ap.add_argument('--frame-skip', type=int, default=4)
  ap.add_argument('--add', action='store_true', help=f'append to {DEFAULT_PATH}')
  a = ap.parse_args(argv)
  try:
    entry = entry_for(spec_from_onnx(a.onnx, frame_skip=a.frame_skip), a.name, a.evidence)
  except ValueError as e:
    print(f'refused: {e}', file=sys.stderr)
    return 2
  if not a.add:
    print(json.dumps(entry, indent=1))
    return 0
  path = Path(DEFAULT_PATH)
  doc = json.loads(path.read_text()) if path.exists() else {'models': []}
  doc['models'] = [m for m in doc['models'] if m.get('sha256') != entry['sha256']] + [entry]
  path.write_text(json.dumps(doc, indent=1) + '\n')
  print(f"allowed {entry['name']} ({entry['sha256'][:12]}) in {path}")
  return 0


if __name__ == '__main__':
  sys.exit(main())
