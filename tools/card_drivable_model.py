#!/usr/bin/env python3
"""
Build the camera-tier card's drivable-area network for both cards.

Network: TwinLiteNet+ Large (chequanghuy/TwinLiteNetPlus, MIT, BDD100K
drivable area + lane lines), 1.94 M parameters, 384x640 -- small enough for
one context on a Hailo-8. The same ONNX is compiled for both cards, so a
camera gets the same answer whichever card is fitted. Neither vendor zoo
ships it. Output contract: system/inferenced/drivable.py.

One command on a build machine (x86-64 Linux, with Hailo's DFC and/or
DeepX's DX-COM installed for the compiles):

  python3 tools/card_drivable_model.py all --images /path/to/exopilot/frames

fetches the upstream weights (or takes --weights, e.g. a fine-tune), then
runs export, calib, check, compile-hailo and compile-dx -- each compile only
if its compiler is installed -- and copies what it built into models/.

Steps (each writes into --work, default build/card_drivable):

  fetch-weights  the upstream pretrained large.pth from TwinLiteNetPlus's
               Google Drive folder (needs gdown).

  export       TwinLiteNet+ weights (.pth state dict: the upstream pretrained
               large.pth, or a fine-tune from tools/finetune_drivable.py) →
               twinlitenet_plus_large_384x640.onnx. The graph takes RGB 0-255
               and scales itself, and its two 2-class heads are folded into
               one [drivable score, lane score] output. Needs torch + onnx;
               clones the upstream repo (pinned) for the model code.
  calib        a folder of road images → calib_384x640.npy, letterboxed as
               inferenced does at runtime. Use ExoPilot's own frames: every
               camera, day, night, rain, and the unpaved roads the fine-tune
               covers. 64-1024 frames.
  check        run the ONNX on the calibration frames through inferenced's
               own post-processing; --overlays DIR writes pictures.
  compile-hailo  ONNX → .hef for hailo8 with Hailo's Dataflow Compiler
               (hailo_sdk_client; developer-zone login, x86-64 Linux).
               --verify compares the quantized emulator with the ONNX.
  compile-dx   ONNX → .dxnn for the DX-M1M with DeepX's DX-COM (dx_com;
               private package, x86-64 Linux).

Upstream pretrained weights: the Google Drive folder in TwinLiteNetPlus's
README (large.pth). Copy the results to models/hef/ and models/dxnn/.

Usage:
  python3 tools/card_drivable_model.py export --weights large.pth
  python3 tools/card_drivable_model.py calib --images /path/to/frames
  python3 tools/card_drivable_model.py check --overlays /tmp/overlays
  python3 tools/card_drivable_model.py compile-hailo --verify
  python3 tools/card_drivable_model.py compile-dx
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import numpy as np

# inferenced's own pre/post-processing, loaded by path: it needs only numpy
# and cv2, and importing the openpilot package would need a known board.
_spec = importlib.util.spec_from_file_location(
  "drivable", Path(__file__).resolve().parents[1] / "system" / "inferenced" / "drivable.py")
assert _spec is not None and _spec.loader is not None
_drivable = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_drivable)
CARD_MODEL, CARD_INPUT_HW = _drivable.CARD_MODEL, _drivable.CARD_INPUT_HW
DRIVABLE, LANE, OTHER = _drivable.DRIVABLE, _drivable.LANE, _drivable.OTHER
drivable_map, letterbox = _drivable.drivable_map, _drivable.letterbox

UPSTREAM_REPO = "https://github.com/chequanghuy/TwinLiteNetPlus.git"
# The pretrained weights (nano/small/medium/large .pth) linked from its README
UPSTREAM_WEIGHTS_FOLDER = "https://drive.google.com/drive/folders/1EqBzUw0b17aEumZmWYrGZmbx_XJqU-vz"
UPSTREAM_COMMIT = "90f1b8695ae311d5123b05f8534b2e11e42499d2"
MODEL_CONFIG = "large"
INPUT_NAME, OUTPUT_NAME = "images", "drivable_lane"


def _paths(work: Path) -> dict[str, Path]:
  work.mkdir(parents=True, exist_ok=True)
  return {
    'repo': work / "TwinLiteNetPlus",
    'onnx': work / f"{CARD_MODEL}.onnx",
    'calib': work / "calib_384x640.npy",
    'har': work / f"{CARD_MODEL}.har",
    'hef': work / f"{CARD_MODEL}.hef",
    'dx_out': work / "dx",
    'dxnn': work / f"{CARD_MODEL}.dxnn",
  }


def upstream_repo(work: Path, repo: Path | None = None) -> Path:
  """The TwinLiteNetPlus checkout (cloned at UPSTREAM_COMMIT if not given)."""
  path = repo or _paths(work)['repo']
  if not (path / "model" / "model.py").exists():
    subprocess.run(["git", "clone", "-q", UPSTREAM_REPO, str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "checkout", "-q", UPSTREAM_COMMIT], check=True)
  return path


def build_model(repo: Path, config: str = MODEL_CONFIG):
  """TwinLiteNetPlus(config) from the upstream code."""
  sys.path.insert(0, str(repo))
  from model.model import TwinLiteNetPlus  # type: ignore[import-not-found]
  return TwinLiteNetPlus(types.SimpleNamespace(config=config))


def card_graph(net):
  """TwinLiteNet+ wrapped to the card contract: RGB 0-255 in, [drivable, lane] scores out."""
  import torch

  class CardGraph(torch.nn.Module):
    def __init__(self, net):
      super().__init__()
      self.net = net

    def forward(self, x):
      da, ll = self.net(x * (1.0 / 255.0))
      return torch.cat([da[:, 1:2] - da[:, 0:1], ll[:, 1:2] - ll[:, 0:1]], dim=1)

  return CardGraph(net).eval()


def load_weights(net, weights: Path) -> None:
  import torch

  state = torch.load(str(weights), map_location='cpu')
  if isinstance(state, dict) and 'state_dict' in state:  # a training checkpoint
    state = state.get('ema_state_dict') or state['state_dict']
  state = {k.removeprefix('module.'): v for k, v in state.items()}
  net.load_state_dict(state)


def export(work: Path, weights: Path, repo: Path | None) -> None:
  import onnx
  import onnxruntime as ort
  import torch

  p = _paths(work)
  net = build_model(upstream_repo(work, repo))
  load_weights(net, weights)
  graph = card_graph(net)
  h, w = CARD_INPUT_HW
  x = torch.rand(1, 3, h, w) * 255.0
  with torch.no_grad():
    ref = graph(x).numpy()
  # The legacy exporter runs the module in training mode and updates its
  # BatchNorm statistics, so export a copy and keep the reference above.
  torch.onnx.export(copy.deepcopy(graph), x, str(p['onnx']), opset_version=11,
                    input_names=[INPUT_NAME], output_names=[OUTPUT_NAME], dynamo=False)
  onnx.checker.check_model(onnx.load(str(p['onnx'])))
  out = ort.InferenceSession(str(p['onnx']), providers=['CPUExecutionProvider']).run(None, {INPUT_NAME: x.numpy()})[0]
  diff = float(np.abs(out - ref).max())
  print(f"wrote {p['onnx']}: output {tuple(out.shape)}, max |onnx - torch| = {diff:.2e}")
  if diff > 1e-3:
    sys.exit("export: ONNX does not match torch")


def calib(work: Path, images: Path, limit: int) -> None:
  import cv2

  files = sorted(f for f in images.rglob('*') if f.suffix.lower() in ('.jpg', '.jpeg', '.png'))[:limit]
  frames = []
  for f in files:
    bgr = cv2.imread(str(f))
    if bgr is not None:
      frames.append(letterbox(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), CARD_INPUT_HW)[0])
  if not frames:
    sys.exit(f"no images under {images}")
  data = np.stack(frames).astype(np.uint8)  # N, H, W, 3 RGB
  np.save(_paths(work)['calib'], data)
  print(f"wrote {len(data)} frames → {_paths(work)['calib']}")


def _nchw(frames_nhwc: np.ndarray) -> np.ndarray:
  return frames_nhwc.astype(np.float32).transpose(0, 3, 1, 2)


def onnx_scores(onnx_path: Path, frames_nhwc: np.ndarray) -> list[np.ndarray]:
  import onnxruntime as ort

  sess = ort.InferenceSession(str(onnx_path), providers=['CPUExecutionProvider'])
  return [sess.run(None, {INPUT_NAME: x[None]})[0] for x in _nchw(frames_nhwc)]


def _full_map(scores: np.ndarray) -> np.ndarray:
  h, w = CARD_INPUT_HW
  return drivable_map({'out': scores}, (h, w), (0, 0, h, w))


def check(work: Path, n: int, overlays: Path | None) -> None:
  import cv2

  p = _paths(work)
  data = np.load(p['calib'])[:n]
  if overlays:
    overlays.mkdir(parents=True, exist_ok=True)
  for i, scores in enumerate(onnx_scores(p['onnx'], data)):
    m = _full_map(scores)
    print(f"frame {i}: drivable {np.mean(m == DRIVABLE):6.1%}  lane {np.mean(m == LANE):5.1%}")
    if overlays:
      vis = cv2.cvtColor(data[i], cv2.COLOR_RGB2BGR)
      vis[m == DRIVABLE] = (0.5 * vis[m == DRIVABLE] + (0, 128, 0)).astype(np.uint8)
      vis[m == LANE] = (0, 0, 255)
      cv2.imwrite(str(overlays / f"{i:04d}.jpg"), vis)


def _iou(a: np.ndarray, b: np.ndarray) -> float:
  union = np.logical_or(a, b).sum()
  return float(np.logical_and(a, b).sum() / union) if union else 1.0


def compile_hailo(work: Path, verify: bool, n_verify: int) -> None:
  try:
    from hailo_sdk_client import ClientRunner, InferenceContext
  except ImportError:
    sys.exit("hailo_sdk_client not installed: Hailo's Dataflow Compiler (developer zone), x86-64 Linux")

  p = _paths(work)
  h, w = CARD_INPUT_HW
  calib_set = np.load(p['calib']).astype(np.float32)  # NHWC 0-255: the graph scales itself
  runner = ClientRunner(hw_arch='hailo8')
  runner.translate_onnx_model(str(p['onnx']), CARD_MODEL, start_node_names=[INPUT_NAME],
                              end_node_names=[OUTPUT_NAME], net_input_shapes={INPUT_NAME: [1, 3, h, w]})
  runner.load_model_script("model_optimization_flavor(optimization_level=2, compression_level=0)\n")
  runner.optimize(calib_set)
  runner.save_har(str(p['har']))

  if verify:
    ref = onnx_scores(p['onnx'], calib_set[:n_verify].astype(np.uint8))
    with runner.infer_context(InferenceContext.SDK_QUANTIZED) as ctx:
      quant = np.asarray(runner.infer(ctx, calib_set[:n_verify]))
    ious = [_iou(_full_map(ref[i]) != OTHER, _full_map(quant[i]) != OTHER) for i in range(len(ref))]
    print(f"Hailo quantized vs ONNX road IoU: mean {np.mean(ious):.3f}, min {np.min(ious):.3f}")

  p['hef'].write_bytes(runner.compile())
  print(f"wrote {p['hef']} → models/hef/{CARD_MODEL}.hef")
  print(f"contexts: hailortcli parse-hef {p['hef']} (expected 1 for TwinLiteNet+ Large)")


def compile_dx(work: Path, calibration_num: int) -> None:
  try:
    import dx_com
    import torch
    from torch.utils.data import DataLoader, Dataset
  except ImportError:
    sys.exit("dx_com not installed: DeepX's DX-COM (dx-compiler install.sh), x86-64 Linux")

  p = _paths(work)
  frames = _nchw(np.load(p['calib']))  # N, 3, H, W float 0-255: the graph scales itself

  class Frames(Dataset):
    def __len__(self):
      return len(frames)

    def __getitem__(self, i):
      return torch.from_numpy(frames[i])

  dx_com.compile(model=str(p['onnx']), output_dir=str(p['dx_out']),
                 dataloader=DataLoader(Frames(), batch_size=1, shuffle=True),
                 calibration_method="ema", calibration_num=min(calibration_num, len(frames)))
  built = sorted(p['dx_out'].glob('*.dxnn'))
  if not built:
    sys.exit(f"compile-dx: no .dxnn in {p['dx_out']}")
  built[0].replace(p['dxnn'])
  print(f"wrote {p['dxnn']} → models/dxnn/{CARD_MODEL}.dxnn")
  print("inferenced's DX backend adapts to the .dxnn's input layout (NHWC uint8 or NCHW float)")


def fetch_weights(work: Path) -> Path:
  """Upstream large.pth, downloaded once into <work>/pretrained."""
  dest = work / "pretrained"
  found = sorted(dest.rglob("large*.pth")) if dest.exists() else []
  if not found:
    try:
      import gdown
    except ImportError:
      sys.exit(f"fetch-weights needs gdown (pip install gdown), or download large.pth from {UPSTREAM_WEIGHTS_FOLDER}")
    dest.mkdir(parents=True, exist_ok=True)
    gdown.download_folder(UPSTREAM_WEIGHTS_FOLDER, output=str(dest), quiet=False)
    found = sorted(dest.rglob("large*.pth"))
  if not found:
    sys.exit(f"no large*.pth in {dest}; download it from {UPSTREAM_WEIGHTS_FOLDER}")
  print(f"weights: {found[0]}")
  return found[0]


def _has(module: str) -> bool:
  return importlib.util.find_spec(module) is not None


def run_all(work: Path, weights: Path | None, images: Path, limit: int, models_dir: Path) -> None:
  """Every step this machine can run; copies what it built into models/."""
  export(work, weights or fetch_weights(work), None)
  calib(work, images, limit)
  check(work, 16, work / "overlays")
  p, built = _paths(work), []
  if _has("hailo_sdk_client"):
    compile_hailo(work, verify=True, n_verify=16)
    built.append((p['hef'], models_dir / "hef"))
  else:
    print("skipped compile-hailo: Hailo's Dataflow Compiler (hailo_sdk_client) is not installed here")
  if _has("dx_com"):
    compile_dx(work, 500)
    built.append((p['dxnn'], models_dir / "dxnn"))
  else:
    print("skipped compile-dx: DeepX's DX-COM (dx_com) is not installed here")
  for artifact, dest in built:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / artifact.name).write_bytes(artifact.read_bytes())
    print(f"installed {dest / artifact.name}")
  print(f"overlays to look at: {work / 'overlays'}")


def main() -> None:
  ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument('step', choices=('all', 'fetch-weights', 'export', 'calib', 'check', 'compile-hailo', 'compile-dx'))
  ap.add_argument('--work', type=Path, default=Path('build/card_drivable'))
  ap.add_argument('--weights', type=Path, help='export: TwinLiteNet+ Large state dict (.pth)')
  ap.add_argument('--repo', type=Path, help='export: an existing TwinLiteNetPlus checkout')
  ap.add_argument('--images', type=Path, help='calib: folder of road images')
  ap.add_argument('--limit', type=int, default=512, help='calib: max frames')
  ap.add_argument('--overlays', type=Path, help='check: write overlay pictures here')
  ap.add_argument('--verify', action='store_true', help='compile-hailo: quantized emulator vs ONNX')
  ap.add_argument('-n', type=int, default=16, help='check / verify: frames to run')
  ap.add_argument('--calibration-num', type=int, default=500, help='compile-dx: calibration samples')
  ap.add_argument('--models-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'models',
                  help='all: where built artifacts are installed')
  args = ap.parse_args()
  if args.step == 'all':
    if args.images is None:
      ap.error('all needs --images (calibration frames)')
    run_all(args.work, args.weights, args.images, args.limit, args.models_dir)
  elif args.step == 'fetch-weights':
    fetch_weights(args.work)
  elif args.step == 'export':
    export(args.work, args.weights or fetch_weights(args.work), args.repo)
  elif args.step == 'calib':
    if args.images is None:
      ap.error('calib needs --images')
    calib(args.work, args.images, args.limit)
  elif args.step == 'check':
    check(args.work, args.n, args.overlays)
  elif args.step == 'compile-hailo':
    compile_hailo(args.work, args.verify, args.n)
  else:
    compile_dx(args.work, args.calibration_num)


if __name__ == '__main__':
  main()
