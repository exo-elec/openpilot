# Drivable-area fine-tune: Thai roads and off-road

The camera-tier card runs TwinLiteNet+ Large (see `CAMERA_ACCEL_TIER.md`).
Its upstream weights are trained on BDD100K: paved US streets and highways,
mostly with lane markings. They know nothing of dirt tracks, and little of
unmarked rural roads, mud or flooded edges. This is how to fine-tune them.

Nothing here has been trained yet. The scripts were run end to end on
synthetic data (prepare → train → export); the real run needs a GPU (the
RTX 3090 is plenty for a 1.94 M-parameter model) and the datasets below.

## Data

| Set | What it adds | Labels to use | Licence |
|-----|--------------|---------------|---------|
| BDD100K (TwinLiteNet+ layout) | paved roads, lane lines — keeps what the model has | drivable and lane masks, any non-zero | BDD100K licence |
| IDD (India Driving Dataset) | unmarked rural roads, mud, mixed traffic — closest to Thai back roads | `createLabels.py --id-type level1Ids`: road `0` and drivable fallback `1` | non-commercial research |
| ORFD | off-road free space: dirt, grass, gravel | its free-space masks (`255`) | check the release |
| ExoPilot drives | our cameras (all six), our roads, night, rain, unpaved | label drivable / not (lane lines optional), e.g. in CVAT | ours |

ExoPilot's own labelled frames matter most. BDD100K is forward dashcam only:
nothing in any public set looks like our side and rear cameras. A few hundred
labelled frames per camera kind is a sensible first target.

Check every licence before shipping weights trained on that set.

## Steps

```bash
# 1. Each set into the common layout: images/, da/, optional ll/ (0/255 PNG)
python3 tools/finetune_drivable.py prepare --images bdd100k/images/train \
    --labels bdd100k/da_labels/train --lane-labels bdd100k/ll_labels/train --out data/bdd100k
python3 tools/finetune_drivable.py prepare --images idd/leftImg8bit/train --labels idd/gtFine/train \
    --label-glob '*_gtFine_labellevel1Ids.png' --drivable-values 0,1 --out data/idd
python3 tools/finetune_drivable.py prepare --images orfd/training/image_data \
    --labels orfd/training/gt_image --drivable-values 255 --out data/orfd
python3 tools/finetune_drivable.py prepare --images exopilot/images \
    --labels exopilot/drivable --out data/exopilot

# 2. Fine-tune from the upstream weights
python3 tools/finetune_drivable.py train --data data/bdd100k data/idd data/orfd data/exopilot \
    --weights large.pth --epochs 30 --batch 16 --out build/finetune

# 3. Build both card artifacts from the result and install them into models/
python3 tools/card_drivable_model.py all --weights build/finetune/best.pth --images exopilot/images
```

## How training works

- The model sees exactly what the card will: frames letterboxed to 384×640
  with grey bars, RGB. The bars are excluded from the loss.
- Loss as upstream: focal + Tversky per head (drivable α 0.7, lane α 0.9).
- A set without lane masks trains the drivable head only; its lane loss is
  masked per sample, so off-road data cannot teach the model "no lane lines
  exist".
- Augmentation: flip, HSV jitter (upstream gains), random scale-crop.
- AdamW, learning rate 1e-4 with cosine decay (upstream trains from scratch
  at 5e-4), EMA of the weights.
- Every epoch reports drivable IoU per validation set (and lane IoU where
  there are lane labels). `best.pth` is the EMA with the best **mean**
  drivable IoU across sets, so gaining off-road cannot silently cost paved
  roads.

## Checking the result

Before replacing the card's model:

1. BDD100K validation drivable IoU should stay near the upstream 92.9 %.
2. IDD, ORFD and ExoPilot validation IoU should rise clearly over the
   upstream weights: run `finetune_drivable.py evaluate --data ... --weights
   large.pth` before training and keep the numbers as the baseline.
3. `card_drivable_model.py check --overlays` on ExoPilot night, rain and
   unpaved frames, from every camera.
4. After compiling, `compile-hailo --verify` reports the quantized network's
   road IoU against the ONNX; calibrate on the same mix of frames.
