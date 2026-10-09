import json
import numpy as np


class NNTorqueModel:
  def __init__(self, model_path: str):
    with open(model_path) as f:
      data = json.load(f)

    self.input_mean = np.array(data['input_mean'], dtype=np.float32).flatten()
    self.input_std = np.array(data['input_std'], dtype=np.float32).flatten()
    self.input_vars = data['input_vars']
    self.input_size = data['input_size']
    self.output_size = data['output_size']

    self.layers = []
    for layer in data['layers']:
      keys = [k for k in layer.keys() if k.endswith('_W')]
      if not keys:
        continue
      w_key = keys[0]
      b_key = w_key.replace('_W', '_b')
      W = np.array(layer[w_key], dtype=np.float32)
      b = np.array(layer[b_key], dtype=np.float32).flatten()
      act = layer.get('activation', 'identity')
      if act == 'σ':
        act = 'sigmoid'
      self.layers.append((W, b, act))

    self._sanity_check()

  def _sanity_check(self):
    assert len(self.input_mean) == self.input_size, "input_mean size mismatch"
    assert len(self.input_std) == self.input_size, "input_std size mismatch"
    assert all(np.isfinite(self.input_mean)), "input_mean has NaN/Inf"
    assert all(np.isfinite(self.input_std)), "input_std has NaN/Inf"
    assert all(np.isfinite(W).all() and np.isfinite(b).all() for W, b, _ in self.layers), "layer weights have NaN/Inf"

  def _activation(self, x: np.ndarray, name: str) -> np.ndarray:
    if name == 'sigmoid':
      return 1.0 / (1.0 + np.exp(-x))
    return x

  def evaluate(self, features: list[float]) -> float:
    x = (np.array(features, dtype=np.float32) - self.input_mean) / self.input_std
    for W, b, act in self.layers:
      x = np.dot(x, W.T) + b
      x = self._activation(x, act)
    return float(x[0])
