# RK3588 fixes ported from Kommu KA2

**Source**: `bukapilot` (Kommu KA2, RK3588, production since 2026-03, active
field testing on BYD/Proton). Unlike EOP10, KA2's RK3588 stack has run on
real silicon. This doc tracks what was pulled from that experience — mainly
into `system/inferenced/rockchip_npu.py` (RKNN inference), plus one
`system/loggerd` finding (video encode) — what wasn't, and why.

---

## Ported

### 1. Explicit `data_type="float16"` on `RKNNLite.inference()`

**Bug found**: `rockchip_npu.py` called `rknn.inference(rknn_inputs)` with no
`data_type`. `RKNNLite.inference()` defaults `data_type` to `"uint8"` (see the
`rknn_toolkit_lite2` wheel — `rknnlite/api/rknn_lite.py`). Our own
`tools/convert_models_to_rknn.py` builds `driving_vision`/`driving_policy`
with `quantize=False` (FP16), so every inference call was asking RKNNLite to
read FP16-model buffers as uint8. KA2's `driving_rknn.py` always passes
`data_type="float16"` explicitly — same RKNNLite API, same FP16 export
convention. Ported: inputs to those two models are now cast to `float16` and
the call passes `data_type="float16"`.

**Scoped to `driving_vision`/`driving_policy` only**, via an explicit
`_FP16_MODELS` allowlist keyed on the `model_name` string `modeld.py` passes
to `infer()` — not applied backend-wide. `rockchip_npu.py`'s `RKNNBackend` is
shared by every RKNN-routed entry in `inference_registry.yaml`, including
`sceneseg`/`ppliteseg`, whose quantization wasn't checked here. `data_type` is
a single scalar per `inference()` call, so a backend-wide fp16 cast would have
silently miscast any of those if they turned out to be int8-quantized.
Verified `driving_vision`'s real ONNX graph declares `img`/`big_img` as
uint8 (elem_type 2) — RKNNLite's uint8 default is nominally correct for the
*ONNX* dtype, but KA2's own production `driving_rknn.py` casts this same
uint8 input to fp16 anyway and it runs correctly in the field, which is the
stronger signal here: `quantize=False` builds apparently expect fp16 at the
RKNNLite call boundary regardless of the source ONNX graph's declared input
dtype. Deferring to KA2's actual runtime behavior over the ONNX metadata for
this reason.

**Not ported**: KA2 additionally scales uint8 camera frames by `/255` in its
*old* `rknnmodel.py` runner — but that runner is legacy and gated behind a
`use_tf8` flag that's off by default. The runner actually in production
(`driving_rknn.py`) does **not** divide by 255; it casts raw 0–255 pixel
values straight to float16. That matches our own exporter, which bakes
`mean_values=[127.5]*3, std_values=[127.5]*3` into the RKNN graph — the
on-chip normalization already expects raw 0–255 input. Do not add a `/255`
step here; it would double-normalize.

### 2. NPU driver version diagnostic

`_check_driver_version()` logs `RKNNLite.get_sdk_version()` and warns if it's
older than `MIN_RKNPU_DRIVER_VERSION = "0.9.6"`. KA2 discovered in production
that older rknpu drivers reject the `RKNN_NPU_CORE_1_2` mask and crash under
concurrent multi-model NPU access (their C API equivalent:
`rknn_query(RKNN_QUERY_SDK_VERSION)`, written to `/dev/shm/rknpu_drv_version`
for `deviceState`). Diagnostic only — nothing is blocked on this.

### 3. NPU core-mask wiring — resolved using our own design, not KA2's

`hal.tuning.npu.CORE_ALLOCATION` assigns `driving_vision`→core 0, `policy`→
core 2 (split). `modeld.py`'s `_load_models()` was hardcoding `npu_cores=0`
(AUTO) for both, silently bypassing the design entirely. Considered copying
KA2's proven production pattern instead — which keeps
`driving_vision`+`driving_policy` **together on one core** inside the single
`modeld` process, splitting cores only for the *separate*
`dmonitoringmodeld` process — but rejected it: `exopilot`'s own
`NPU_CORE_CONFIGURATION.md` documents `driving_vision` alone at ~100% of a
core's 2.0 TOPS budget on RK3588 ("effectively exclusive due to hardware
limitation"), leaving no headroom to add `policy` (~0.5 TOPS) onto the same
core without exceeding it. KA2 apparently has that headroom (different
chip/model TOPS profile) — theirs isn't a safe template for this repo's
specific budget. Fixed instead by calling `get_platform_npu_config()` and
passing its `get_core_mask('driving_vision')`/`get_core_mask('policy')` as
`npu_cores`, i.e. actually applying the split this repo's own team already
designed, rather than either AUTO or KA2's pattern.

**Still open**: this repo's own split-core design has never run on real
RK3588 hardware either, so "matches our documented intent" isn't the same as
"proven safe." The KA2 driver bug (item 2 above) was documented for a
combined core-mask on one model and for two concurrent *processes* — not for
one process alternating between two singly-masked models on different cores,
which is what this now does and which neither project has tested. See
`RK3588_HARDWARE_VALIDATION_CHECKLIST.md` §3.

### 4. Toolkit version — decided against pinning to KA2's

Considered pinning to `rknn-toolkit2==2.2.0` (KA2's proven production
version) but decided against it: their runtime wheel is `cp311`-only and this
repo targets Python 3.12, so the exact artifact can't be reused anyway, and
the actual value from KA2 wasn't the version number — it was the behavioral
knowledge (items 1 and 2 above), which is toolkit-version-independent. Use
whichever `rknn-toolkit2` is latest and cp312-compatible. The one thing to
watch when moving off 2.2.0: `RKNNLite.inference()`'s `data_type` default and
`get_sdk_version()` return format are assumed stable across versions here —
neither was re-verified against a newer wheel, since none was available in
this environment either (no network path to PyPI's binary index was
attempted; only bukapilot's vendored 2.2.0 wheel and a 1.6.0 wheel vendored
in the separate `visionpilot` repo were inspectable locally).

### 5. MPP/RGA community mirror fallback

`scripts/install_rockchip_deps.sh` only looks for vendor `.deb`s inside a
local LubanCat SDK checkout, with no fallback if that SDK isn't present. The
official `rockchip-linux/mpp` repo was taken down; KA2's docs
(`docs/development/loggerd_mpp_deps.md` in bukapilot) point at
`tsukumijima/mpp-rockchip` and `tsukumijima/librga-rockchip` as the maintained
community mirror. Added as a documented fallback in the script's missing-deps
summary.

### 6. RK3588 hardware video encode — opt-in, in `ffmpeg_encoder.cc`

Separate subsystem (`system/loggerd`, not `system/inferenced`), found while
looking for more KA2 material to port. KA2 has a from-scratch C++
`MppEncoder` (`system/loggerd/encoder/mpp_encoder.cc`, ~512 lines) calling
Rockchip's MPP API directly, wired into the same `VideoEncoder` interface
this repo's `FfmpegEncoder`/`V4LEncoder` also implement. Porting that whole
class wasn't attempted — different subsystem's build wiring, no way to
compile/link it here (same reasoning as skipping the RKNN C++ runner port).

Instead: `encoderd.cc` `#define Encoder FfmpegEncoder` for Rockchip, with a
comment claiming "MPP support via ffmpeg" — but `ffmpeg_encoder.cc` called
`avcodec_find_encoder(AV_CODEC_ID_H264)`, a generic lookup that resolves to
whatever's first-registered for that codec ID (typically software `libx264`,
not the hardware `h264_rkmpp` encoder, which must be requested **by name**).
Confirmed via search that `h264_rkmpp` is the established path (mainline
ffmpeg since 2017, actively maintained via `nyanmisaka/ffmpeg-rockchip`) —
this repo was one `avcodec_find_encoder_by_name()` call away from actually
using it, not missing the capability architecturally.

Added an opt-in path: `ENCODER_USE_RKMPP` env var (default off) tries
`avcodec_find_encoder_by_name("h264_rkmpp")` first, falls back to the
existing software codec if not found or if `avcodec_open2` fails. Default
behavior is unchanged unless explicitly enabled. **Not changed**: the frame
format fed to the encoder (`AV_PIX_FMT_YUV420P`, converted from the camera's
native NV12 via `libyuv::NV12ToI420`). Unverified whether this ffmpeg build's
`h264_rkmpp` accepts YUV420P directly or requires NV12 — if the latter, the
`avcodec_open2` fallback catches it safely (no crash), but won't actually get
hardware encoding until someone feeds it NV12 directly (which would also let
`NV12ToI420` be skipped entirely — camera buffers are already NV12). That's
the natural next step once someone can test against real hardware; not
attempted here since I can't verify the pixel-format contract without it.
Syntax-checked with `clang++ -std=c++20 -fsyntax-only` against real cached
ffmpeg headers (`libavcodec`/`libavformat`) — compiles clean.

### 7. Dead code removed

- **`selfdrive/modeld/runners/rknn_platform.py`** (319 → 111 lines): the
  `NPUPlatformConfig` class had ~10 methods; only `get_core_mask()`,
  `is_core_available()`, `.core_count`, `.is_rk3588`, `.platform.value` were
  ever called anywhere (`modeld.py`, `selfdrive/monod/monod.py`). Everything
  else — `get_safe_budget_tops()`, `can_fit_on_core()`,
  `get_core_headroom_tops()`, `recommend_sharing()`, `get_tops_per_core()`,
  plus a whole `NPUMask` enum duplicating `RKNNLite`'s own core-mask
  constants — computed TOPS-budget numbers nothing ever consulted before
  assigning a model to a core; the real decision was always the hand-written
  `CORE_ALLOCATION` dict. Verified zero external references to every removed
  symbol (grepped the whole repo) before deleting. Kept identical behavior
  for everything still called — verified by running the file standalone
  before/after and diffing outputs.
- **`selfdrive/modeld/runners/rknn_runner.py`** (`RKNNRunner`/
  `RKNNModelPool`, ~200 lines): zero real callers — `modeld.py` and
  `selfdrive/gridd/pp_liteseg.py` had both already moved to the
  `InferenceClient`/HAL pattern directly (per their own comments: *"replacing
  direct RKNNRunner usage"*, *"REPLACED by direct RKNNRunner usage"* in
  `inference_registry.yaml`, itself now stale in the other direction). Its
  own audit history (`docs/upstream-audit/COMMIT_CABE4693_REVIEW.md`)
  documents it used to crash on construction; fixed since, but never wired
  back into anything that calls it. It **was** shown as a public usage
  example in `docs/eop/01_Core/PLATFORM_CONSTRAINTS.md` — updated that
  example to the actual `InferenceClient`/HAL pattern (matching what
  `modeld.py` really does) instead of deleting the example outright.

---

## Explicitly not ported

- **`RKNN_NHWC_BIGIMG_SCALE`/`BIAS` affine hack** in KA2's `driving_rknn.py`
  (scale=0.55, bias=-6.0, clipped 0–255) — this compensates for a specific
  NHWC export quirk in KA2's own vision model conversion. Our models are
  NCHW (`img`/`big_img` are `[1, 12, 128, 256]` — see "Verified: KA2's ONNX
  shapes match our own code exactly" below — with no NHWC transpose anywhere
  in `modeld.py`). Applying this would corrupt inputs.
- **`RKNN_PY_*` env-var matrix** (`RKNN_PY_VISION_PT`, `RKNN_PY_VISION_FORMAT`,
  `RKNN_PY_VISION_EXPLICIT`, etc.) — tuning knobs for KA2's specific model
  export, not a generic RK3588 concern.
- **C++ RKNN runner** (`rknnmodel.cc`/`.pxd`/pyx wrapper, preallocated output
  buffers, per-model NPU context) — KA2's fast path, real perf win over the
  pure-Python `RKNNLite` calls we use today. Not attempted here: our
  `third_party/rknpu2` submodule is an empty, uninitialized checkout in this
  environment, so a native-extension port would be unbuildable and
  unverifiable. Worth a dedicated pass once real RK3588 hardware + a working
  `rknpu2` checkout are available (Phase 5 in `PHASE5_HARDWARE_READINESS.md`).

## Verified: KA2's ONNX shapes match our own code exactly

Pulled the real (git-lfs) `driving_vision.onnx` / `driving_policy.onnx` from
`origin/byd_sng_ka2` in bukapilot and inspected the graphs directly
(`onnx.load(...).graph.input/output`), opset 17 both:

| Model | Tensor | KA2 shape/dtype | Our code |
|---|---|---|---|
| vision | `img` | `[1,12,128,256]` uint8 | matches `MODEL_WIDTH=512, MODEL_HEIGHT=256` raw YUV frame → loadyuv-transformed (12ch, half-res) — see `commonmodel.h` |
| vision | `big_img` | `[1,12,128,256]` uint8 | same |
| vision | `outputs` | `[1,1576]` float16 | — |
| policy | `desire_pulse` | `[1,25,8]` float16 | `INPUT_HISTORY_BUFFER_LEN(25) x DESIRE_LEN(8)` — exact match |
| policy | `traffic_convention` | `[1,2]` float16 | `TRAFFIC_CONVENTION_LEN(2)` — exact match |
| policy | `features_buffer` | `[1,25,512]` float16 | `INPUT_HISTORY_BUFFER_LEN(25) x FEATURE_LEN(512)` — exact match |
| policy | `outputs` | `[1,1000]` float16 | — |

Both projects source `driving_vision.onnx`/`driving_policy.onnx` the same
way: not committed to git, fetched separately at build time (this repo's
`convert_models_to_rknn.py` just checks the files exist — no URL). Combined
with the constants match, this is strong evidence both are the same stock
comma model.

**Found an inconsistency, fixed defensively, impact unverified**:
`convert_models_to_rknn.py` was calling `rknn.load_onnx(...,
input_size_list=[[1, 3, 256, 512]])` — one entry, RGB-shaped — for a model
with *two* inputs (`img`, `big_img`), each `[1, 12, 128, 256]` per the real
graph. Changed to `input_size_list=[[1, 12, 128, 256], [1, 12, 128, 256]]` to
match. **Not confirmed**: whether the old value ever mattered.
`load_onnx()`'s `input_size_list` is documented to apply only when the graph
has dynamic input shapes — if this graph's shapes are static (likely, given
it's a fixed-architecture stock model), the real graph shape wins regardless
and the old value was inert. No `rknn-toolkit2` install was available in the
environment this was checked in, so this couldn't be run either way. Treat
the fix as "now matches the real graph, can't hurt" rather than "confirmed
broken before."

**Still unverified — do this before trusting a KA2 `.rknn` file as a
drop-in**: the shape match is strong but not proof of bit-identical models.
Not confirmed: KA2's exact `rknn.config(mean_values=..., std_values=...,
target_platform=..., ...)` at compile time (no conversion script is checked
into bukapilot — models were built externally), and the rknn-toolkit2
*build-side* version used (only their on-device `rknn_toolkit_lite2==2.2.0`
runtime version is known). KA2's `driving_rknn.py` does no manual pixel
normalization before casting to fp16, which is consistent with our own
`mean_values=[127.5]*3, std_values=[127.5]*3` baked-in-graph approach — but
"consistent with" isn't "confirmed identical to." Before deploying a KA2
`.rknn` file in this repo: run it through `rknn.list_inputs()`/actual
inference on hardware and compare output distributions against our own
from-ONNX conversion on a shared test frame.
