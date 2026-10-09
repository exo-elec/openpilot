# Camera-tier card (Hailo-8 / DX-M1M): drivable area for every camera

**Status (2026-09-27):** code complete on `dev/01M`, `dev/EOP10` and `dev/02M`
(board-agnostic). Not yet run on hardware; every rate and budget below is a
plan from vendor figures, to be replaced by bench measurements.

**Done (2026-09-28):** the card output feeds gridd's BEV fusion and cost
layer as per-camera drivable maps (`drivableBev`). segd is the card's only
client and feeds it by what the drive needs (road 10 Hz always; wide, tele,
side and rear by speed, blinkers, blind-spot flags and detections; an idle
tick runs no job), at most 20 jobs/s. See
`docs/eop/03_Software/Architecture/PERCEPTION_FUSION.md`.

## The split

| Hardware | Where | Runs | If it fails |
|----------|-------|------|-------------|
| **RKNN NPU** | inside the SoC | driving model; **YOLOv8 object detection for every camera** (road, 02M telephoto, side left/right, rear), plus traffic lights on the road camera | nothing else can replace it — which is why detection lives here |
| **Hailo-8 or DX-M1M** | M.2 card on PCIe | **drivable-area segmentation for every camera**: TwinLiteNet+ Large (drivable area + lane lines), the same network on both cards | segmentation **degrades**: it stops, gridd runs without a road mask and sets `gridStatus.segmentationDegraded`; nothing takes over; detection is unaffected |
| **USB eGPU** | external, USB | openpilot's Chestnut driving model **only** | one-way failover to the RKNN driving model |

Why this way round:

- **Detection is what BSD, RCTA and the front object list are built from**,
  so it runs where it cannot disappear. RKNN is on the die; a card can be
  unfitted, unseated or drop off the bus.
- **A card with no DRAM does best with one network, all the time.** The
  Hailo-8 keeps its weights on-chip; several different networks mean context
  switches over PCIe. Segmentation for every camera is one network (the same
  artifact for every camera) running constantly — exactly that.
- **The two cards are interchangeable, chosen on price.** Both run the same
  network from the same weights, so consumers get the same answer whichever
  card is fitted.
- **Drivable area is what the consumers ask for.** gridd wants a road mask,
  `rcd` wants "is there road ahead / is its edge in view". The Cityscapes
  networks used before (STDC1, BiSeNetV2) gave 19 classes of which three bits
  were used, were trained on dry daytime German city streets, and needed a
  different network per card. TwinLiteNet+ is trained on BDD100K (day, night,
  rain), answers the question directly, and is small enough to fine-tune for
  Thai and off-road surfaces.

## The network: TwinLiteNet+ Large, 384×640

| | |
|---|---|
| Model | TwinLiteNet+ Large (chequanghuy/TwinLiteNetPlus, MIT): drivable area + lane lines, BDD100K |
| Size | 1.94 M parameters, 17.6 GFLOPs — small enough for **one context** on a Hailo-8 |
| Accuracy (BDD100K, authors') | drivable mIoU 92.9 %, lane IoU 34.2 % (YOLOPv2: 93.2 %, 27.2 %) |
| Build | `tools/card_drivable_model.py all --images <frames>`: upstream weights → one ONNX → HEF (Hailo DFC) and `.dxnn` (DX-COM), installed into `models/`. Neither vendor zoo ships it |
| Contract | `system/inferenced/drivable.py` |

Why not YOLOPv2, which DeepX's zoo does ship: 24.8 M parameters even without
its detection heads, so multi-context on the Hailo-8, and too big to fine-tune
cheaply for off-road. Its drivable-area accuracy is only 0.3 points higher.

The export shapes the graph for the cards: it takes RGB 0–255 and scales
itself, and folds the two 2-class heads into **one** output,
`[drivable score, lane score]` (each head's class-1 minus class-0 logit, > 0
means yes). One output means no compiler can rename or reorder heads.
Clients send a 540×960 RGB frame; `inferenced` letterboxes it to 384×640
(grey 114 bars, 12 rows top and bottom, exactly as TwinLiteNet+ trains) and
returns a uint8 map at 540×960: `OTHER` 0, `DRIVABLE` 1, `LANE` 2. DX-COM may
or may not bake the layout change into the `.dxnn`; the DX backend reads the
compiled input shape and feeds NHWC uint8 or NCHW float to match.

Verified here: the model code builds at 1.944 M parameters with two
2-channel heads at 384×640; the ONNX matches PyTorch to 2e-8; the build tool,
calibration set, ONNX check and the fine-tune loop run end to end (random
weights — the pretrained `large.pth` is on Google Drive, which this dev
environment cannot reach). Not yet run: the two vendor compiles (they need
Hailo's and DeepX's compilers), and checks of the real weights. The model's
attention block uses MatMul/Softmax, which both compilers list as supported
but which is the first thing to watch in their logs.

## Off-road and Thai roads

BDD100K is paved US roads with lane markings; it has no dirt tracks, and few
unmarked rural roads. `tools/finetune_drivable.py` fine-tunes from the
BDD100K weights on a mix — see `DRIVABLE_FINETUNE.md`:

- **BDD100K** keeps paved-road and lane-line skill.
- **IDD** (India Driving Dataset): unmarked rural roads, mud, mixed traffic.
- **ORFD**: off-road free space (dirt, grass, gravel).
- **ExoPilot's own drives**, labelled — every camera, including side and rear.

Sets without lane labels train the drivable head only. The best checkpoint
(mean drivable IoU over every validation set) goes straight into the export.

## DX-M1M, not DX-M1

ExoPilot fits the **DX-M1M** M.2 module: the same DX-M1 die (25 TOPS INT8) on
an **M.2 2242** board with **2 GB integrated LPDDR4x**, **PCIe Gen3 x2**
(Gen1/2 supported), **3 W typical**, **−40 to 85 °C industrial**, 1 Gbit QSPI
flash, heatsink optional. (Earlier notes here said 4 GB LPDDR5 and x4: that is
the larger DX-M1 M.2 2280 card.) What that means:

- Gen3 x2 matches 01M's M.2 M-key socket exactly; on 02M's PCIe 2.1 x1 slot it
  links at Gen2 x1.
- 2242 is shorter than the 2280 socket: the carrier needs a 42 mm standoff
  position (mechanical check on both boards).
- 2 GB is plenty for one network resident.
- 3 W typical and an industrial temperature range suit an in-car module.

## Who runs what

| Daemon | RKNN (core) | Card |
|--------|-------------|------|
| `monod` | YOLOv8 road 20 Hz; tele 20 Hz on 02M (last core: 01M 2, 02M 1) | — |
| `sided` | YOLOv8 side left and right, 20 Hz each (core 1) | — |
| `reard` | YOLOv8 rear 20 Hz (core 1) | — |
| `gridd` | — | none (fuses segd's `drivableBev`; no maps → degraded) |
| `segd` | — | by demand, ≤ 20 jobs/s: `seg_road` 10 Hz; `seg_wide` 2–4 Hz; `seg_tele` 4 Hz above 15 m/s (02M); `seg_side` 4–6 Hz per side with a blinker / blind-spot flag / side detection, else off; `seg_rear` 10 Hz in reverse, 4 Hz with a rear detection, else off; publishes `monoSegments` and `drivableBev` |

Core 1 is the core that SceneSeg/PP-LiteSeg used to occupy on both boards;
moving segmentation to the card is what frees it for side/rear detection.
Neither runs on RKNN any more.

## When the card fails: degrade, no fallback

A card failure is not covered by anything else. That is a deliberate choice
(2026-09-27): segmentation is an added layer, and a substitute path is more
code to validate for a feature that is allowed to go away.

- **Card missing at start, or drops out:** `segd` idles; gridd gets no
  camera maps for 1 s. gridd sets `gridStatus.segmentationDegraded` and logs the change
  once each way. It is not `gridStatus.fault` — that disables openpilot.
  surfaced's drivable-area costmap needs no card and carries on.
- **Card cannot load its model:** `inferenced` marks that card degraded. Its
  model ids stay on it and answer "not available" until restart. No other
  model, no moving to the other card (01M). Each card has exactly one
  artifact.

The eGPU is different and unchanged: Chestnut keeps openpilot's one-way switch
to the RKNN driving model.

`segd` publishes one `MonoSegment` per camera — no schema change, now
computed from the drivable map (lane lines count as road): `hasRoad` = road
covers ≥ 5 % of the lower half; `hasDrivable` = ≥ 30 % of the lower-centre
third; `hasEdge` = road present and ≥ 10 % of the lower half off it (the
road's boundary is in view). `rcd` reads the `road` entry. The same maps go
to gridd as `drivableBev` (projected onto the road through each camera's
calibration, `segd/drivable_bev.py`), fused into gridd's cost layer
(`gridd/drivable_fusion.py`) and used for `stereoGround.hasSegmentation`.

## Budget (plan)

| | Hailo-8 | DX-M1M |
|---|---|---|
| Card jobs | ≤ 20/s (segd only, by demand) | ≤ 20/s |
| Throughput | to bench. Single context at 17.6 GFLOPs: expect well above the 20 jobs/s needed (for scale, the single-context yolov8s, 28.6 GFLOPs, runs 491 fps in the Hailo zoo) | to bench; 2 GB DRAM, no context switching |
| PCIe traffic (model input, UINT8 384×640×3) | 0.74 MB × 20 ≈ 15 MB/s at most | ≈ 15 MB/s |
| 01M link | PCIe 3.0 x2 — ample | Gen3 x2 — ample |
| 02M link | PCIe 2.1 x1 — ~5 % | Gen2 x1 — ~5 % |

The smaller input is a side benefit: STDC1 at 1024×1920 moved 5.9 MB a job.
With a single-context network the card should have room to raise segd's
per-camera rates; decide that from the bench numbers.

| RKNN | 01M (3 × 2 TOPS) | 02M (2 × 3 TOPS) |
|---|---|---|
| core 0 | driving vision | driving vision + policy |
| core 1 | side 2 × 20 + rear 20 = 60 inf/s | road 20 + side 2 × 20 + rear 20 + tele 20 = 100 inf/s |
| core 2 | road YOLOv8 20 inf/s + policy | — |

Every camera is detected at the 20 Hz foundation rate; nothing is
rate-reduced. On a board without the telephoto, 02M core 1 carries 80/s.

This budget is not shown to fit yet. Rockchip's rknn_model_zoo puts yolov8n
at 640×640 on RK3588 at around 50 fps, about 20 ms a frame, so 60/s may already
be more than one core, and 100/s on 02M certainly is. If the bench confirms
that, the remedy is to spread detection across cores, never to lower a
camera's rate:

- 01M: rear moves to core 2 beside road, leaving core 1 with side at 40/s.
- 02M: monod's road and telephoto move to core 0 beside the driving model.

The driving model's latency on core 0 has to be re-checked after any such move.

## Bench before trusting any of this

1. TwinLiteNet+ latency per frame and sustained throughput on each card; the
   HEF's context count (`hailortcli parse-hef`, expected 1).
2. Drivable-area quality on ExoPilot's own footage — Thai roads, night, rain,
   unpaved roads, and the side/rear views, which BDD100K (forward dashcam)
   never saw — before and after the fine-tune. Calibrate both compiles on
   those frames.
3. 02M PCIe x1 throughput under that load.
4. RKNN yolov8n per-core throughput on both SoCs at 20 Hz per camera (01M core 1: 60/s; 02M core 1: 100/s).
5. Card temperature under sustained load; the DX-M1M with and without heatsink.
6. Pull the card while running: segd idles, gridd reports
   `segmentationDegraded` with no fault and no disengagement, detection
   carries on.
7. DX-M1M in the 2280 socket: standoff, and link training at Gen3 x2.

## Files

- `selfdrive/sided/yolo_detector.py` — `YoloDetector` (RKNN, core mask), shared YOLO decode
- `selfdrive/sided/sided.py`, `selfdrive/reard/reard.py`, `selfdrive/monod/monod.py` — detection on RKNN
- `selfdrive/segd/card_segmenter.py`, `selfdrive/segd/segd.py` — card segmentation, `monoSegments`, `drivableBev`
- `selfdrive/segd/schedule.py` — which camera the card gets next (by demand)
- `selfdrive/segd/drivable_bev.py` — card map → BEV grid through each camera's pose
- `selfdrive/gridd/drivable_fusion.py` — per-camera maps fused into the cost layer; degraded without them
- `system/inferenced/drivable.py` — letterbox and `[drivable, lane]` scores → OTHER/DRIVABLE/LANE map
- `system/inferenced/inferenced.py` — `CAMERA_ACCEL_MODELS` (seg ids, one artifact per card)
- `system/inferenced/deepx_dxnn.py` — adapts to the `.dxnn`'s input layout
- `tools/card_drivable_model.py` — builds the HEF and the `.dxnn` from one ONNX
- `tools/finetune_drivable.py` — Thai / off-road fine-tune
- `system/inferenced/{hailo_hef,deepx_dxnn}.py` — UINT8 input; DX-M1M facts
- `selfdrive/controls/lib/rcd.py` — reads the `road` segment
- Tests: `selfdrive/sided/tests/test_yolo_detector.py`, `selfdrive/monod/tests/test_front_detection.py`,
  `selfdrive/segd/tests/test_segd.py`, `system/inferenced/tests/test_camera_accel_routing.py`

## Sources

- TwinLiteNet+ (code, weights, figures): <https://github.com/chequanghuy/TwinLiteNetPlus>
- YOLOPv2 (the alternative considered): <https://github.com/CAIC-AD/YOLOPv2>
- DX-COM (`dx_com.compile`, custom DataLoader, input-layout bake-in): <https://github.com/DEEPX-AI/dx-compiler>
- Hailo Dataflow Compiler: <https://hailo.ai/developer-zone/>
- DX-M1M module (DEEPX product page: 25 TOPS, PCIe Gen3 x2, 2 GB LPDDR4x, 3 W):
  <https://deepx.ai/products/dx-m1/>
