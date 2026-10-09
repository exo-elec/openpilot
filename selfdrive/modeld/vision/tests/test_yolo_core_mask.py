from openpilot.selfdrive.modeld.vision.yolo_rknn import YoloRKNNDetector


def parse(spec):
  return YoloRKNNDetector._parse_core_mask(None, spec)


def test_int_mask_passes_through():
  # rknn_platform.get_core_mask() returns RKNN masks; gridd passes them as-is.
  assert parse(2) == 2
  assert parse(1) == 1


def test_string_specs_unchanged():
  assert parse("0") == 0x01
  assert parse("1") == 0x02
  assert parse("0,1") == 0x03
  assert parse("all") == 0xFF
