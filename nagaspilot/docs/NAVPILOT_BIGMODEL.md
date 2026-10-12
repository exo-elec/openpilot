# Big model on the phone: NavPilot-owned link (port plan and status)

Status 2026-10-12 (end of day): **the comma side is merged on `dev/NGP10` and carried onto `dev/EOP10`, `dev/01M` and `dev/02M`; the phone core is a pure-Dart package in NavPilot; nothing has run on a comma, a board or a phone.**
Everything is default off (`ngp_bigmodel_link` = 0). 199 comma-side tests and 16 Dart tests pass on a PC; the bulk and FunctionFS paths have only run against fakes.

This replaces [`JETLINK_BIGMODEL.md`](JETLINK_BIGMODEL.md). Decision (2026-10-12): NavPilot owns the whole feature. There is
no dependency on `exo-elec/jetlink`: no `jetlink_repo` gitlink, no Jetlink protocol, no JetlinkKit. What the Jetlink port
already settled on the openpilot side (cereal slots, the `selfdrived` gate, the settling window, the `modeld` hook, the
OpenCL warp source, the Chestnut-parity policy, the alerts) is kept unchanged. What Jetlink supplied is rewritten here, following
Jetlink's design lessons with attribution (`nagaspilot/bigmodel/THIRD_PARTY_NOTICES.md`; Jetlink is MIT and the project is open source).

Phone side lives in NavPilot (`navpilot/docs/architecture/BIGMODEL_WIRED_PLAN.md`, updated to point here).

## Decisions

| # | Decision | Why |
| --- | --- | --- |
| D1 | **Model contract A: the phone runs the same v0.10 split vision + policy contract NGP10 already parses.** A contract id travels in the hello, so a later combined-model contract can be added without a protocol break. | No `modeld` rework, reuses NGP10's `Parser`, `fill_model_msg` and warp. Proves the whole pipeline. Gains capability only when a larger model with this contract exists. |
| D2 | **iPhone transport: TCP over a CDC-NCM USB network** (Android: D7). The comma owns `192.168.60.1:5600` and listens on that address only; the phone gets an address by DHCP and **dials** in. The protocol roles do not follow the TCP roles: the comma sends HELLO and asks for frames. No FunctionFS, no vendor interface. | iPhone needs it; NavPilot's `CableTransportService` already assumes it; configfs NCM is a few lines instead of Jetlink's FunctionFS stack. Android uses the other transport (D7); Lightning iPhones, Wi-Fi and USB-host-only computers are out of scope. |
| D3 | **The phone keeps the history; the comma sends only the newest warped frame per camera** (393,216 bytes) plus `desire` and `traffic_convention`. | Same bandwidth reasoning as Jetlink (a per-frame second frame would double a cost the comma's kernel pays per packet). The server's state is rebuilt by a RESET flag, so it is never trusted across a restart. |
| D4 | **The reply is the raw vision output followed by the raw policy output** (float32), sliced on the comma with NGP10's own metadata. | The comma keeps the parser, so output parity is checked in one place and the server needs no openpilot knowledge. |
| D5 | **Fallback behaviour is unchanged from the Jetlink port**: the small model is always loaded; one-way hand-back; engagement held off while loading/proving; a hand-back while engaged soft-disables; settling window 5 s. | Already decided and documented in `CHESTNUT_PARITY.md`. |
| D6 | Setting `ngp_bigmodel_link` stays, but is now **0 off / nonzero on** (no iOS vs USB split). | One transport. Old value 2 still reads as on. |
| D7 | **Scope (2026-10-12, revised): iPhone with a USB-C port and Android with USB-C, both over the USB-C cable. No Lightning, no Wi-Fi.** Two comma transports carry the same NPBM bytes, chosen by `ngp_bigmodel_link` (2 = iPhone: CDC-NCM network, TCP; 1 = Android: FunctionFS vendor bulk interface that the phone reads as USB host, with `framing.py` padding). An iPhone 15 Pro or later gives USB 3; the base iPhone 15/16 are USB 2 and raise the `USB slow` alert. | Owner decision, reversing the iPhone-only scope of the same day. Android cannot be reached over NCM, and tethering/Wi-Fi are less reliable than a cable. |
| D8 | **(revised) The phone core is pure Dart, in the Flutter app.** Owner decision: accept the performance risk for the lower long-run maintenance of one language and one codebase. `packages/npbm` in the NavPilot repo is a Flutter-free Dart package (`dart test` runs it): protocol, the model-history state machine, the server loop and tether-interface choice. It is byte-identical to the Python reference on every golden vector, and a Python comma client ran six frames against it bit-exactly. The only native part is the inference backend behind `Backend` (CoreML / ONNX Runtime / NNAPI / LiteRT through a Flutter plugin or a thin FFI), chosen in a spike. Measured on a PC with the stub backend, the Dart protocol and history work is about 2.5 to 3.5 ms per frame (JIT, one core, no inference); inference time on a phone is the open number. | Replaces the shared-C++-core plan. Dart does the 393 KB copies and the 100-step history cheaply; only inference is heavy and it was always native. |
| D9 | **USB tethering is the default and recommended link (`ngp_bigmodel_link` = 3).** The phone is the USB *device* and shares a network (Android USB tethering, iPhone Personal Hotspot over USB); the comma is the *host*. Android and iPhone then use the same transport, plain TCP, so the app needs no USB code: the app listens on the tether interface (`findTetherAddress`, never on Wi-Fi or cellular) and the comma dials the phone's gateway address (`tether.py`). It also keeps the comma as USB host, the role it already has for a Chestnut, and the host powers the phone. Modes 2 (iPhone as host of the comma's NCM gadget) and 1 (Android as host of the FunctionFS bulk gadget) stay as fallbacks, unverified, and can be deleted once tethering is proven. | Removes the Android-specific USB transport and the gadget work from the critical path; fits ExoPilot, whose boards are USB hosts on that port. |

## Architecture

```
NavPilot app (phone)                                   comma 3/3X, NGP10
  native model server  ---- dials TCP 192.168.60.1:5600 --->  nagaspilot/bigmodel/listener.py -> client.py
   (CoreML / ORT+QNN)         over CDC-NCM on USB          |
   history: frames, features, desire                       remote_state.RemoteModelState  (one inference = one frame)
                                                           joining.Joining  (swap, hold, hand back, backoff)
                                                           runtime/bigmodel.py  BigModelFacade  -> modeld.py hook (unchanged)
                                                           selfdrived NGPBigModelGate (unchanged)
  bigmodeld (manager process): configfs NCM gadget + 192.168.60.1/24 + DHCP, stands aside for a Chestnut
```

## Wire protocol (NPBM v1, `nagaspilot/bigmodel/protocol.py`)

All integers little-endian. Header, 20 bytes: `magic "NPBM"` (4), `version u8`, `type u8`, `flags u16`, `seq u32`,
`length u32`, `hcrc u32` (crc32 of the first 16 bytes). Payload follows; its length is `length`. A header with a bad
magic, version or crc is a broken stream: both sides drop the connection. TCP and USB already checksum payloads.

| Type | Dir | Payload |
| --- | --- | --- |
| `HELLO` 1 | comma -> phone | JSON `{"contract": "ngp-v0.10-split", "frame": [2,6,128,256], "desire": 8, "traffic": 2, "fps": 20}` |
| `HELLO_ACK` 2 | phone -> comma | JSON `{"ok": bool, "error"?, "model": name, "sha256", "vision_floats": n, "policy_floats": n, "ready": bool}` |
| `INFER_REQ` 3 | comma -> phone | `frame_id u32`, `req_flags u32`, `desire f32[8]`, `traffic f32[2]`, frame `u8[2*6*128*256]` |
| `INFER_RESP` 4 | phone -> comma | `frame_id u32`, `status u32`, `server_us u32`, `f32[vision_floats + policy_floats]` (empty when `status != 0`) |
| `PING` 5 / `PONG` 6 | both | empty |
| `LEAVE` 7 | comma -> phone | JSON `{"reason": "lost"\|"behind"\|"stopped", "stats": {...}}`, best effort |
| `ERROR` 8 | phone -> comma | JSON `{"error": text}` then the phone closes |

`req_flags`: bit0 `RESET` (drop all history; the first frame after a swap and after any hand-back). `status`: 0 ok,
1 `NOT_FINITE` (the phone's own check; it keeps its last good state), 2 `NOT_READY`, 3 `INTERNAL`.

Phone state: per camera a ring of the last `TEMPORAL_SKIP+1` frames (older = `skip` frames back, newest), the feature
history `(FULL_HISTORY_BUFFER_LEN, 512)` and the desire history, advanced exactly as `ModelState.run` advances them, one step
per `INFER_REQ`. A reply that is not finite leaves the phone's state as it was before the frame.

## Comma-side modules (all under `nagaspilot/bigmodel/`, standard library + numpy at import)

| Module | Replaces (Jetlink) | Does |
| --- | --- | --- |
| `protocol.py` | `jetlink/protocol.py` | Header, message codecs, constants. Pure, golden-vector tested. |
| `framing.py` | `transport/` | Bulk framing for Android: burst padding out, one pad byte in, `BulkSocket` (comma) and `HostBulkSocket` (the phone's spec). |
| `ffs.py` | `transport/ffs.py` | FunctionFS descriptors, `FfsControl` (ep0 owner in `bigmodeld`), `FfsEndpoint` (signal-masked IO threads in `modeld`), `FfsListener`. **Not run on a device.** |
| `tether.py` | (new) | Mode 3: finds the tether interface by kernel driver (rndis_host, cdc_ncm, cdc_ether, cdc_eem, ipheth), reads the phone's gateway address from `/proc/net/route`, dials the app. |
| `listener.py` | `transport/` | Binds the cable address only (retried until the gadget's netdev exists), keeps the newest phone connection. |
| `client.py` | `jetlink/client.py` | Blocking client over the accepted socket: hello, `send_frame`, `recv_response(deadline)`, ping, leave. Every failure is a `LinkError`. |
| `metadata.py` | (jetlink spec) | Reads NGP10's own model metadata for the slices and float counts the reply is checked and sliced with. |
| `remote_state.py` | `openpilot/model_state.py` | `RemoteModelState`: takes the warped frame from the small model's `DrivingModelFrame`s, sends it, parses the reply with NGP10's `Parser`, holds a late frame, flags `behind` (5 in a row, >20 in 10 s, first frames settle). |
| `joining.py` | `openpilot/joining.py` | `Joining`: background connect with backoff, swap only when nothing is in control and after the small model's warm-up frames, demote on lost link / lag / frame-drop ratio, rejoin policy hooks. Same public surface the facade already reads. |
| `gadget.py` + `scripts/ngp-bigmodel-root.sh` | `comma/gadget.py`, `jetlink-root.sh` | configfs NCM gadget, `192.168.60.1/24`, dnsmasq DHCP. `GadgetOwner` (manager process `bigmodeld`) creates, binds and addresses it while the setting is on, stands aside for a Chestnut, and redoes the netdev step when a replug rebuilds it. No PD role-swap logic yet (see unverified). |
| `server_ref.py` | JetlinkKit | Reference phone server in Python: same protocol and state, a pluggable backend (ONNX Runtime CPU for real models, a deterministic stub for tests). `--dial 192.168.60.1` is the phone's mode. It is the executable spec for the Swift and Kotlin servers. |

Rewired: `runtime/bigmodel.py` (the hook reads the metadata, starts the listener and builds `Joining`; `InControl` replaces the
adapter's `in_control`), `runtime/bigmodel_warp.py` (`NgpWarp` and `reset_function` stay, the Jetlink `WarpSource` goes),
`runtime/bigmodel_face.py` (only the action function stays), `runtime/bigmodel_platform.py` (Jetlink's JSON param keys go),
`system/manager/process_config.py` (`bigmodeld` replaces `jetlinkd`), `common/params_keys.h`. Removed: the `jetlink_repo` gitlink and
`.gitmodules` entry, `runtime/jetlink_adapter.py`, the Jetlink entry in `DEPENDENCY_POLICY.md`. Unchanged: `modeld.py` (one comment),
`selfdrived`, cereal, the gate, status and alert code.

## Phone-side modules (NavPilot repo)

1. Dart `BigModelLinkService`: controls and status for the UI. Its channel contract changes from Jetlink's `ControlEvent`s to
   NavPilot's own (`start`, `stop`, `status`, `model`, events `link`, `engine`, `stats`).
2. The shared native core (D8) with an iOS build (CoreML) and an Android build (usbfs bulk pipes, QNN/NNAPI/LiteRT), called from Flutter through `dart:ffi`.
   Jetlink's `JetlinkKit` (MIT) is a worked reference for both, including its usbfs pipes and ORT profiles; copying from it needs its notice kept.
3. Model delivery: NavPilot downloads or bundles the split vision/policy models and converts them to the device runtime.

The Python reference server is the conformance target: the same golden vectors (`protocol` fixtures, a recorded
request/response pair) must pass.

## Phases and exit criteria

| Phase | Work | Exit |
| --- | --- | --- |
| P0 | Plan (this file). | Reviewed. |
| P1 ☑ | `protocol.py`, `client.py`, `listener.py`, `server_ref.py` with a stub backend, loopback tests. | Round trip over loopback; bad header, truncation, timeout, reset flag, non-finite status, dial-in and newest-connection-wins all tested. |
| P2 ☑ | `remote_state.py`, `joining.py` with fakes for sockets, clock and the small model, and one end-to-end test over a real socket. | Swap, hold, hand-back, backoff and drop-ratio rules tested against a fake host. |
| P3 ☑ | Rewire the hook, manager, docs; remove Jetlink. | Facade/gate/status tests pass; the two real-joining-model integration tests now run on `Joining`. No Jetlink import or checkout anywhere. |
| P4 ☑ | `gadget.py`, root script. | Script run against a fake configfs (gadget, bind, net before and after the netdev, refusal to fight another gadget, teardown); owner decisions tested. |
| P4b ☑ | Android transport: `framing.py`, `ffs.py`, `gadget --bulk`, owner mode switch, listener choice by setting. | Protocol round trip over bulk framing, over real pipes through `FfsEndpoint`; descriptors checked against the FunctionFS v2 layout; script against a fake configfs. |
| P5 | Phone side: Dart contract, shared native core (iOS and Android builds) behind `dart:ffi`. | Needs Xcode and the Android SDK/NDK: not buildable in this environment. |
| P6 | Device: comma 3X bench. | p99 frame < 50 ms with NavPilot's map running, 10+ min heat, forced-failure hand-backs. |

## Task list (2026-10-12)

| # | Task | State |
| --- | --- | --- |
| 1 | Comma side: protocol, client, state, joining, three transports, gadget script, owner, hook | ☑ merged on NGP10, carried to EOP10, 01M, 02M |
| 2 | Python reference server and golden vectors | ☑ |
| 3 | Dart core `packages/npbm` (NavPilot): protocol, history, server, tether interface choice | ☑ 16 tests, bit-exact with the Python client |
| 4 | ExoPilot kernel drivers for tethering (`feat/usb-tether-host`) | ☑ merged on exopilot `main`, not built |
| 5 | Comma check: kernel USB Ethernet host drivers, comma holds its port as host | ☐ needs a comma 3X |
| 6 | Phone inference backend spike (see the survey below) on one iPhone Pro and one Android flagship, with a real model | ☐ needs phones |
| 7 | Wire `packages/npbm` into the app: dependency, host service ☑ (Flutter tests pass); provider registration and the tethering guide screen ☐ next |
| 8 | `modeld` rework: combined-model contract as a second contract id, in six steps: [`NAVPILOT_COMBINED_MODEL.md`](NAVPILOT_COMBINED_MODEL.md). Steps 1 to 5c done and tested (spec, queues against openpilot's own digests, contract negotiation with a comma allow-list, `RemoteSpecModelState`, the Dart queued and stateful servers, the allow-list tool); step 6 (`modeld` itself) needs a real model file | ◐ done except step 6 |
| 9 | Replay a recorded route: warp parity and phone output parity | ☐ needs a route and a model |
| 10 | Bench on a board: p99 under 50 ms, 10+ min heat, forced-failure hand-backs | ☐ needs hardware |
| 11 | Remove the eGPU / Chestnut path | ☐ gated, see below |

## Inference backend survey (pub.dev, 2026-10-12)

The Dart core needs one native piece, `Backend` (run the vision and policy models). Packages that exist:

| Package | Engine | Fit |
| --- | --- | --- |
| `flutter_onnxruntime` 1.9.0 (published 2026-10-06, ORT 1.28) | iOS: `onnxruntime-objc` (CoreML EP available); Android: the stock `onnxruntime-android` AAR (XNNPACK, NNAPI; **no QNN**) | The closest drop-in. Method-channel API, so each frame's tensors are copied through a channel: measure. Does not carry the model-preparation work Jetlink needed for the Neural Engine (fp16-safe LayerNorm scaling, a vision/heads split). |
| `onnxruntime_plus` 1.5.0 | FFI over ORT **1.15.1** (old) | FFI avoids channel copies, but the engine is years behind the 1.28 / 1.29 the models were validated on. |
| `onnxruntime` 1.4.1 (2024) | ORT 1.15.1 | Stale. |
| `flutter_litert` 3.9.4 (published 2026-10-12) | LiteRT through FFI: XNNPACK, GPU, Metal, CoreML, NPU delegates | Needs the model converted to `.tflite`; Jetlink did that conversion on the phone for Android and found it a project of its own (op lowering for the GPU delegate). |

Nothing off the shelf gives a ready Android NPU path for a Chestnut-class model. Plan on a thin NavPilot native layer (an FFI to the ORT C API,
the way Jetlink's Swift does it) for production, and use `flutter_onnxruntime` only to get a first number on an iPhone.

## Removing the eGPU / Chestnut path (gated, not done)

Owner intent (2026-10-12): if a phone can run a Chestnut-class model, drop the GPU and keep only the phone backend. Not yet met: no phone has run such a model for us,
NGP10 cannot consume the combined model (task 8), and Android is unproven (Jetlink reports no Snapdragon run). Remove only when **all** hold:

1. Task 8 is done and a real combined model runs through the phone backend.
2. One iPhone and one Android phone run it on the bench for 10+ minutes, p99 frame under 50 ms, with the map running (task 10).
3. A drive test shows the hand-back to the small model.

Then, one reviewed change per branch in the order NGP10, EOP10, 01M, 02M: `selfdrive/modeld/egpu_detect.py` and its test; the eGPU parts of `modeld.py`
(`EgpuState`, the `egpuState` service and cereal message); `nagaspilot/runtime/tinygrad_model.py` and its test; the `Egpu*` params; the eGPU panel
(`selfdrive/ui/qt/offroad/ngp_panel.cc`); the SConscript entries; `EGPU_INTEGRATION.md` and `NGP10_CHESTNUT_MIGRATION_PLAN.md`; the Chestnut-parity shim in the gate;
`egpu_in_use()` in the platform file. In exopilot: the eGPU overlays, `EGPU_ASM2464PD.md` and `usb_c_port_mode.sh`.

## Unverified (needs hardware or a recorded route)

- Tethering (D9): the comma kernel must carry the USB ethernet host drivers (check `zcat /proc/config.gz | grep -E 'USB_NET|IPHETH|RNDIS'` on a comma 3X; ExoPilot's `exopilot-usb-tether.config` adds them). An iPhone's Personal Hotspot over USB on Linux also needs `usbmuxd`/`libimobiledevice` pairing (the Trust prompt), which AGNOS may not carry: if it does not, the iPhone uses mode 2. The comma must hold its USB-C port as host (the Jetlink port code solves the opposite role); NetworkManager must keep the tether's default route at metric 900 so it never replaces the uplink. The phone's interface names (`findTetherAddress`) are heuristics to confirm on devices.

- The FunctionFS path (`ffs.py`, `gadget --bulk`) has only run against pipes and a fake sysfs. Known device-only risks: the descriptor write
  (EINVAL on a wrong layout), a wedged gadget if an endpoint is opened early, writes that never time out, the mount's ownership, and
  switching functions on a live controller. Jetlink's `transport/ffs.py` records the lessons; its AIO writes (not used here) were needed to
  keep a frame's write off the caller's thread.

- `modeld.py` has only been byte-compiled, and `attach()` was exercised with a stubbed `swaglog` and a fake listener; the real `Parser`, cereal and
  OpenCL objects were not importable in this environment. The first run is on the bench.
- Frame cost: `pack_infer_req` copies the 393 KB frame once and `sendall` runs on modeld's frame thread.

- Everything on a device: AGNOS configfs/NCM support on the comma 3, the iPhone's NCM enumeration, DHCP, USB 3 speed.
- USB-C role handling: Jetlink forces the comma to be the sink/device and requests PD data-role swaps for dual-role far ends.
  That logic is not ported; a C-to-C phone that wins the host role will not enumerate us until it is.
- Output parity of the OpenCL warp against the model's training warp, and the phone model's outputs against the comma's
  small-model contract (replay of a recorded route).
- Frame cost of a 393 KB TCP send on modeld's thread against the 50 ms budget; a sender thread or UDP split may be needed
  (Jetlink measured 7.35 ms to 4.3 ms with UDP).
- Own USB VID/PID before distribution (no PID is allocated; the gadget uses the Linux Foundation test VID 0x1d6b / 0x0104 for bench only).
