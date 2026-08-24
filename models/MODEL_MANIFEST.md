# EOP Model Manifest

All model binaries are downloaded at install time via `download_models.sh`.
They are NOT stored in git. Verify downloads with the sha256 checksums below.

## RKNN Models (Rockchip NPU — driving_vision + driving_policy)

| File | SHA256 | Source | Notes |
|------|--------|--------|-------|
| `rknn/driving_vision.rknn` | `34da99c3b818df565d36a9729a5e186acb64174d2486ae4f75873b1a3cc8e78f` | bukapilot KA2 (`byd_sng_ka2`, git-LFS `gitlab.com/iXcess/openpilot-lfs`) | Road + wide-road vision model, 79,425,946 bytes; output `(1,1576)` |
| `rknn/driving_policy.rknn` | `988db22cbed43fd9c50a91a937a091d810b160059c010f61d80a32fc2236d708` | bukapilot KA2 (`byd_sng_ka2`, git-LFS `gitlab.com/iXcess/openpilot-lfs`) | Policy model, 16,441,036 bytes; output `(1,1000)` |

These are bukapilot's proven KA2 (RK3588) driving pair. They are coupled to
bukapilot's input method, which EOP follows: all inputs cast to float16,
vision inputs fed NHWC by default (`RKNN_ENFORCE_VISION_NCHW=1` to override),
and the big_img affine (`RKNN_NHWC_BIGIMG_AFFINE_*`). Known caveat inherited
from bukapilot: their RKNN vision graph has an unresolved hidden-state
collapse on ~10% of frames; the `RKNN_BLIP_GUARD` lateral mitigation in modeld
ships enabled by default. See `docs/eop/05_Features/CHESTNUT_EGPU_ADOPTION.md`.

## Hailo HEF Models (Hailo-8 NPU)

| File | SHA256 | Source | Notes |
|------|--------|--------|-------|
| `hef/yolov8n.hef` | `7103302bf5f2bac163f60b3f9436684e85f762405749020f89249101bd606f49` | Hailo Model Zoo (`hailo8/yolov8n.hef`; cross-verified against `../visionpilot`'s copy) | YOLO v8 nano — monod/sided/reard, 5,155,491 bytes |

`scrfd_2.5g.hef` (face detection for `driverd`'s DMS pipeline) was fetched
and then removed — this hardware has no driver-facing camera, and
`driverd`'s face-DMS is VisionPilot-only anyway (see `models/README.md`).
Re-add from Hailo Model Zoo `hailo8/scrfd_2.5g.hef` if one is ever fitted.

## ONNX Models (Chestnut big model — plus pending eGPU-shadow placeholders)

The only file actually stored in `onnx/` today is Chestnut's big-model ONNX.
This dev PC does not run the driving model via ONNX Runtime, so the earlier
dev-PC RKNN substitute (bukapilot's `driving_vision.onnx`/`driving_policy.onnx`)
and the reference-only Autoware vision suite (`egolanes_lite_int8`,
`scene3d_lite_int8`, `sceneseg_lite_int8`, `autosteer_full_int8`,
`autospeed_full_int8`) were fetched and then removed. They're still available
from `../visionpilot` and `../bukapilot` if that capability is needed again —
see git history for the exact hashes. The `yolo_side`/`yolo_rear`/`seg_*` rows
below are unrelated, pre-existing `.onnx`-format placeholders for the eGPU
camera-shadow feature (`inferenced.py`'s `MODEL_REGISTRY`) — not yet exported,
kept as the documented target path for when they are.

| File | SHA256 | Source | Notes |
|------|--------|--------|-------|
| `onnx/big_driving_supercombo.onnx` | `10926f2c0911821ca0e72439c1c3bf3ec11f0a08789aa14b7ee8f25379b2afa4` | `commaai/openpilot@master` (`b7c333cf3fee117779515c9ebfd7b2beb164fa81`, via `../ext_gpu/openpilot-upstream`), cross-verified byte-identical against `sunnypilot@master` (`bf74ce544738189693dbd07266a46e63465710c1`) | Upstream Chestnut big model, 1,753,235,978 bytes. **Not currently loaded by anything** — `ChestnutDrivingRunner`/`factory.py` is deliberately fail-closed until the tinygrad-JIT-compiled artifact, replay/HIL/hardware-soak gates, and closed-course validation are all in place (see `task.md`, `docs/eop/05_Features/CHESTNUT_EGPU_ADOPTION.md`). **Hash/size differ from the previously audited value** (`a501760a9d1...`, 1,757,355,221 bytes) — comma appears to have shipped a model update since that audit (sunnypilot's commit touching this same file is titled "Be Right Here Model 🏃 (big)", 2026-08-01); this is expected drift for a live upstream artifact, not corruption. Re-verify against `commaai/openpilot@master` before any future compile step. |
| `onnx/yolo_side.onnx` | *(set after verified export)* | Ultralytics YOLOv8n export | Side-left/right eGPU shadow model; replace independently after side-camera training |
| `onnx/yolo_rear.onnx` | *(set after verified export)* | Ultralytics YOLOv8n export | Rear eGPU shadow model; replace independently after rear-camera training |
| `onnx/seg_side.onnx` | *(set after verified export)* | viewpoint-specific segmentation | Side-camera eGPU segmentation shadow (independent class map); no trained artifact exists anywhere yet |
| `onnx/seg_rear.onnx` | *(set after verified export)* | viewpoint-specific segmentation | Rear-camera eGPU segmentation shadow (independent class map); no trained artifact exists anywhere yet |
| `onnx/seg_front_road.onnx` | *(set after verified export)* | viewpoint-specific segmentation | Front-road eGPU segmentation shadow vs. PP-LiteSeg; no trained artifact exists anywhere yet |
| `onnx/seg_front_wide.onnx` | *(set after verified export)* | viewpoint-specific segmentation | Front-wide eGPU segmentation shadow vs. SceneSeg; no trained artifact exists anywhere yet |

`domainseg_full_int8.onnx` and `dmonitoring_model*.onnx` also exist in
`../visionpilot/models/onnx/` but have no corresponding EOP entry point today
(no roadwork-segmentation or dmonitoring-via-ONNX consumer on this branch) —
not pulled in; add an entry here first if a consumer is built.

## Folder naming

Folders are named by file format, not backend brand, and this must stay
consistent — never mix the two axes:

| Folder | Format | Backend |
|---|---|---|
| `rknn/` | `.rknn` | `BackendType.NPU` (Rockchip) |
| `hef/` | `.hef` | `BackendType.HAILO_8` |
| `onnx/` | `.onnx` | Chestnut's big model (not currently loaded by anything) plus pending eGPU-shadow placeholders; `BackendType.ONNX` (dev-PC/CPU fallback) exists in code but has no driving-model file here today |
| `axmodel/` | `.axmodel` | reserved — no AX-M1/AXCL backend implemented yet; intended tier is `VOICE_INFERENCE` (local LLM + whisper voice encoder, per `compute.py`'s `WorkloadClass`), not camera inference. Official source: [github.com/AXERA-TECH](https://github.com/AXERA-TECH) (model weights also on Hugging Face); LLM-on-AX650 specifically: [AXERA-TECH/ax-llm](https://github.com/AXERA-TECH/ax-llm) |
| `dxnn/` | `.dxnn` | `BackendType.DX_M1` (DeepX) — reserved, no models registered yet; intended tier is `CAMERA_INFERENCE` alongside `hef/` (side/rear/etc.), interchangeable with Hailo-8. Official source: [github.com/DEEPX-AI/dx-modelzoo](https://github.com/DEEPX-AI/dx-modelzoo) (354 pre-compiled models — detection, segmentation, classification, face recognition) |

Priority ordering across tiers: `rknn/` (`SAFETY_INFERENCE`, the driving model)
is always authoritative and always loaded. `hef/`/`dxnn/` (`CAMERA_INFERENCE` —
side, rear, and similar smaller per-camera models) run first/cheaper. `onnx/`'s
Chestnut big model (`egpu`) is the heaviest, optional, shadow-only tier — see
"Not currently loaded by anything" above. `axmodel/` (`VOICE_INFERENCE` —
local LLM, whisper voice encoder) is a separate tier from all of the above;
nothing is stored there yet — a `.hef`-compiled whisper build exists in
`../visionpilot` but was deliberately not pulled in here since it's the wrong
tier for this folder's purpose (camera inference); wait for a real
`.axmodel`-compiled build instead of storing a mismatched-format placeholder.

**Reconciled against `VOICE_PIPELINE.md`'s cloud-voice decision (2026-08-24):**
`VOICE_PIPELINE.md` (2026-08-14, Design/Implementation both complete) mandates
no local STT/TTS, reasoned partly around "no NPU/GPU contention with driving
models" — that reasoning does not block `axmodel/`'s `VOICE_INFERENCE` tier.
AX-M1/AXCL is a separate PCIe-attached NPU with its own on-device DRAM; it
shares neither the eGPU's USB link nor the RK3588 driving-model NPU, so a
future local voice/LLM build there would not contend with ADAS inference.
The eGPU (ASM2464PD, USB) stays strictly ADAS/camera-only — see
`docs/eop/05_Features/EGPU_CAMERA_SHADOW.md` and
`docs/eop/05_Features/CHESTNUT_EGPU_ADOPTION.md`. If local voice is ever
actually built on AX-M1, `VOICE_PIPELINE.md`'s Safety Design section still
needs its own explicit update (attack surface / auditability / centralized
updates are independent of the hardware-contention argument) — that decision
is not made by this folder reservation alone.

## Adding New Models

1. Download and place the file in the appropriate subdirectory.
2. Run `sha256sum <file>` and record the checksum here.
3. Add an entry to `download_models.sh` so CI and fresh installs can fetch it.
