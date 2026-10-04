import numpy as np

from nagaspilot.controls.ngp_detect import Letterbox, decode, nms


def head(rows, n_cls=80, n=8400):
  """[1, 4 + C, N] head from (cx, cy, w, h, cls, score) rows."""
  out = np.zeros((1, 4 + n_cls, n), dtype=np.float32)
  for i, (cx, cy, w, h, c, s) in enumerate(rows):
    out[0, :4, i] = (cx, cy, w, h)
    out[0, 4 + c, i] = s
  return out


def test_letterbox_roundtrip():
  lb = Letterbox.fit(1928, 1208, 640)
  assert abs(lb.scale - 640 / 1928) < 1e-9
  assert lb.pad_x == 0 and lb.pad_y > 0
  x, y = lb.to_source(np.array([lb.pad_x + 100 * lb.scale]), np.array([lb.pad_y + 50 * lb.scale]))
  assert abs(x[0] - 100) < 1e-6 and abs(y[0] - 50) < 1e-6


def test_decode_maps_back_to_source_pixels():
  lb = Letterbox.fit(1280, 720, 640)  # scale 0.5, pad_y 140
  out = head([(320, 320, 100, 80, 2, 0.9)])
  boxes = decode(out, lb, 1280, 720)
  assert len(boxes) == 1 and boxes[0].name == 'car'
  b = boxes[0]
  assert abs(b.x1 - 540) < 1e-3 and abs(b.x2 - 740) < 1e-3
  assert abs(b.y1 - 280) < 1e-3 and abs(b.y2 - 440) < 1e-3  # (300-140)/0.5, (340-140)/0.5


def test_transposed_head_and_threshold_and_class_filter():
  lb = Letterbox.fit(640, 640, 640)
  out = head([(100, 100, 40, 40, 2, 0.9), (300, 300, 40, 40, 2, 0.2), (500, 500, 40, 40, 9, 0.95)])
  assert [b.name for b in decode(out, lb, 640, 640)] == ['car']  # low score and traffic light dropped
  assert len(decode(np.transpose(out, (0, 2, 1)), lb, 640, 640)) == 1


def test_nms_suppresses_overlap_same_class_only():
  lb = Letterbox.fit(640, 640, 640)
  out = head([(100, 100, 50, 50, 2, 0.9), (104, 100, 50, 50, 2, 0.8), (104, 100, 50, 50, 0, 0.7)])
  names = sorted(b.name for b in decode(out, lb, 640, 640))
  assert names == ['car', 'person']  # the duplicate car is removed, the person overlapping it is kept


def test_nms_fused_rows():
  lb = Letterbox.fit(640, 640, 640)
  rows = np.array([[10, 10, 50, 60, 0.8, 0], [10, 10, 50, 60, 0.9, 9]], dtype=np.float32)
  boxes = decode(rows, lb, 640, 640)
  assert len(boxes) == 1 and boxes[0].name == 'person' and abs(boxes[0].h - 50) < 1e-6


def test_empty_and_garbage():
  lb = Letterbox.fit(640, 640, 640)
  assert decode(np.zeros((1, 84, 8400), np.float32), lb, 640, 640) == []
  assert decode(np.zeros((0,), np.float32), lb, 640, 640) == []
  assert nms(np.zeros((0, 4)), np.zeros(0), 0.5) == []
