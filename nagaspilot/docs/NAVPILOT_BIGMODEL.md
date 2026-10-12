# Big model on the phone: NavPilot-owned link (port plan and status)

Status 2026-10-12: **comma side written and unit-tested on a PC (159 tests, loopback sockets and a fake configfs); the phone side is specified here and has a Python
reference server; nothing has run on a device and the Swift server is not written.** Branch `native/ngp10-bigmodel`, off `jetlink/ngp10-bigmodel`.

This replaces [`JETLINK_BIGMODEL.md`](JETLINK_BIGMODEL.md). Decision (2026-10-12): NavPilot owns the whole feature. There is
no dependency on `exo-elec/jetlink`: no `jetlink_repo` gitlink, no Jetlink protocol, no JetlinkKit. What the Jetlink port
already settled on the openpilot side (cereal slots, the `selfdrived` gate, the settling window, the `modeld` hook, the
OpenCL warp source, the Chestnut-parity policy, the alerts) is kept unchanged. What Jetlink supplied is rewritten here.

Phone side lives in NavPilot (`navpilot/docs/architecture/BIGMODEL_WIRED_PLAN.md`, updated to point here).

## Decisions

| # | Decision | Why |
| --- | --- | --- |
| D1 | **Model contract A: the phone runs the same v0.10 split vision + policy contract NGP10 already parses.** A contract id travels in the hello, so a later combined-model contract can be added without a protocol break. | No `modeld` rework, reuses NGP10's `Parser`, `fill_model_msg` and warp. Proves the whole pipeline. Gains capability only when a larger model with this contract exists. |
| D2 | **One transport: TCP over a CDC-NCM USB network.** The comma owns `192.168.60.1:5600` and listens on that address only; the phone gets an address by DHCP and **dials** in. The protocol roles do not follow the TCP roles: the comma sends HELLO and asks for frames. No FunctionFS, no vendor interface. | iPhone needs it; NavPilot's `CableTransportService` already assumes it; configfs NCM is a few lines instead of Jetlink's FunctionFS stack. Android, Lightning iPhones and USB-host-only computers are out of scope (D7). |
| D3 | **The phone keeps the history; the comma sends only the newest warped frame per camera** (393,216 bytes) plus `desire` and `traffic_convention`. | Same bandwidth reasoning as Jetlink (a per-frame second frame would double a cost the comma's kernel pays per packet). The server's state is rebuilt by a RESET flag, so it is never trusted across a restart. |
| D4 | **The reply is the raw vision output followed by the raw policy output** (float32), sliced on the comma with NGP10's own metadata. | The comma keeps the parser, so output parity is checked in one place and the server needs no openpilot knowledge. |
| D5 | **Fallback behaviour is unchanged from the Jetlink port**: the small model is always loaded; one-way hand-back; engagement held off while loading/proving; a hand-back while engaged soft-disables; settling window 5 s. | Already decided and documented in `CHESTNUT_PARITY.md`. |
| D6 | Setting `ngp_bigmodel_link` stays, but is now **0 off / nonzero on** (no iOS vs USB split). | One transport. Old value 2 still reads as on. |
| D7 | **Scope (2026-10-12): iPhone with a USB-C port only. No Lightning, no Android.** Android would need a second comma transport (FunctionFS vendor interface read through usbfs, as Jetlink's Android app does) because it does not reliably host a CDC-NCM gadget; that is dropped, not deferred. USB 3 speeds need an iPhone 15 Pro or later; the base iPhone 15 and 16 have USB-C at USB 2 speed and will raise the existing `USB slow` alert. | Owner decision. Keeps the single NCM transport of D2. |

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
2. Native iOS (`ios/Runner`, Swift): the NPBM server state machine dialling the comma on the NCM interface, and a CoreML
   backend. No Android module.
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
| P5 | Phone side: Dart contract, Swift server. | Needs Xcode: not buildable in this environment. |
| P6 | Device: comma 3X bench. | p99 frame < 50 ms with NavPilot's map running, 10+ min heat, forced-failure hand-backs. |

## Unverified (needs hardware or a recorded route)

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
