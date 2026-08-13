# RKNN Runtime Notes for RK3588/RK3576

Behavioral notes for the `RKNNBackend` in `system/inferenced/rockchip_npu.py`.
These were validated against a proven RK3588 production stack and apply to
RK3588/RK3576 NPU inference.

## FP16 inference allowlist

Models exported with `quantize=False` (FP16) must call `RKNNLite.inference()`
with `data_type="float16"`, even when the source ONNX graph declares uint8
inputs. In EOP10 this is scoped to an explicit allowlist:

```python
_FP16_MODELS = frozenset({"driving_vision", "driving_policy"})
```

Other entries in `inference_registry.yaml` (e.g. `sceneseg`, `ppliteseg`) keep
RKNNLite's default uint8 behavior because their quantization was not verified.

## Driver version diagnostic

`_check_driver_version()` logs `RKNNLite.get_sdk_version()` and warns if it is
older than:

```python
MIN_RKNPU_DRIVER_VERSION = "0.9.6"
```

Older drivers are known to reject combined core masks and crash under
concurrent multi-model NPU access. This is diagnostic only; no runtime path is
blocked.

## NPU core allocation

Do not hardcode `npu_cores=0` (AUTO). Use `get_platform_npu_config()` from
`hal.tuning.npu` and pass per-model core masks:

- `driving_vision` → core 0
- `driving_policy` → core 2

This matches ExoPilot's documented TOPS budget. Splitting across cores is the
repo's own design; do not collapse both models onto one core on RK3588 without
re-checking the TOPS headroom.

## Input normalization

Camera frames are fed as raw 0–255 uint8 values. The RKNN graph bakes in
`mean_values=[127.5]*3, std_values=[127.5]*3`. Do **not** divide by 255 before
inference; that would double-normalize.

## Hardware video encode (loggerd)

`ffmpeg_encoder.cc` has an opt-in `ENCODER_USE_RKMPP` path that requests
`h264_rkmpp` by name, falling back to software if unavailable. Default is off
until validated on real hardware with the correct pixel format (NV12 vs
YUV420P).

## Toolkit version

Use the latest `rknn-toolkit2` / `rknn-toolkit-lite2` that is compatible with
the target Python version. The runtime behavior above is assumed stable across
recent toolkit versions.

## Not applied

- NHWC affine input hacks — EOP10 models are NCHW.
- RKNN-specific env-var tuning matrices — tied to a specific external export.
- Native C++ RKNN runner — blocked until `third_party/rknpu2` is populated and
  buildable on target hardware.
