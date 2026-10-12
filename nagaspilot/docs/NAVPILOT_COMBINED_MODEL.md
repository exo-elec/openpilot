# The combined (Chestnut-class) model contract, step by step

Status 2026-10-12: **Steps 1 (this spec) and 2 (`spec.py`, `queues.py`) are done and tested; steps 3 to 6 are not.** Following Jetlink's design and tests closely is deliberate: it is open source (MIT) and is the reference the community will compare against.
Part of [`NAVPILOT_BIGMODEL.md`](NAVPILOT_BIGMODEL.md) task 8. Source of the facts below: Jetlink's `spec.py`, `queues.py` and `docs/` (MIT, credited in
`nagaspilot/bigmodel/THIRD_PARTY_NOTICES.md`); not yet checked against a real model file, because none is in this tree.

## Why a second contract

NGP10's `modeld` runs two small graphs (vision, then policy) and keeps the history on the comma. The models comma ships for Chestnut (and Jetlink's catalog, e.g. Cinque
Terre, BMRLNAP, Lebowski) are **one graph** with the history as stacked inputs, and a newer family keeps the history inside the graph. The contract the phone serves
must therefore be described by the model itself, not hard-coded.

## The queued graph (what Jetlink calls the queued layout; openpilot before #38916)

Inputs (shapes from the ONNX; the big model's):

| Input | Shape | Meaning |
| --- | --- | --- |
| `img`, `big_img` | `(1, 12, H, W)` uint8 | narrow and wide camera: `n_frames = 12 / 6 = 2` frames of 6 planes each, the newest and one `frame_skip` x (n-1) back |
| `features_buffer` | `(1, 32, 32, 512)` | 32 history steps of a 16,384-float feature (`feat_dim = prod(shape[2:])`) |
| `desire_pulse` | `(1, 32, 8)` | desire history, max-pooled over each `frame_skip` group |
| `traffic_convention` | `(1, 2)` | |
| `action_t` | `(1, 2)` | lateral and longitudinal action horizons the comma builds from its delays |

Output: one tensor `outputs` of 18,452 floats, with a named slice `hidden_state` (16,384 floats) that is **not** part of the driving message: it is fed back
into the next frame's features queue. `frame_skip = MODEL_RUN_FREQ / MODEL_CONTEXT_FREQ = 20 / 5 = 4`.

The comma sends, per frame, only: the newest warped frame of each camera `(2, 6, H, W)` uint8 and the packed scalars `desire` (the model's pulse width), `traffic_convention`,
`action_t` (float32). The phone keeps every queue and the hidden state. The reply is `outputs` **less `hidden_state`** (about 8.3 KB instead of 74 KB).

Per frame on the phone (`queues.py`, `HistoryQueues.step`): shift each of four queues by one and write the new row (`img_q`, `big_img_q` hold `frame_skip * (n_frames - 1) + 1` frames; `feat_q` holds
`frame_skip * 32` rows and takes the **previous frame's hidden state**; `desire_q` holds `frame_skip * 32` pulses), then build the inputs: images and features are every
`frame_skip`-th row of the queues, the desire is the max over each group of `frame_skip`, scalars are cast to the queue dtype (float16 in Jetlink's reference, which gives the same values as casting
at the model boundary because each row is cast once). After a frame whose output is all finite, `prev_feat` = that frame's `hidden_state` floats. A hello zeroes `prev_feat`; a RESET frame zeroes everything.

## The stateful graph (openpilot #38916, Cinque Terre V3 onward)

The graph carries its own history: the newest warped frame goes in as `new_img`, each queue is an input `state_<q>` and comes back as `next_state_<q>`, and the hidden state never leaves
the graph. The phone only has to feed each `next_state_*` back as the next `state_*`. Same wire frame and reply.

## What changes in NPBM

| Piece | v0.10 split (today) | Combined |
| --- | --- | --- |
| Contract id in HELLO / ACK | `ngp-v0.10-split` | `ngp-spec-queued`, later `ngp-spec-stateful` |
| Who knows the layout | the comma (its own metadata) | the phone announces a **ModelSpec** in HELLO_ACK (input and output shapes, output slices, `frame_skip`, sha256); the comma refuses a sha256 it does not know |
| `INFER_REQ` after the header | frame id, flags, desire(8), traffic(2), frame | frame id, flags, then the packed floats the spec lays out (`desire`, `traffic_convention`, `action_t`), then the frame |
| `INFER_RESP` | vision floats + policy floats | `outputs` less `hidden_state` |
| Phone state | frame ring, 100-step feature history, desire history | the four queues + `prev_feat` (queued) or the graph's states (stateful) |

**Safety rule, new with this contract:** the comma must not trust a model because the phone says so. It keeps an allow-list of sha256 values (shipped with the build, or from the model manager) mapped to the output
layout it parses, and refuses anything else. The v0.10 contract is implicitly allow-listed by the comma's own metadata.

## Steps

| Step | Work | State |
| --- | --- | --- |
| 1 | This spec | done |
| 2 | `nagaspilot/bigmodel/spec.py` (`ModelSpec`: queued and stateful layouts, wire form, derived sizes), `queues.py` (`PolicyQueues`), `onnx_meta.py` (graph metadata), adapted from Jetlink with its notice kept. Tests: the queues reproduce, over 300 frames, the digest of everything openpilot's own `shift_and_sample` / `sample_skip` / `sample_desire` fed the model, for the big and the small layout (Jetlink's `policy_queues_openpilot.json`, copied; regenerating it needs openpilot's tinygrad and is done from Jetlink's checkout); the big model's sizes (393,216-byte frame, 12 packed floats, 16,384-float feature, 2,068-float reply) are asserted | done: 13 tests |
| 3 | Protocol: contracts list in HELLO, spec in HELLO_ACK, per-spec `INFER_REQ`/`INFER_RESP`; Python client and server; golden vectors; allow-list on the comma | next |
| 4 | Comma `RemoteModelState` for a spec: scalar building (`action_t` from delays), output slicing, action head, `modelV2` fill for the combined output | after 3 |
| 5 | Phone: Dart `HistoryQueues` and spec-driven `step` in `packages/npbm`, vectors | after 3 |
| 6 | `modeld` itself: constants (`MODEL_RUN_FREQ`/`MODEL_CONTEXT_FREQ`, `ACTION_WIDTH`), `Parser` for the combined output, `fill_model_msg` split, cereal fields. **Needs the real model and its metadata** | blocked on a model file |
