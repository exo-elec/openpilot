"""Real NavPilot Dart -> Python PLP loopback; no phone or inference claims.

Uses an isolated copy of the current sibling PLP package and offline Dart deps.
Run with --dart /path/to/dart and optionally --pub-cache /path/to/existing/cache.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


def check(args):
  root = Path(__file__).resolve().parents[2]
  sys.path.insert(0, str(root))
  from nagaspilot.plp.cable import CablePLP
  from nagaspilot.plp.hello import Hello
  from nagaspilot.plp.locate import LocateSink

  fixture = root / 'nagaspilot/tests/fixtures/map_horizon_navpilot.json'
  package = args.navpilot_root / 'packages/plp'
  if not package.is_dir():
    raise ValueError(f'NavPilot PLP package not found: {package}')
  env = os.environ.copy()
  env['DART_SUPPRESS_ANALYTICS'] = 'true'
  if args.pub_cache:
    env['PUB_CACHE'] = str(args.pub_cache)

  with tempfile.TemporaryDirectory(prefix='navpilot-plp-interop-') as temp:
    project = Path(temp) / 'plp'
    shutil.copytree(package, project, ignore=shutil.ignore_patterns('.dart_tool', 'build'))
    shutil.copyfile(Path(__file__).with_name('navpilot_plp_client.dart'), project / 'interop.dart')
    dependencies = subprocess.run([args.dart, 'pub', 'get', '--offline'], cwd=project, env=env,
                                  capture_output=True, text=True, timeout=60, check=False)
    if dependencies.returncode:
      raise RuntimeError(f'Dart offline dependencies unavailable:\n{dependencies.stdout}\n{dependencies.stderr}')

    sink = LocateSink()
    server = CablePLP(Hello(services=(('core', 1, 1), ('locate', 1, 1)), max_payload=64,
                           link='usb', payload_crc=False), sink.attach,
                      bind_addr='127.0.0.1', port=0, allowed_prefix='127.')
    child = None
    server.start()
    try:
      deadline = time.monotonic()+5
      while not server.bound_port and time.monotonic() < deadline:
        time.sleep(0.01)
      if not server.bound_port:
        raise TimeoutError('Python PLP server did not bind loopback')
      child = subprocess.Popen([args.dart, 'run', 'interop.dart', str(server.bound_port), str(fixture)],
                               cwd=project, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
      deadline = time.monotonic()+10
      while (sink.latest_fix is None or sink.horizons_taken != 1) and time.monotonic() < deadline and child.poll() is None:
        time.sleep(0.01)
      if sink.latest_fix is None or abs(sink.latest_fix.lat-13.75) > 1e-8:
        raise RuntimeError('Dart GNSS payload did not reach the Python LocateSink intact')
      expected_route = json.loads(fixture.read_text())['route_id']
      if sink.horizons_taken != 1 or sink.tracker.current.route_id != expected_route:
        raise RuntimeError('Fragmented Dart map horizon was not accepted by the Python host')
      out, err = child.communicate('done\n', timeout=5)
      if child.returncode:
        raise RuntimeError(f'Dart PLP peer failed:\n{out}\n{err}')
      print('PASS Dart -> Python PLP: TCP hello, 64-byte negotiation, GNSS and fragmented map horizon')
    finally:
      if child is not None:
        if child.poll() is None:
          child.kill()
          child.communicate()
        elif child.returncode:
          out, err = child.communicate()
          print(out, err, file=sys.stderr)
      server.stop()


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--navpilot-root', type=Path, default=Path(__file__).resolve().parents[3] / 'navpilot')
  parser.add_argument('--dart', default=shutil.which('dart'))
  parser.add_argument('--pub-cache', type=Path)
  args = parser.parse_args()
  if not args.dart:
    parser.error('Dart SDK not found; supply --dart. No SDK is installed by this check.')
  check(args)


if __name__ == '__main__':
  main()
