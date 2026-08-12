# RK3588 hardware validation checklist

Every item below is a question raised while porting fixes from Kommu RK3588 reference
(bukapilot) into this repo's RKNN/encoder code (see
`RKNN_RK3588 reference_PROVENANCE.md`) that could not be settled without a real ExoPilot
01M board — confirmed 2026-08-12 that the current `dev/EOP10` code has not
run on hardware yet (see `PLATFORM_SUPPORT.md` correction in `exopilot`).
Run these in one session rather than piecemeal; several share setup.

Ordered cheapest/fastest first — bail early if an early item fails, since
later ones assume `modeld` boots at all.

---

## 1. Does `modeld` boot and produce sane output?

The baseline everything else depends on. `system/inferenced/rockchip_npu.py`
and the `driving_vision`/`driving_policy` core-mask wiring in `modeld.py`
have never executed against a real `RKNNLite`/NPU.

```
python3 -m openpilot.selfdrive.modeld.modeld
```
- Watch for the driver-version log line (`rknpu driver version: ...`) —
  confirms `_check_driver_version()` and `get_sdk_version()` work as coded.
  Warn if driver `< 0.9.6`.
- Confirm no crash/exception loading either model with the new
  `npu_cores=get_core_mask(...)` values (core 0 for vision, core 2 for
  policy) — this is pinned behavior that replaces the previous `AUTO` (`0`),
  which is what has actually been running (nowhere, but AUTO was at least
  the coded default). If this crashes or produces garbage: revert
  `modeld.py`'s two `npu_cores=` lines back to `0` as the known-safe value
  and file that as a real finding — the split-core design doesn't hold.

## 2. Is the `driving_vision`/`driving_policy` fp16 handling correct?

`rockchip_npu.py`'s `_FP16_MODELS` path casts inputs to `float16` and passes
`data_type="float16"` for exactly these two models — matching RK3588 reference's
production runner, despite `driving_vision`'s ONNX graph declaring `img`/
`big_img` as uint8 (see provenance doc §1 for why RK3588 reference's runtime behavior was
trusted over the ONNX metadata).

- Compare `modeld`'s output (steering/path/lead) against expected sane
  values for a static test scene, or replay a route if one exists for this
  hardware.
- If output is garbage/NaN: the ONNX-declared uint8 dtype may be authoritative
  after all for *this* specific model export (unlike RK3588 reference's), meaning the
  RKNNLite default (`data_type="uint8"`, no cast) was actually correct for
  vision and only `driving_policy` needed the fp16 fix. Split the
  `_FP16_MODELS` handling per-model if so.

## 3. Does the core-mask split cause the crash pattern RK3588 reference found?

RK3588 reference's documented crash was for a combined core-mask on one model plus two
*concurrent processes* sharing the NPU — not for one process holding two
singly-masked models sequentially, which is what `modeld` now does
(vision→core 0, policy→core 2). This is a third, untested configuration.

- Run `modeld` alongside another NPU consumer (any second RKNN-backed daemon,
  or a synthetic second `rknn.inference()` loop) and watch `dmesg` for
  NPU/rknn errors, matching RK3588 reference's diagnostic pattern in
  `modeld_rknn.md`/`RKNN_RK3588 reference_PROVENANCE.md` §2.
- If it's stable: good, the split-core design holds under this repo's own
  documented TOPS budget (see `NPU_CORE_CONFIGURATION.md`).
- If it crashes: consider whether `driver < 0.9.6` is the cause (check #1's
  driver-version log first) before concluding the split itself is unsafe.

## 4. Does `convert_models_to_rknn.py`'s `input_size_list` fix matter?

Changed from a wrong 1-entry `[[1, 3, 256, 512]]` to a 2-entry
`[[1, 12, 128, 256], [1, 12, 128, 256]]` matching the real graph (verified
via a fetched RK3588 reference `driving_vision.onnx`, not this repo's own — see
provenance doc). Never run through an actual `rknn-toolkit2` install.

```
python3 tools/convert_models_to_rknn.py --test
```
- Confirm it builds successfully at all (first real run of this script).
- Confirm the fix mattered: temporarily revert to the old `[[1, 3, 256,
  512]]` value and see whether `rknn.load_onnx()` errors, silently ignores
  it (graph has static shape, parameter is inert), or produces a broken
  model. Settles the "unverified whether the old value ever mattered" note.

## 5. Would RK3588 reference's actual `.rknn` files work as a drop-in?

Not staged into this repo — deliberately left for explicit hardware
verification given it's swapping the literal weights driving the car. Real
RK3588 reference ONNX graphs were already pulled and diffed against this repo's own
`constants.py` (exact match on `desire_pulse[1,25,8]`,
`traffic_convention[1,2]`, `features_buffer[1,25,512]`, vision `img`/
`big_img [1,12,128,256]`) — strong but not sufficient evidence.

Use a `.rknn` produced from this repo's own ONNX files
(`tools/convert_models_to_rknn.py`) or obtained from a trusted internal source.
Copy it into `selfdrive/modeld/models/` and point the model paths at it.
- Load via `rknn.list_inputs()` (or just try `RKNNLite.load_rknn()` +
  `init_runtime()`) and confirm it accepts the same core masks.
- Run one frame through and sanity-check output ranges against this repo's
  own from-ONNX conversion (item #4) on the same input, if #4 succeeded.
- RK3588 reference's exact compile-time `rknn.config(mean_values=..., std_values=...)`
  is unknown (no conversion script checked into the reference fork) — a clean load +
  plausible output is the best available confirmation, not proof of
  bit-identical behavior.

## 6. Does `h264_rkmpp` accept this repo's YUV420P frames, or need NV12?

`ffmpeg_encoder.cc`'s `ENCODER_USE_RKMPP=1` opt-in path was added but never
run — unverified whether the `h264_rkmpp` encoder in whatever ffmpeg build
ships on this board accepts `AV_PIX_FMT_YUV420P` (what's currently fed) or
requires `AV_PIX_FMT_NV12` (the camera's native format, and what RK3588's
hardware encoder natively wants).

```
ffmpeg -hide_banner -h encoder=h264_rkmpp   # confirm it's present in this build's ffmpeg first
ENCODER_USE_RKMPP=1 <run encoderd>
```
- Check logs for the `LOGE("h264_rkmpp avcodec_open2 failed...")` fallback
  message — if it fires, `avcodec_open2` rejected the config (likely the
  pix_fmt) and safely fell back to software. Not a crash, but not hardware
  encoding either.
- If it opens successfully: confirm the *output* is actually valid encoded
  video (not just that `avcodec_open2` returned 0) — play back a recorded
  segment.
- If YUV420P is rejected: the natural fix is feeding NV12 directly (camera
  buffers already are NV12 — `libyuv::NV12ToI420` could be skipped entirely
  for this path, which is a real efficiency win beyond just "unblocks
  hardware encode"). Not attempted here; needs the pix_fmt answer first.

---

## After this session

Update `PHASE5_HARDWARE_READINESS.md` and `RKNN_RK3588 reference_PROVENANCE.md`'s open
items with actual results — several notes there currently say "unverified,
needs hardware," which this checklist exists to close out.
