"""Loopback-only, read-only NGP status viewer; no vehicle commands or parameter writes."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = b'''<!doctype html><html><meta name="viewport" content="width=device-width"><title>NGP Status</title>
<style>body{background:#101318;color:#edf0f4;font:20px sans-serif;margin:32px}
pre{background:#1d232c;padding:24px;border-radius:16px;white-space:pre-wrap}</style>
<h1>NGP Status</h1><p>Read-only device and trip information</p><pre id="state">Loading...</pre>
<script>async function refresh(){
try{const r=await fetch('/api/state');
document.getElementById('state').textContent=JSON.stringify(await r.json(),null,2)
}catch(e){document.getElementById('state').textContent='Device unavailable'}}
refresh();setInterval(refresh,2000)</script></html>'''


def snapshot(params):
  # Only non-sensitive data is exposed. Never serialize all Params.
  result = {'onroad': params.get_bool('IsOnroad')}
  for name in ('ngp_trip_total_distance', 'ngp_trip_total_drives', 'ngp_trip_last_distance', 'ngp_trip_last_duration'):
    value = params.get(name)
    if isinstance(value, bytes):
      value = value.decode('utf-8', errors='replace')
    result[name.removeprefix('ngp_trip_')] = value
  return result


def handler(params):
  class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
      if self.path == '/':
        body, content_type = PAGE, 'text/html; charset=utf-8'
      elif self.path == '/api/state':
        body, content_type = json.dumps(snapshot(params)).encode(), 'application/json'
      else:
        self.send_error(404)
        return
      self.send_response(200)
      self.send_header('Content-Type', content_type)
      self.send_header('Content-Length', str(len(body)))
      self.send_header('Cache-Control', 'no-store')
      self.end_headers()
      self.wfile.write(body)

    def log_message(self, *_args):
      pass
  return Handler


def main():
  from openpilot.common.params import Params
  with ThreadingHTTPServer(('127.0.0.1', 9091), handler(Params())) as server:
    server.serve_forever()


if __name__ == '__main__':
  main()
