"""Compiled ONNX artifact adapter for the official big-model tinygrad ABI."""
import importlib.util
from pathlib import Path

import numpy as np


def _openpilot_helpers():
  """tinygrad_repo/examples/openpilot/helpers.py, loaded from beside the tinygrad package.

  `examples` is not an importable package on a device: the launcher puts only the openpilot root on PYTHONPATH and
  `tinygrad` is reached through the root symlink to tinygrad_repo/tinygrad, so `from examples.openpilot.helpers import`
  raised ModuleNotFoundError and modeld could not load its compiled models. helpers.py needs only tinygrad and the
  standard library.
  """
  import tinygrad
  path = Path(tinygrad.__file__).resolve().parents[1] / 'examples' / 'openpilot' / 'helpers.py'
  spec = importlib.util.spec_from_file_location('tinygrad_openpilot_helpers', path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


class TinygradModel:
  def __init__(self, path, device=None):
    from tinygrad import Tensor, Context, Device
    load_pickle = _openpilot_helpers().load_pickle
    self.Tensor = Tensor
    self.device = device or Device.DEFAULT
    with Context(DEV=self.device):
      artifact = load_pickle(path)
    if not isinstance(artifact, dict) or not {'run', 'input_specs', 'output_specs'} <= artifact.keys():
      raise ValueError('Rebuild this model with the pinned examples/openpilot/compile_onnx.py')
    self.run = artifact['run']
    self.input_specs = artifact['input_specs']
    with Context(DEV=self.device):
      self.output_buffers = {name: Tensor(np.zeros(shape, dtype=dtype), device=target).realize()
                             for name, (shape, dtype, target) in artifact['output_specs'].items()}
    if not self.output_buffers:
      raise ValueError('Compiled model has no output tensors')
    self.output_name = 'outputs' if 'outputs' in self.output_buffers else next(iter(self.output_buffers))
    if len(self.output_buffers) != 1 and self.output_name != 'outputs':
      raise ValueError('Compiled model has ambiguous output tensors')

  def __call__(self, **inputs):
    from tinygrad import Context
    if inputs.keys() != self.input_specs.keys():
      raise ValueError('Model input names differ from compiled artifact')
    with Context(DEV=self.device):
      tensors = {}
      for name, value in inputs.items():
        shape, dtype, device = self.input_specs[name]
        # Policy history arrays mutate each frame; use a fresh device copy.
        tensor = value if isinstance(value, self.Tensor) else self.Tensor(np.array(value, dtype=dtype, copy=True), device='NPY')
        if tuple(tensor.shape) != tuple(shape):
          raise ValueError(f'Model input shape mismatch: {name}')
        tensors[name] = tensor.to(device).realize()
      self.run(output_buffers=self.output_buffers, **tensors)
    output = self.output_buffers[self.output_name].numpy()
    if not np.all(np.isfinite(output)):
      raise RuntimeError('Model output is not finite')
    return output

  def warmup(self):
    inputs = {name: np.zeros(shape, dtype=dtype) for name, (shape, dtype, _) in self.input_specs.items()}
    self(**inputs)
