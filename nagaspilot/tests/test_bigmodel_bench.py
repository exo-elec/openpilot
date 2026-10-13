from nagaspilot.bigmodel import server_ref as S
from nagaspilot.bigmodel.client import Client
from nagaspilot.bigmodel.tools import bench_link


def test_percentile_picks_from_the_sorted_list():
  ms = sorted(float(i) for i in range(100))
  assert bench_link.percentile(ms, .5) == 50.0
  assert bench_link.percentile(ms, .99) == 99.0
  assert bench_link.percentile([3.0], .99) == 3.0


def test_the_bench_runs_against_the_reference_server_and_times_every_frame():
  server = S.Server(S.StubBackend(1024, 600), '127.0.0.1', 0)
  try:
    ms = bench_link.run(Client('127.0.0.1', server.port), frames=5, hz=200)
  finally:
    server.close()
  assert len(ms) == 5 and ms == sorted(ms) and ms[0] > 0
