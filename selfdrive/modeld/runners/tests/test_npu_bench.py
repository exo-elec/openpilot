import os

import pytest

from openpilot.selfdrive.modeld.runners import npu_bench as nb


class Clock:
  t = 0.0

  def __call__(self):
    return self.t


CLOCK = Clock()


class FakeRKNN:
  latency: dict[str, float] = {}

  def load_rknn(self, path):
    self.path = path
    return 0

  def init_runtime(self, core_mask):
    self.mask = core_mask
    return 0

  def inference(self, inputs):
    assert inputs[0].shape == (1, 3, 8, 8)
    CLOCK.t += self.latency[self.path]

  def release(self):
    pass


@pytest.fixture(autouse=True)
def rk3588(monkeypatch):
  monkeypatch.setenv("RKNN_PLATFORM", "rk3588")


def test_parse_spec():
  s = nb.parse_spec("modeld=/data/m.rknn@20:1x3x8x8:float16")
  assert (s.task, s.path, s.hz, s.shape, s.dtype) == ("modeld", "/data/m.rknn", 20.0, (1, 3, 8, 8), "float16")
  assert nb.parse_spec("yolo=/a@b.rknn@10:1x3x8x8").path == "/a@b.rknn"
  with pytest.raises(nb.argparse.ArgumentTypeError):
    nb.parse_spec("nonsense")


def test_core_loads_sum_per_core():
  a, b, c = (nb.parse_spec(f"{t}=/x@{hz}:1x3x8x8") for t, hz in (("modeld", 20), ("monod", 10), ("yolo", 10)))
  loads = nb.core_loads([(a, 1, 0.03), (b, 2, 0.02), (c, 2, 0.05)])
  assert loads[1] == pytest.approx(0.6)
  assert loads[2] == pytest.approx(0.7)


def test_main_passes_and_fails_on_the_85_percent_line(capsys):
  FakeRKNN.latency = {"/drive.rknn": 0.030, "/mono.rknn": 0.050}
  args = ["--model", "modeld=/drive.rknn@20:1x3x8x8", "--model", "monod=/mono.rknn@10:1x3x8x8",
          "--runs", "5", "--warmup", "1"]
  assert nb.main(args, rknn_factory=FakeRKNN, clock=CLOCK) == 0     # core 0 60%, core 2 50%
  out = capsys.readouterr().out
  assert "core mask 0x1: 60.0%" in out and "core mask 0x4: 50.0%" in out   # RK3588: monod on core 2

  FakeRKNN.latency["/mono.rknn"] = 0.090                            # core 2 90%
  assert nb.main(args, rknn_factory=FakeRKNN, clock=CLOCK) == 1
  assert "OVER" in capsys.readouterr().out


def test_off_board_refuses(monkeypatch):
  monkeypatch.delenv("RKNN_PLATFORM")
  if os.path.exists("/proc/device-tree/compatible"):
    pytest.skip("running on a device-tree machine")
  assert nb.main(["--model", "modeld=/x@20:1x3x8x8"], rknn_factory=FakeRKNN, clock=CLOCK) == 2
