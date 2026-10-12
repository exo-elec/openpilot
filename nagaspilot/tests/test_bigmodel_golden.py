import json
from pathlib import Path

from nagaspilot.bigmodel.tools.make_golden import build

GOLDEN = Path(__file__).resolve().parents[1] / 'docs' / 'npbm_golden.json'


def test_the_committed_vectors_are_what_the_code_produces():
  assert json.loads(GOLDEN.read_text()) == json.loads(json.dumps(build())), \
    'regenerate: python3 -m nagaspilot.bigmodel.tools.make_golden > nagaspilot/docs/npbm_golden.json (and copy it to NavPilot)'
