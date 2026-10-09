"""Run the daemon's real settings refresh without loading camera/IPC backends."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

PATHD = Path(__file__).resolve().parents[1] / 'daemons/pathd/pathd.py'


@pytest.mark.parametrize('canonical,legacy,expected', [
  (None, False, False), (None, True, True), (b'0', True, False), (b'1', False, True),
])
def test_scale_switch_keeps_saved_settings_and_canonical_off_wins(canonical, legacy, expected):
  tree = ast.parse(PATHD.read_text())
  methods = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_refresh_params']
  assert len(methods) == 1
  applied = []
  namespace = {'time': NS(monotonic=lambda: 100.0), 'set_scale_fix': applied.append}
  exec(compile(ast.Module(body=methods, type_ignores=[]), str(PATHD), 'exec'), namespace)

  class Params:
    def get(self, key):
      assert key == 'EOPPathdFixScaleEnabled'
      return canonical

    def get_bool(self, key):
      return legacy if key == 'ngp_pathd_fix_scale' else True

  daemon = NS(params=Params(), _last_param_t=0.0)
  namespace['_refresh_params'](daemon)
  assert applied == [expected]
  assert daemon._aeb_enabled and daemon._soc_enabled and daemon._stereo_enabled
  namespace['_refresh_params'](daemon)  # refresh remains rate-limited
  assert applied == [expected]
