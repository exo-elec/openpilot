#!/usr/bin/env python3
"""Required real-GPU numerical diagnostic; unavailable GPU is a failure.

Run directly on the target device after Exopilot's OpenCL smoke check. Tests
cover shifted textures, repeated-buffer use and rejection of flat images.
These small fixtures do not establish production accuracy or frame latency.
"""
import importlib.util
import os
from pathlib import Path
import unittest  # noqa: TID251


def main() -> int:
  os.environ['EOP_GPU_TESTS'] = '1'
  spec = importlib.util.spec_from_file_location('stereo_target_tests', Path(__file__).with_name('test_stereo_runtime.py'))
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  suite = unittest.defaultTestLoader.loadTestsFromTestCase(module.TestRealGPUStereo)
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
  raise SystemExit(main())
