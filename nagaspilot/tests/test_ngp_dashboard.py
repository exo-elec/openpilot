import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from nagaspilot.runtime.dashboard import handler


class Params:
  def get_bool(self, key):
    return key == 'IsOnroad'

  def get(self, key):
    assert key.startswith('ngp_trip_')
    return b'12'


def test_read_only_http_whitelist():
  server = ThreadingHTTPServer(('127.0.0.1', 0), handler(Params()))
  thread = threading.Thread(target=server.serve_forever)
  thread.start()
  try:
    client = HTTPConnection(*server.server_address)
    client.request('GET', '/api/state')
    response = client.getresponse()
    assert response.status == 200
    data = json.loads(response.read())
    assert data == dict(onroad=True, total_distance='12', total_drives='12', last_distance='12', last_duration='12')
    for method, path, status in [('GET', '/params', 404), ('POST', '/api/state', 501)]:
      client.request(method, path)
      response = client.getresponse()
      assert response.status == status
      response.read()
    client.close()
  finally:
    server.shutdown()
    server.server_close()
    thread.join()
