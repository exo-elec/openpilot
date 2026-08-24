# 3D reconstruction on the eGPU: USB 3.0 Gen1 bandwidth analysis

## Question

Can EOP do 3D reconstruction (stereo depth / occupancy / voxel) on the
ASM2464PD eGPU, given the USB 3.0 Gen1 (5Gbps) link, and if so what input/
output representation keeps it within budget?

## What EOP already has — entirely local, not on the eGPU

`stereod` → `gridd` is EOP's existing 3D-reconstruction pipeline
(`docs/eop/03_Software/Architecture/DAEMON_CONNECTIONS.md`):

- `stereod`: SGM stereo depth (RK3588 ACL, GPU/CPU) → `disparity` (msgq).
- `gridd`: fuses disparity + multi-camera detections into an ego-centric 2D
  occupancy grid → `grid` (msgq), consumed by `pathd`/`controlsd`.

Both run **today, on RK3588, with zero eGPU/USB involvement.** The output
format (`cereal/log.capnp`'s `GridObjects`/`OccupancyGridLayer`) is already
a compact, quantized representation, not a dense depth map or point cloud:

```
struct OccupancyGridLayer {
  name @0 :Text;
  encoding @1 :OccupancyGridEncoding;
  data @2 :Data;        # flattened row-major array, raw bytes
  scale @3 :Float32;    # raw_value * scale + offset = metric value
  offset @4 :Float32;
}
```

This is currently 2D (ego-centric BEV: `width`/`height`, no `z`/depth-layer
dimension) — but the `scale`/`offset`-quantized flat-byte-array pattern is
exactly the right shape to extend to a true 3D voxel grid if that's ever
wanted, and is already far more compact than a raw depth map or point cloud
(see budget comparison below). Any eGPU-assisted 3D reconstruction should
reuse this encoding, not invent a new one.

## The bandwidth ceiling, with real numbers (not the nominal link rate)

USB 3.0 Gen1 signals at 5 Gbit/s with 8b/10b encoding (500 MB/s raw), but
protocol/flow-control overhead brings sustained bulk throughput down to
**roughly 300–400 MB/s** in practice for capable controllers [Tom's
Hardware, sunbeamtech.com, pshinecable.com]. This matches what the
ASM2464PD firmware itself measures: its DMA-to-SRAM bulk path (the `0xF2`
message, the one that matters for moving real tensor data — not the
`0xF0` PCIe-TLP control-message path, which the firmware's own docs put at
just **3.6 MB/s write / 1.8 MB/s read**) hits **~700 MB/s at USB3 Gen2
(10 Gbps)** [tinygrad/asm2464pd-firmware `USBGPU.md`]. Halved for our Gen1
(5 Gbps) link, that's **~350 MB/s** — consistent with the general USB3
Gen1 figures above, and the number to actually budget against, not 500 MB/s.

**Practical implication for any eGPU transport design:** data must move
through the bulk-DMA path (tinygrad's `ops_amd.py`/`support/usb.py` already
does this — 31 concurrent bulk streams, 512 KB host→device buffers), never
through PCIe-TLP-style control messages for anything beyond register
pokes. This is already how tinygrad's own USB backend works, so it's not a
design decision EOP needs to make — just a constraint to stay inside of.

## Existing committed budget (already documented, `EGPU_CAMERA_SHADOW.md`)

One FP16 512×1024 RGB tensor ≈ 3.15 MB. At the driving model's 20 Hz
(`ModelConstants.MODEL_FREQ`), **one camera view alone is ~63 MB/s**; EOP's
driving path needs at least two (road + wide), so realistically **≥126 MB/s**
before side/rear detection, segmentation shadow jobs, or anything else in
the priority queue (`CHESTNUT_EGPU_ADOPTION.md`'s scheduler policy). Against
a ~350 MB/s realistic ceiling, that's already more than a third of the
budget for the driving path alone.

## Why re-uploading a stereo camera pair for 3D reconstruction is the wrong move

The naive approach — send both stereo views to the eGPU so it can do SGM
(or a learned depth network) there instead of on RK3588 — costs another
**~63–126 MB/s** (one more FP16 tensor pair, same math as above), on top of
a budget that's already tight. And it buys nothing `stereod` doesn't
already provide today, for free, with zero USB cost. **Recommendation:
don't do this.** Two better options, in priority order:

### Option A (recommended): derive 3D structure from the driving model's own features, zero extra input bandwidth

If/when the eGPU big-model path is real (`ChestnutDrivingRunner`/
`EgpuDrivingRunner`, currently fail-closed pending a compiled artifact —
see `CHESTNUT_EGPU_ADOPTION.md`), the vision encoder's hidden-state tensor
is *already resident on the eGPU* for driving purposes. A 3D/occupancy head
reading from that same feature tensor costs **no additional camera upload
at all** — only the marginal compute for the extra head, plus whatever
compact grid the output requires (see below). This is the standard modern
pattern (occupancy-network-style multi-task heads sharing one backbone) and
is the only option that doesn't touch the USB budget on the input side.
Blocked on the same thing the whole big-model path is blocked on: a real
compiled artifact and validation gates — not a new blocker specific to 3D
reconstruction.

### Option B: keep it local (status quo), only escalate if there's a proven fidelity gap

`stereod`/`gridd` already work without the eGPU. Don't move them onto a
scarce, shared, higher-latency USB link unless RK3588's local ACL compute
demonstrably can't hit the fidelity or range needed — and if that gap is
ever real, prefer Option A (fuse into the driving model's existing forward
pass) over a standalone stereo-reconstruction eGPU path.

### If a standalone eGPU 3D-reconstruction path is ever truly required anyway

- **Input:** send raw NV12 camera frames (12 bits/pixel), not pre-normalized
  FP16 tensors (48 bits/pixel for RGB) — a ~4x bandwidth reduction, at the
  cost of moving normalization/color-space work onto the eGPU side instead
  of RK3588's RGA/Mali path. This is a deliberate *departure* from
  `CHESTNUT_EGPU_ADOPTION.md`'s existing "upload the model-ready tensor, not
  a raw frame" rule for the driving path — that rule was made for the
  primary driving contract; a bandwidth-starved secondary path is a
  legitimate reason to make a different tradeoff for a different workload,
  not a reason to reopen the driving-path decision.
- **Output:** extend `OccupancyGridLayer`'s existing `scale`/`offset`
  quantized-byte-array encoding to a third (height/z) dimension for a real
  voxel grid, rather than a dense per-pixel depth map (which is comparable
  in size to the *input* it was computed from — no bandwidth win) or an
  unbounded point cloud (variable, unpredictable size — hard to budget or
  admission-control against, which `CHESTNUT_EGPU_ADOPTION.md`'s scheduler
  section already requires). A coarse voxel grid (e.g. a road-relevant
  ~100m × 100m × ~5m volume at 0.5–1m cells, 1 byte/voxel quantized
  occupancy) is on the order of tens to low hundreds of KB per frame —
  negligible next to the driving-path budget above, and it's the same shape
  `gridd` already emits today, just with a third dimension.
- **Rate:** run below the driving model's 20 Hz — occupancy/BEV planning
  inputs tolerate more latency than the driving model itself; `gridd`'s own
  existing `grid` service already publishes below driving-model rate for
  exactly this reason. Lower rate is a direct, proportional bandwidth win
  and should be the first lever before touching resolution or precision.

## Summary

| Approach | Extra input bandwidth | Output size | Verdict |
|---|---|---|---|
| Re-upload stereo pair to eGPU, run SGM there | +63–126 MB/s | small (grid) | Don't — no benefit over existing local `stereod`, meaningful budget cost |
| Derive 3D/occupancy from driving model's existing features | ~0 | small (grid) | **Recommended**, once the big-model path is real |
| Keep `stereod`/`gridd` local (status quo) | 0 | n/a (already local) | Fine as-is until a real fidelity gap is demonstrated |
| Standalone eGPU 3D path, raw-frame input + voxel output | ~16–32 MB/s (4x cheaper than FP16) | tens–low hundreds of KB | Fallback only if Option A/B both prove insufficient |

No code changes from this analysis — it's a design/budget reference for
whenever 3D reconstruction on the eGPU is actually proposed. Cross-linked
from `CHESTNUT_EGPU_ADOPTION.md` and `EGPU_CAMERA_SHADOW.md`.

## Sources

- [Tom's Hardware — USB 3.0 UAS/Turbo Mode real-world throughput](https://www.tomshardware.com/reviews/usb-3-uas-turbo,3215-2.html)
- [sunbeamtech.com — USB 2.0 vs 3.0 speed differences](https://sunbeamtech.com/hardware-guides/usb-2-vs-3-differences/)
- [pshinecable.com — actual USB 3.1 Gen1/Gen2 transfer rate](https://www.pshinecable.com/article/what-is-the-actual-data-transfer-rate-of-usb-3-1-gen-1-and-gen-2.html)
- [tinygrad/asm2464pd-firmware — `USBGPU.md`](https://github.com/tinygrad/asm2464pd-firmware/blob/master/USBGPU.md)
- `../exopilot/docs/02-HARDWARE/EGPU_ASM2464PD.md` §2, §13 (this repo's own hardware/transport findings)
- `docs/eop/05_Features/EGPU_CAMERA_SHADOW.md` (existing tensor-size/scheduling budget)
- `docs/eop/05_Features/CHESTNUT_EGPU_ADOPTION.md` (existing preprocessing/transport decisions)
- `docs/eop/03_Software/Architecture/DAEMON_CONNECTIONS.md` (existing `stereod`/`gridd` pipeline)
- `cereal/log.capnp` `OccupancyGridLayer`/`GridObjects` (existing compact grid encoding)
