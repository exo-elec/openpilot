import base64
import json
import pickle

import pytest

onnx = pytest.importorskip('onnx')

from nagaspilot.bigmodel import allowlist
from nagaspilot.bigmodel.spec import spec_from_onnx
from nagaspilot.bigmodel.tools import allow_model

SHAPES = {'img': (1, 12, 128, 256), 'big_img': (1, 12, 128, 256), 'desire_pulse': (1, 33, 8), 'traffic_convention': (1, 2),
          'action_t': (1, 2), 'features_buffer': (1, 32, 2, 4)}


def write_model(path, shapes=SHAPES, slices=None, out=28):
  from onnx import TensorProto, helper
  inputs = [helper.make_tensor_value_info(k, TensorProto.FLOAT, list(v)) for k, v in shapes.items()]
  outputs = [helper.make_tensor_value_info('outputs', TensorProto.FLOAT, [1, out])]
  node = helper.make_node('Constant', [], ['outputs'], value=helper.make_tensor('c', TensorProto.FLOAT, [1, out], [0.0] * out))
  model = helper.make_model(helper.make_graph([node], 'g', inputs, outputs))
  slices = slices if slices is not None else {'plan': slice(0, 2), 'hidden_state': slice(2, 10), 'meta': slice(10, 28)}
  props = {'output_slices': base64.encodebytes(pickle.dumps(slices)).decode(), 'model_checkpoint': 'test'}
  for k, v in props.items():
    model.metadata_props.add(key=k, value=v)
  onnx.save(model, str(path))
  return path


def test_the_spec_is_read_from_the_graph_and_becomes_an_entry(tmp_path):
  m = write_model(tmp_path / 'm.onnx')
  spec = spec_from_onnx(str(m))
  assert spec.feat_dim == 8 and spec.hidden_range == (2, 10)
  entry = allow_model.entry_for(spec, 'test model', 'route x replay')
  assert entry['name'] == 'test model' and entry['evidence'] == 'route x replay' and entry['sha256'] == spec.sha256
  assert allowlist.AllowList({spec.sha256: entry}).check(spec) == 'test model'


def test_the_tool_prints_and_adds_and_the_comma_then_allows_the_model(tmp_path, monkeypatch, capsys):
  m = write_model(tmp_path / 'm.onnx')
  lst = tmp_path / 'allowed.json'
  lst.write_text('{"models": []}')
  monkeypatch.setattr(allow_model, 'DEFAULT_PATH', lst)
  assert allow_model.main([str(m), '--name', 'n', '--evidence', 'e']) == 0
  assert json.loads(capsys.readouterr().out)['name'] == 'n' and json.loads(lst.read_text())['models'] == []
  assert allow_model.main([str(m), '--name', 'n', '--evidence', 'e', '--add']) == 0
  assert allow_model.main([str(m), '--name', 'n2', '--evidence', 'e2', '--add']) == 0      # same model: replaced, not duplicated
  models = json.loads(lst.read_text())['models']
  assert len(models) == 1 and models[0]['name'] == 'n2'
  spec = spec_from_onnx(str(m))
  assert allowlist.AllowList.load(lst).check(spec) == 'n2'


def test_the_tool_refuses_what_the_comma_cannot_serve(tmp_path, capsys):
  other_frames = {**SHAPES, 'img': (1, 12, 64, 256), 'big_img': (1, 12, 64, 256)}
  assert allow_model.main([str(write_model(tmp_path / 'a.onnx', other_frames)), '--name', 'n', '--evidence', 'e']) == 2
  assert 'warps' in capsys.readouterr().err
  no_hidden = write_model(tmp_path / 'b.onnx', slices={'plan': slice(0, 2)})
  assert allow_model.main([str(no_hidden), '--name', 'n', '--evidence', 'e']) == 2
  overlap = write_model(tmp_path / 'c.onnx', slices={'hidden_state': slice(2, 10), 'bad': slice(8, 12)})
  assert allow_model.main([str(overlap), '--name', 'n', '--evidence', 'e']) == 2
  assert 'overlaps' in capsys.readouterr().err


def test_evidence_is_required(tmp_path):
  with pytest.raises(SystemExit):
    allow_model.main([str(write_model(tmp_path / 'm.onnx')), '--name', 'n'])
