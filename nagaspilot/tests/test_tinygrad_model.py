"""Exercise the pinned compiler artifact ABI on CPU without Chestnut hardware."""
import os
from pathlib import Path
import pickle
import subprocess
import sys

import numpy as np
import pytest

from nagaspilot.runtime.tinygrad_model import TinygradModel


@pytest.fixture(scope="module")
def compiled_model(tmp_path_factory):
  onnx = pytest.importorskip("onnx")
  tinygrad = pytest.importorskip("tinygrad")
  compiler = Path(tinygrad.__file__).parents[1] / "examples/openpilot/compile_onnx.py"
  if not compiler.exists():
    pytest.skip("Pinned tinygrad checkout required")
  directory = tmp_path_factory.mktemp("compiled_model")
  source, artifact = directory / "double.onnx", directory / "double.pkl"
  def tensor(name):
    return onnx.helper.make_tensor_value_info(name, onnx.TensorProto.FLOAT, [1, 4])
  graph = onnx.helper.make_graph([onnx.helper.make_node("Add", ["input", "input"], ["outputs"])],
                                 "double", [tensor("input")], [tensor("outputs")])
  onnx.save(onnx.helper.make_model(graph, opset_imports=[onnx.helper.make_opsetid("", 17)]), source)
  subprocess.run([sys.executable, str(compiler), str(source), str(artifact)],
                 env={**os.environ, "DEV": "CPU:CLANG"}, check=True, capture_output=True)
  return TinygradModel(artifact, device="CPU:CLANG")


def test_mutated_policy_array_is_uploaded_for_each_inference(compiled_model):
  values = np.array([[1, 2, 3, 4]], dtype=np.float32)
  compiled_model.warmup()
  np.testing.assert_array_equal(compiled_model(input=values), values * 2)
  values[:] += 4
  np.testing.assert_array_equal(compiled_model(input=values), values * 2)


def test_compiled_input_contract_is_checked(compiled_model):
  with pytest.raises(ValueError, match="names"):
    compiled_model(other=np.zeros((1, 4), dtype=np.float32))
  with pytest.raises(ValueError, match="shape"):
    compiled_model(input=np.zeros((1, 3), dtype=np.float32))


def test_nonfinite_inference_fails_closed(compiled_model):
  with pytest.raises(RuntimeError, match="not finite"):
    compiled_model(input=np.full((1, 4), np.nan, dtype=np.float32))


def test_legacy_pickle_requires_rebuild(tmp_path):
  pytest.importorskip("tinygrad")
  path = tmp_path / "legacy.pkl"
  path.write_bytes(pickle.dumps({"old_model": True}))
  with pytest.raises(ValueError, match="Rebuild"):
    TinygradModel(path, device="CPU:CLANG")
