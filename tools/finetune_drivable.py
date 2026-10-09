#!/usr/bin/env python3
"""
Fine-tune the card's drivable-area network (TwinLiteNet+ Large) for Thai
roads and off-road: BDD100K (paved, lane lines) mixed with unstructured and
off-road data, starting from the upstream BDD100K weights.

BDD100K alone does not know unpaved, rural or off-road surfaces. Add:
  IDD (India Driving Dataset) -- unmarked rural roads, mud, mixed traffic;
      labels from IDD's createLabels.py with level1Ids: road 0 and
      "drivable fallback" 1 are drivable.
  ORFD (Off-Road Freespace Detection) -- dirt tracks, grass, gravel; its
      ground-truth masks mark free space.
  ExoPilot's own drives -- the most valuable, once labelled (e.g. in CVAT)
      as drivable / not, lane lines optional.
Check each dataset's licence before shipping weights trained on it (IDD is
for non-commercial research).

Steps:
  prepare   one labelled dataset → <out>/{images,da[,ll]}/ with 0/255 masks,
            matched by file stem. --drivable-values / --lane-values say which
            label values count (IDD level1Ids: 0,1; ORFD: 255; BDD100K
            TwinLiteNet+ masks: any non-zero, the default).
  evaluate  validation IoU per set for some weights -- run it on the
            upstream large.pth first, as the baseline to beat.
  train     fine-tune on several prepared sets at once. Sets without lane
            masks train the drivable head only (lane loss masked per
            sample). Loss as upstream: focal + Tversky; the letterbox
            padding is excluded. Saves the EMA weights with the best mean
            drivable IoU over the validation sets -- a plain state dict that
            tools/card_drivable_model.py export takes.

Usage:
  python3 tools/finetune_drivable.py prepare --images idd/leftImg8bit/train --labels idd/gtFine/train \\
      --label-glob '*_gtFine_labellevel1Ids.png' --drivable-values 0,1 --out data/idd
  python3 tools/finetune_drivable.py prepare --images orfd/training/image_data --labels orfd/training/gt_image \\
      --drivable-values 255 --out data/orfd
  python3 tools/finetune_drivable.py train --data data/bdd100k data/idd data/orfd data/exopilot \\
      --weights large.pth --epochs 30 --out build/finetune
  python3 tools/card_drivable_model.py export --weights build/finetune/best.pth
"""

from __future__ import annotations

import argparse
import copy
import math
import random
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from card_drivable_model import CARD_INPUT_HW, build_model, load_weights, upstream_repo

IMAGE_EXTS = ('.jpg', '.jpeg', '.png')


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------
def _values(text: str | None) -> set[int] | None:
  return None if text is None else {int(v) for v in text.split(',') if v.strip()}


def _binary(label: np.ndarray, values: set[int] | None) -> np.ndarray:
  if label.ndim == 3:
    label = label[..., 0]
  on = label > 0 if values is None else np.isin(label, list(values))
  return on.astype(np.uint8) * 255


def prepare(images: Path, labels: Path, label_glob: str, drivable_values: set[int] | None,
            lane_labels: Path | None, lane_glob: str, lane_values: set[int] | None, out: Path) -> None:
  def by_stem(root: Path, glob: str) -> dict[str, Path]:
    found = {}
    for f in root.rglob(glob):
      stem = f.stem
      for suffix in ('_gtFine_labellevel1Ids', '_gtFine_labelIds', '_fillcolor', '_label', '_mask'):
        stem = stem.removesuffix(suffix)
      found[stem.removesuffix('_leftImg8bit')] = f
    return found

  da = by_stem(labels, label_glob)
  ll = by_stem(lane_labels, lane_glob) if lane_labels else {}
  for sub in ('images', 'da') + (('ll',) if ll else ()):
    (out / sub).mkdir(parents=True, exist_ok=True)
  n = 0
  for img in sorted(f for f in images.rglob('*') if f.suffix.lower() in IMAGE_EXTS):
    stem = img.stem.removesuffix('_leftImg8bit')
    if stem not in da or (ll and stem not in ll):
      continue
    label = cv2.imread(str(da[stem]), cv2.IMREAD_UNCHANGED)
    if label is None:
      continue
    name = f"{n:07d}"
    cv2.imwrite(str(out / 'images' / f"{name}.jpg"), cv2.imread(str(img)))
    cv2.imwrite(str(out / 'da' / f"{name}.png"), _binary(label, drivable_values))
    if ll:
      cv2.imwrite(str(out / 'll' / f"{name}.png"), _binary(cv2.imread(str(ll[stem]), cv2.IMREAD_UNCHANGED), lane_values))
    n += 1
  print(f"prepared {n} samples → {out}" + (" (with lane masks)" if ll else " (drivable only)"))


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
def letterbox_sample(img: np.ndarray, masks: list[np.ndarray]) -> tuple[np.ndarray, list[np.ndarray], np.ndarray]:
  """Letterbox image (grey 114) and masks (0) to CARD_INPUT_HW, as inferenced does; plus the valid-pixel mask."""
  in_h, in_w = CARD_INPUT_HW
  h, w = img.shape[:2]
  gain = min(in_h / h, in_w / w)
  new_h, new_w = int(round(h * gain)), int(round(w * gain))
  top, left = (in_h - new_h) // 2, (in_w - new_w) // 2

  def place(a: np.ndarray, fill: int, interp: int) -> np.ndarray:
    out = np.full((in_h, in_w) + a.shape[2:], fill, dtype=a.dtype)
    out[top:top + new_h, left:left + new_w] = cv2.resize(a, (new_w, new_h), interpolation=interp)
    return out

  valid = np.zeros((in_h, in_w), np.uint8)
  valid[top:top + new_h, left:left + new_w] = 1
  return place(img, 114, cv2.INTER_LINEAR), [place(m, 0, cv2.INTER_NEAREST) for m in masks], valid


def augment(img: np.ndarray, masks: list[np.ndarray], rng: random.Random) -> tuple[np.ndarray, list[np.ndarray]]:
  if rng.random() < 0.5:  # horizontal flip
    img, masks = img[:, ::-1], [m[:, ::-1] for m in masks]
  if rng.random() < 0.5:  # HSV jitter, as upstream (h 0.015, s 0.7, v 0.4)
    gains = np.array([rng.uniform(-1, 1) * g + 1 for g in (0.015, 0.7, 0.4)])
    hsv = cv2.cvtColor(np.ascontiguousarray(img), cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 0] = (hsv[..., 0] * gains[0]) % 180
    hsv[..., 1:] = np.clip(hsv[..., 1:] * gains[1:], 0, 255)
    img = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
  if rng.random() < 0.5:  # random scale-crop, keeps the road's perspective
    h, w = img.shape[:2]
    s = rng.uniform(0.7, 1.0)
    ch, cw = int(h * s), int(w * s)
    y, x = rng.randint(0, h - ch), rng.randint(0, w - cw)
    img, masks = img[y:y + ch, x:x + cw], [m[y:y + ch, x:x + cw] for m in masks]
  return np.ascontiguousarray(img), [np.ascontiguousarray(m) for m in masks]


class DrivableSet:
  """A prepared set: images/, da/, optional ll/ (0/255 PNG, same stems)."""

  def __init__(self, root: Path, train: bool, val_fraction: float, seed: int = 0):
    self.root = root
    self.has_lane = (root / 'll').is_dir()
    files = sorted(f for f in (root / 'images').iterdir() if f.suffix.lower() in IMAGE_EXTS)
    random.Random(seed).shuffle(files)
    n_val = max(1, int(len(files) * val_fraction)) if len(files) > 1 else 0
    self.files = files[n_val:] if train else files[:n_val]
    self.train = train
    self.rng = random.Random(seed + (1 if train else 2))

  def __len__(self) -> int:
    return len(self.files)

  def __getitem__(self, i: int):
    import torch

    f = self.files[i]
    img = cv2.imread(str(f))
    da = cv2.imread(str(self.root / 'da' / f"{f.stem}.png"), cv2.IMREAD_GRAYSCALE)
    ll = cv2.imread(str(self.root / 'll' / f"{f.stem}.png"), cv2.IMREAD_GRAYSCALE) if self.has_lane else np.zeros_like(da)
    if self.train:
      img, (da, ll) = augment(img, [da, ll], self.rng)
    img, (da, ll), valid = letterbox_sample(img, [da, ll])
    rgb = img[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
    return (torch.from_numpy(np.ascontiguousarray(rgb)), torch.from_numpy((da > 127).astype(np.int64)),
            torch.from_numpy((ll > 127).astype(np.int64)), torch.from_numpy(valid.astype(np.bool_)),
            torch.tensor(self.has_lane))


# ---------------------------------------------------------------------------
# loss and metrics
# ---------------------------------------------------------------------------
def seg_loss(logits, target, valid, tversky_alpha: float, per_sample: bool = False):
  """Focal (alpha 0.25, gamma 2) + focal-Tversky (gamma 4/3) on 2-class logits, valid pixels only."""
  import torch
  import torch.nn.functional as F

  logp = F.log_softmax(logits, dim=1)
  logp_t = logp.gather(1, target[:, None])[:, 0]
  p_t = logp_t.exp()
  alpha_t = torch.where(target == 1, 0.25, 0.75)
  focal = -alpha_t * (1 - p_t) ** 2.0 * logp_t
  v = valid.float()
  focal = (focal * v).flatten(1).sum(1) / v.flatten(1).sum(1).clamp(min=1)

  p1 = logp[:, 1].exp() * v
  t1 = (target == 1).float() * v
  tp = (p1 * t1).flatten(1).sum(1)
  fp = (p1 * (1 - t1) * v).flatten(1).sum(1)
  fn = ((1 - p1) * t1).flatten(1).sum(1)
  tversky = (tp + 1.0) / (tp + tversky_alpha * fn + (1 - tversky_alpha) * fp + 1.0)
  loss = focal + (1 - tversky) ** (1 / 1.3333)
  return loss if per_sample else loss.mean()


def iou_counts(logits, target, valid) -> tuple[int, int]:
  pred = logits.argmax(1).bool() & valid
  tgt = target.bool() & valid
  return int((pred & tgt).sum()), int((pred | tgt).sum())


# ---------------------------------------------------------------------------
# train
# ---------------------------------------------------------------------------
def train(data: list[Path], weights: Path | None, out: Path, epochs: int, batch: int, lr: float,
          val_fraction: float, workers: int, max_steps: int | None, repo: Path | None) -> None:
  import torch
  from torch.utils.data import ConcatDataset, DataLoader

  out.mkdir(parents=True, exist_ok=True)
  device = 'cuda' if torch.cuda.is_available() else 'cpu'
  net = build_model(upstream_repo(out, repo))
  if weights:
    load_weights(net, weights)
  net.to(device)
  ema = copy.deepcopy(net).eval()
  for p in ema.parameters():
    p.requires_grad_(False)

  train_sets = [DrivableSet(d, True, val_fraction) for d in data]
  val_sets = {d.name: DrivableSet(d, False, val_fraction) for d in data}
  loader = DataLoader(ConcatDataset(train_sets), batch_size=batch, shuffle=True, num_workers=workers, drop_last=sum(len(s) for s in train_sets) > batch)
  for s in train_sets:
    print(f"{s.root.name}: {len(s)} train, {len(val_sets[s.root.name])} val, lanes: {s.has_lane}")

  opt = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=5e-4)
  steps = epochs * max(1, len(loader))
  sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: 0.5 * (1 + math.cos(math.pi * min(s, steps) / steps)))
  scaler = torch.amp.GradScaler(enabled=device == 'cuda')
  best, step = -1.0, 0

  for epoch in range(epochs):
    net.train()
    for img, da, ll, valid, has_lane in loader:
      img, da, ll, valid, has_lane = (t.to(device) for t in (img, da, ll, valid, has_lane))
      with torch.autocast(device_type=device, enabled=device == 'cuda'):
        out_da, out_ll = net(img)
      out_da, out_ll = out_da.float(), out_ll.float()
      loss_da = seg_loss(out_da, da, valid, 0.7)
      lane = seg_loss(out_ll, ll, valid, 0.9, per_sample=True) * has_lane.float()
      loss = loss_da + lane.sum() / has_lane.float().sum().clamp(min=1)
      opt.zero_grad(set_to_none=True)
      scaler.scale(loss).backward()
      scaler.step(opt)
      scaler.update()
      sched.step()
      with torch.no_grad():  # EMA, decay ramping to 0.9999 as upstream
        d = 0.9999 * (1 - math.exp(-(step + 1) / 2000))
        for e, m in zip(ema.state_dict().values(), net.state_dict().values(), strict=True):
          if e.dtype.is_floating_point:
            e.mul_(d).add_(m.detach(), alpha=1 - d)
          else:
            e.copy_(m)
      step += 1
      if step % 50 == 0:
        print(f"epoch {epoch} step {step}: loss {loss.item():.4f}")
      if max_steps and step >= max_steps:
        break

    ious = evaluate(ema, val_sets, device)
    mean_da = float(np.mean([v[0] for v in ious.values()])) if ious else 0.0
    print(f"epoch {epoch}: " + ", ".join(f"{k} da {v[0]:.3f}" + (f" lane {v[1]:.3f}" if v[1] is not None else "")
                                          for k, v in ious.items()) + f" | mean da {mean_da:.3f}")
    torch.save(ema.state_dict(), out / 'last.pth')
    if mean_da > best:
      best = mean_da
      torch.save(ema.state_dict(), out / 'best.pth')
    if max_steps and step >= max_steps:
      break
  print(f"best mean drivable IoU {best:.3f} → {out / 'best.pth'}")


def evaluate(model, val_sets: dict, device: str) -> dict[str, tuple[float, float | None]]:
  import torch
  from torch.utils.data import DataLoader

  model.eval()
  result = {}
  with torch.no_grad():
    for name, s in val_sets.items():
      if len(s) == 0:
        continue
      da_i = da_u = ll_i = ll_u = 0
      for img, da, ll, valid, _ in DataLoader(s, batch_size=4):
        out_da, out_ll = model(img.to(device))
        i, u = iou_counts(out_da.cpu(), da, valid)
        da_i, da_u = da_i + i, da_u + u
        i, u = iou_counts(out_ll.cpu(), ll, valid)
        ll_i, ll_u = ll_i + i, ll_u + u
      result[name] = (da_i / max(da_u, 1), (ll_i / max(ll_u, 1)) if s.has_lane else None)
  return result


def evaluate_weights(data: list[Path], weights: Path | None, val_fraction: float, repo: Path | None, out: Path) -> None:
  import torch

  device = 'cuda' if torch.cuda.is_available() else 'cpu'
  net = build_model(upstream_repo(out, repo))
  if weights:
    load_weights(net, weights)
  ious = evaluate(net.to(device), {d.name: DrivableSet(d, False, val_fraction) for d in data}, device)
  for name, (da, lane) in ious.items():
    print(f"{name}: drivable IoU {da:.3f}" + (f", lane IoU {lane:.3f}" if lane is not None else ""))


def main() -> None:
  ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  sub = ap.add_subparsers(dest='step', required=True)
  p = sub.add_parser('prepare')
  p.add_argument('--images', type=Path, required=True)
  p.add_argument('--labels', type=Path, required=True, help='drivable-area label images')
  p.add_argument('--label-glob', default='*.png')
  p.add_argument('--drivable-values', help='label values that are drivable (default: any non-zero)')
  p.add_argument('--lane-labels', type=Path, help='lane-line label images (optional)')
  p.add_argument('--lane-glob', default='*.png')
  p.add_argument('--lane-values', help='label values that are lane lines (default: any non-zero)')
  p.add_argument('--out', type=Path, required=True)
  t = sub.add_parser('train')
  t.add_argument('--data', type=Path, nargs='+', required=True, help='prepared sets')
  t.add_argument('--weights', type=Path, help='start from these weights (upstream large.pth)')
  t.add_argument('--out', type=Path, default=Path('build/finetune'))
  t.add_argument('--epochs', type=int, default=30)
  t.add_argument('--batch', type=int, default=16)
  t.add_argument('--lr', type=float, default=1e-4, help='fine-tune rate (upstream trains from scratch at 5e-4)')
  t.add_argument('--val-fraction', type=float, default=0.1)
  t.add_argument('--workers', type=int, default=4)
  t.add_argument('--max-steps', type=int, help='stop early (smoke test)')
  t.add_argument('--repo', type=Path, help='an existing TwinLiteNetPlus checkout')
  e = sub.add_parser('evaluate', help='validation IoU of some weights (e.g. upstream large.pth, as the baseline)')
  e.add_argument('--data', type=Path, nargs='+', required=True)
  e.add_argument('--weights', type=Path)
  e.add_argument('--val-fraction', type=float, default=0.1)
  e.add_argument('--repo', type=Path)
  e.add_argument('--out', type=Path, default=Path('build/finetune'))
  args = ap.parse_args()
  if args.step == 'prepare':
    prepare(args.images, args.labels, args.label_glob, _values(args.drivable_values),
            args.lane_labels, args.lane_glob, _values(args.lane_values), args.out)
  elif args.step == 'evaluate':
    evaluate_weights(args.data, args.weights, args.val_fraction, args.repo, args.out)
  else:
    train(args.data, args.weights, args.out, args.epochs, args.batch, args.lr,
          args.val_fraction, args.workers, args.max_steps, args.repo)


if __name__ == '__main__':
  main()
