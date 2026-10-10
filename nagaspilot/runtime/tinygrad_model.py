"""Compiled ONNX artifact adapter for the official Chestnut tinygrad ABI."""
import numpy as np


class TinygradModel:
  def __init__(self, path, device=None):
    from tinygrad import Tensor, Context, Device
    from examples.openpilot.helpers import load_pickle
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
