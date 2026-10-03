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
| `hef/twinlitenet_plus_large_384x640.hef` | *(set after first build)* | built by `tools/card_drivable_model.py` from TwinLiteNetPlus `large.pth` (chequanghuy/TwinLiteNetPlus @ 90f1b86, MIT) or a fine-tune | TwinLiteNet+ Large, drivable area + lane lines, 384x640, 1.94 M params, one context — camera-tier segmentation, every camera (`seg_*`); the same ONNX as the DX-M1M's |

`hef/yolov8n.hef` (`7103302b…`, 5,155,491 bytes) is no longer used: object
detection runs on RKNN (`rknn/yolo_640_<soc>.rknn`) for every camera (2026-09-27).

No face model (SCRFD): ExoPilot has no driver-facing camera, and there is no
camera DMS. The card runs segmentation only.

## ONNX Models (Chestnut big model — the eGPU's only model)

The only file actually stored in `onnx/` today is Chestnut's big-model ONNX.
This dev PC does not run the driving model via ONNX Runtime, so the earlier
dev-PC RKNN substitute (bukapilot's `driving_vision.onnx`/`driving_policy.onnx`)
and the reference-only Autoware vision suite (`egolanes_lite_int8`,
`scene3d_lite_int8`, `sceneseg_lite_int8`, `autosteer_full_int8`,
`autospeed_full_int8`) were fetched and then removed. They're still available
from `../bukapilot` and Autoware if that capability is needed again —
see git history for the exact hashes. The eGPU runs this one model and
nothing else (2026-09-27): the old `yolo_side`/`yolo_rear`/`seg_*` eGPU-shadow
placeholders were removed. Detection is RKNN (`rknn/`); segmentation for
every camera is the camera-tier card (`hef/` + `dxnn/`).

| File | SHA256 | Source | Notes |
|------|--------|--------|-------|
| `onnx/big_driving_supercombo.onnx` | `10926f2c0911821ca0e72439c1c3bf3ec11f0a08789aa14b7ee8f25379b2afa4` | `commaai/openpilot@master` (`b7c333cf3fee117779515c9ebfd7b2beb164fa81`, via `../ext_gpu/openpilot-upstream`), cross-verified byte-identical against `sunnypilot@master` (`bf74ce544738189693dbd07266a46e63465710c1`) | Upstream Chestnut big model, 1,753,235,978 bytes. **Not currently loaded by anything** — `ChestnutDrivingRunner`/`factory.py` is deliberately fail-closed until the tinygrad-JIT-compiled artifact, replay/HIL/hardware-soak gates, and closed-course validation are all in place (see `task.md`, `docs/eop/05_Features/CHESTNUT_EGPU_ADOPTION.md`). **Hash/size differ from the previously audited value** (`a501760a9d1...`, 1,757,355,221 bytes) — comma appears to have shipped a model update since that audit (sunnypilot's commit touching this same file is titled "Be Right Here Model 🏃 (big)", 2026-08-01); this is expected drift for a live upstream artifact, not corruption. Re-verify against `commaai/openpilot@master` before any future compile step. |

There is no roadwork-segmentation or dmonitoring-via-ONNX consumer on this
branch; add an entry here first if one is built.

## Folder naming

Folders are named by file format, not backend brand, and this must stay
consistent — never mix the two axes:

| Folder | Format | Backend |
|---|---|---|
| `rknn/` | `.rknn` | `BackendType.NPU` (Rockchip) |
| `hef/` | `.hef` | `BackendType.HAILO_8` |
| `onnx/` | `.onnx` | Chestnut's big model (the eGPU's only model; not currently loaded by anything); `BackendType.ONNX` (dev-PC/CPU fallback) exists in code but has no driving-model file here today |
| `dxnn/` | `.dxnn` | `BackendType.DX_M1` (DeepX DX-M1M) — `twinlitenet_plus_large_384x640.dxnn`, TwinLiteNet+ Large drivable area + lane lines, built with DX-COM by `tools/card_drivable_model.py` from the same ONNX as the HEF (not in the repo). Camera tier alongside `hef/`, interchangeable with Hailo-8. Official source: [github.com/DEEPX-AI/dx-modelzoo](https://github.com/DEEPX-AI/dx-modelzoo) (354 pre-compiled models — detection, segmentation, classification, face recognition) |

Priority ordering across tiers: `rknn/` (`SAFETY_INFERENCE`, the driving model)
is always authoritative and always loaded. `hef/`/`dxnn/` (`CAMERA_INFERENCE` —
side, rear, 02M telephoto, and rule-based Autoware-style camera models) run
first/cheaper. `onnx/`'s Chestnut big model is the eGPU's one and only model:
an optional external driving model with one-way failover back to `rknn/` — see
"Not currently loaded by anything" above.

**No `axmodel/` tier on ExoPilot.** AX-M1/AXCL is HumRobot's (ExoRobot 01H)
hardware, not ExoPilot's — there is no AX-M1/AXCL card in the ExoPilot BOM and
no backend for it here. Voice on ExoPilot is local-only: `micd` -> `voiced`
(beamformer + VAD), no STT/wake-word/LLM planned. See `VOICE_PIPELINE.md`.

## Adding New Models

1. Download and place the file in the appropriate subdirectory.
2. Run `sha256sum <file>` and record the checksum here.
3. Add an entry to `download_models.sh` so CI and fresh installs can fetch it.
