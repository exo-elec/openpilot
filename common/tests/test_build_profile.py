"""The same process registry selects renderers and board-specific I/O."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('backend,soc,process_type,radar', [
  ('cpp', 'rk3588', 'NativeProcess', False),
  ('pyqt5', 'rk3588', 'PythonProcess', False),
  ('pyqt5', 'rk3576', 'PythonProcess', True),
])
def test_shared_registry_selects_renderer_and_corner_wifi(backend, soc, process_type, radar):
  code = '''
import json,sys,types
profile = types.ModuleType('openpilot.common.build_profile')
profile.UI_BACKEND = sys.argv[1]
sys.modules[profile.__name__] = profile
params = types.ModuleType('openpilot.common.params')
class Params:
  def get_bool(self,key): return key == 'EOPIgnitionOn'
params.Params = Params
sys.modules[params.__name__] = params
process = types.ModuleType('openpilot.system.manager.process')
class PythonProcess:
  def __init__(self,name,*args,**kwargs): self.name,self.condition = name,args[-1]
class NativeProcess(PythonProcess): pass
process.PythonProcess,process.NativeProcess = PythonProcess,NativeProcess
sys.modules[process.__name__] = process
from openpilot.nagaspilot.manager.process_config import managed_processes
ui = managed_processes['ui']
corner = managed_processes['radar4d']
print(json.dumps({'ui':type(ui).__name__, 'radar':corner.condition(True,Params(),None),
                  'ui_loaded':any(n.startswith('openpilot.selfdrive.ui') for n in sys.modules)}))
'''
  env = dict(os.environ, HARDWARE=soc)
  result = subprocess.run([sys.executable, '-c', code, backend], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=30)
  assert result.returncode == 0, result.stderr
  assert json.loads(result.stdout) == {'ui':process_type, 'radar':radar, 'ui_loaded':False}


@pytest.mark.parametrize('backend', ['cpp', 'pyqt5'])
def test_ui_build_selects_native_binary_or_disk_translations(backend, tmp_path, monkeypatch):
  from types import SimpleNamespace
  import common.build_profile as profile  # noqa: TID251 -- SCons uses the raw common namespace.
  monkeypatch.setattr(profile, 'UI_BACKEND', backend)
  calls = []

  class Env(dict):
    def __getattr__(self, method):
      def record(*args, **kwargs):
        calls.append((method, args))
        return []
      return record

  env = Env(LIBS=[], FRAMEWORKS=[], CXXFLAGS=[])

  def file_node(path):
    if path == 'translations/languages.json':
      return SimpleNamespace(abspath=str(ROOT / 'selfdrive/ui/translations/languages.json'))
    return SimpleNamespace(abspath=str(tmp_path / Path(path).name))

  namespace = dict(env=env, qt_env=env, arch='x86_64', common=[], messaging=[], visionipc=[], transformations=[],
                   Import=lambda *args: None, Export=lambda *args: None,
                   GetOption=lambda key: False, File=file_node, Glob=lambda *args, **kwargs: [])
  code = (ROOT / 'selfdrive/ui/SConscript').read_text()
  exec(compile(code, 'selfdrive/ui/SConscript', 'exec'), namespace)
  programs = [args[0] for method,args in calls if method == 'Program']
  assert ('ui' in programs) == (backend == 'cpp')
  commands = [args[2] for method,args in calls if method == 'Command']
  assert any('lrelease' in str(command) for command in commands)
  if backend == 'pyqt5':
    assert not any('rcc ' in str(command) or 'moc ' in str(command) for command in commands)
