# The combined (Chestnut-class) model contract, step by step

Status 2026-10-12: **Steps 1 to 5b and the stateful layout (5c) are done and tested; only step 6, which needs a real model file, is open.** Following Jetlink's design and tests closely is deliberate: it is open source (MIT) and is the reference the community will compare against.
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

## Which big model to run first (survey of Jetlink and comma's model repo, 2026-10-12)

Source: Jetlink's `catalog/`, `tests/fixtures/catalog_chestnut_v25.json`, `docs/{models,model-cli,jetson-performance,mac-performance,backends,link-protocol}.md`, and the file listing of
`commaai/openpilot_driving_models` on Hugging Face (MIT). The files themselves were **not** downloaded: this environment's proxy blocks Hugging Face file downloads, so nothing below was measured here.

**The models.** Every entry is a comma openpilot commit (a *ref*) whose `big_driving_supercombo.onnx` is the model. A ref resolves to exactly one sha256.

| Model | Size | Layout | Notes (Jetlink's numbers) |
| --- | --- | --- | --- |
| Lebowski (July 2026) | 1,757 MB | queued | Jetson 29.1 ms GPU, 37-40 ms a frame on the comma: least margin in the 50 ms budget. Too heavy for a phone |
| BMRLNAP v1 to v6, Tee Time, Sad Model, I Desire, Happy Birthday (Aug to Sep 2026) | 766 MB | queued | BMRLNAP v6: Jetson 15.7 ms GPU, 24 ms a frame on the comma |
| Cinque Terre, Cinque Terre V2 (Sep 2026) | 766 MB | queued | V2 on a 16 GB M1 Pro: 30.7 ms mean / 32 to 37 ms p99, 0 of 1,160 frames over 50 ms; same GPU time as BMRLNAP |
| **Cinque Terre V3 (Jetlink's default)** | 766 MB | **stateful** (openpilot #38916) | Jetson 16.4 ms GPU, 24.5 ms a frame; M1 Pro 30.6 / 33.8 ms. From V3 on, the ONNX is in comma's Hugging Face repo |
| ResAction Preview, Mountain Dew V2 Preview (Oct 2026, Jetlink's `extra_big_models.json`) | not stated | newest | previews comma may change or withdraw |

**What is in comma's repo:** three `big_driving_supercombo.onnx` files, in UUID folders (`1a421175...`, `870a4823...`, `f78ed37d...`, each under `12864/`): 766,320,181 bytes, 792,487,890 bytes and 766,354,845 bytes. Which folder is which model is not
written anywhere I can read (the mapping is the ref's pointer file; Jetlink's `jetlink-server models resolve <ref>` shows it). Their sizes put two of them in the 766 MB class and one a little larger.

**The phone number that exists:** Jetlink claims 14 ms a frame on an iPhone 18 Pro (Neural Engine plus GPU, ONNX Runtime with CoreML) for its default; the Mac default is 30 ms. No Android layout has run on a Snapdragon, and the first Pixel (Tensor G5) was refused.
So the 766 MB class fits a recent Pro iPhone on Jetlink's evidence and nothing else is established.

**Consequence for this work.** The newest models, the ones on Hugging Face and the default, are **stateful**. What I built and tested serves the **queued** layout (BMRLNAP, Cinque Terre V2, Lebowski). So:

1. **A queued 766 MB model (BMRLNAP v6 or Cinque Terre V2) can go through the whole path as it stands** once its ONNX is in hand: `allow_model.py` makes the entry, the phone runs it.
2. **Cinque Terre V3 and everything after it is stateful** (`new_img` in, each `state_<q>` in and `next_state_<q>` out fed back on the phone, the hidden state never leaving the graph, no queues). **Served now** (step 5c): contract `ngp-spec-stateful`, `StateLoop` on the Python reference and in Dart, the allow-list tool accepts it, and a Python comma ran 12 frames against the Dart server with no mismatch. Verified only against stand-in graphs, not against Cinque Terre V3 itself.
3. Whichever model is chosen, step 6 (parser names and shapes, the `action` head and its units, the constants) can only be settled against that file.

## Steps

| Step | Work | State |
| --- | --- | --- |
| 1 | This spec | done |
| 2 | `spec.py` (`ModelSpec`: queued and stateful layouts, wire form, derived sizes, NavPilot's `reply_slices`), `queues.py` (`PolicyQueues`), `onnx_meta.py`, adapted from Jetlink with its notice kept. The queues reproduce, over 300 frames, the digest of everything openpilot's own `shift_and_sample` / `sample_skip` / `sample_desire` fed the model for the big and the small layout (Jetlink's `policy_queues_openpilot.json`, copied) | done |
| 3 | Protocol: HELLO lists the contracts the comma accepts and the server picks the first it supports; HELLO_ACK carries the contract and, for `ngp-spec-queued`, the model spec; `INFER_REQ` is now frame id, flags, the contract's packed floats, then the frame (`pack_infer_req_packed`; the split contract's bytes are unchanged); the reply is the output less `hidden_state`. Comma side: `allowlist.py` (sha256 plus pinned frame_skip, input and output shapes and slices; ships empty), `Client` refuses a model that is not listed, differs in a pinned field, or announces frames other than the comma's warp. Phone side: `server_ref.py` serves both contracts (`StubSpecBackend`, `step_spec`). Golden vectors for both | done |
| 4 | Comma `RemoteSpecModelState`: packs `desire` (rising-edge pulse, slot 0 cleared), `traffic_convention` and `action_t` as the spec lays them out; cuts the reply with `reply_slices`; hands the sliced dict to `Parser.parse_outputs`; holds, hand-back and the rest are the split state's. `BigModelHook` offers the queued contract only when the allow-list is non-empty and builds the right state from the phone's reply | done |
| 5 | Phone: Dart `ModelSpec`, `PolicyQueues`, `SpecBackend`, `stepSpec` and the queued server in `packages/npbm`. Ten frames reproduce the Python replies exactly; a Python comma (`RemoteSpecModelState`) ran 12 frames against the Dart server with no mismatch | done |
| 5b | `nagaspilot/bigmodel/tools/allow_model.py`: turns a model's ONNX into an allow-list entry (refusing what the comma cannot serve, requiring evidence) | done |
| 5c | The stateful layout: `StateLoop` (states advance only after a finite frame, a deliberate difference from Jetlink's in-engine loop), contract `ngp-spec-stateful` (the client refuses a contract that does not match the graph), `StubStatefulBackend`, the Dart port, golden vectors, the allow-list tool accepting state pairs | done |
| 6 | **`modeld` itself**, which needs a real model file and its metadata: add the entry with the tool, check the 18,452-float output against `Parser.parse_outputs` (names, shapes, the `action` head and its units), the constants (`MODEL_RUN_FREQ`/`MODEL_CONTEXT_FREQ`, `ACTION_WIDTH`), `fill_model_msg` for a combined output, cereal fields, then replay a recorded route through the phone and the small model and compare | **blocked on a model file** |

The phone's inference backend is still the open piece (`NAVPILOT_BIGMODEL.md`, backend survey). The phone's inference backend is still the open piece
(`NAVPILOT_BIGMODEL.md`, backend survey).
