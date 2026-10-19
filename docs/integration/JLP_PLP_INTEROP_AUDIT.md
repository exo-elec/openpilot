# NavPilot JLP and PLP interoperability audit

Checked 2026-10-19. **The pair is not yet integrated seamlessly.** PLP's current
core/Locate library traffic interoperates; upstream JLP v3 is not implemented
by the current NGP10/EOP10 or NavPilot inference link. This audit adds a
repeatable cross-language test, not a new production protocol adapter.

JLP means JetLink Protocol here; upstream's wire magic is `JLNK`. PLP means
Pilot Link Protocol v5. They are separate parallel protocols. Inference frames
belong to JLP; companion navigation/telemetry/services belong to PLP.

## Inspected and fetched sources

- Openpilot NGP10 `4728d8add`; EOP10 `c29b3b759` before this audit.
- NavPilot master `572a481647b730c88deb5f17b49b72194da46a48`, matching fetched
  origin/master. Its existing working-tree differences disappear when ignoring
  end-of-line whitespace; none were normalized, committed or overwritten.
- Local JetLink checkout `a34ad5f3c38546d0915b112939f51a8cb4f57b18` was inspected;
  canonical upstream/main was fetched independently to
  `d24328d849b0e5154b55f149dc18c5dfe8bd79cc`. The checkout was not switched.
  Primary source: [upstream protocol at the fetched revision](https://github.com/zoompilot/jetlink/blob/d24328d849b0e5154b55f149dc18c5dfe8bd79cc/jetlink/protocol.py).

## Compatibility findings

| Boundary | Actual implementation | Result |
| --- | --- | --- |
| Upstream JLP | `JLNK`, version 3, 32-byte `<IHHIIIQ4x>` header | Required target, not current inference protocol |
| NGP10/EOP10 inference | `nagaspilot/bigmodel/protocol.py`: `NPBM`, version 1, 20-byte header | Not wire-compatible with JLP |
| NavPilot inference | `packages/npbm/lib/src/protocol.dart`: same NPBM v1; BigModelLinkService supports protocol 1 | Matches legacy host protocol, not JLP v3 |
| Native phone inference | Channel interface exists in Dart; Android/iOS shells have no matching custom channel registration or JetLink engine integration | No demonstrated native JLP engine |
| PLP USB library | Dart `PlpTcpLink` and Python `CablePLP`, TCP 5601 | Real cross-language hello, negotiated limits and Locate delivery pass |
| PLP application lifecycle | App providers do not attach PlpTcpLink/PilotLinkManager; Client.plpLocate has no live app assignment | Library success is not app integration |
| PLP BLE | Host GATT shell and stream codec exist; NavPilot BLE transport remains legacy NCP | Phone PLP BLE adapter and device bench remain open |
| Service coverage | USB and BLE host hellos advertise only `core` and `locate` | Registry entries do not prove nav/vehicle/OBD/pairing/radar/auth/voice handlers |
| Android cable PLP | Existing host PLP server is the USB-network path | Second bulk interface and app byte pipe remain unbuilt |

The protocol audit loaded the exact fetched upstream Python codec and current
host NPBM codec. Each rejects the other's header. Renaming a service or changing
one version constant would not migrate handshake, tensor/model ABI, recurrent
state, framing, padding or message types.

For network-mode coexistence, JLP's default TCP endpoint is 5599, current NPBM
inference uses 5600, PLP uses 5601, and the legacy NCP cable service uses 5598.
Use one physical gadget/interface owner and separate sessions. Do not put PLP
messages into inference frames or start competing USB gadget setup scripts.
Bulk endpoints and interface identities need separate Android conformance.

## Vehicle-owned local fallback

Current BigModelFacade/NgpSmallModel and engagement-gate tests pass (52 cases).
Selection remains on the vehicle; phone disconnection must not transfer ADAS
authority to NavPilot. These are current legacy-link tests, not JLP validation.

There is also a policy distinction to reconcile before migration: the existing
`NGPBigModelGate` soft-disables after an active phone model hands back, even
though local-model output becomes available. It is not an uninterrupted-
engagement guarantee. The owner's CPU/RKNN compute-failure policy does not by
itself prove that this inherited model-source handover matches the intended
behavior. Reconcile and test the shared baseline first; do not silently change
an EOP-only copy or remove model/control validity guards.

## Tests actually run

| Check | Result | Scope |
| --- | --- | --- |
| Python PLP codec/session/BLE stream | 39 passed | Host logic and local sockets; no BlueZ hardware |
| Current NavPilot PLP package | 25 passed | Fresh isolated package copy, real Dart TCP loopback |
| Dart PLP static analysis | No issues | `lib` and `test`, not the full Flutter/native application |
| Dart -> Python interop | Passed | Real CablePLP hello, 64-byte negotiation, GNSS and fragmented map horizon accepted by LocateSink |
| Header incompatibility check | Passed | Exact upstream JLP and current NPBM reject one another |
| Current host model hook/gate | 52 passed | Existing fallback/join/engagement behavior, not a new JLP adapter |

Repeat the cross-language test from openpilot:

```bash
python3 tools/protocol/check_navpilot_plp_interop.py \
  --navpilot-root ../navpilot \
  --dart /path/to/dart \
  --pub-cache /path/to/existing/pub-cache
```

The tool copies the current sibling PLP package into a temporary directory,
resolves already-cached dependencies offline and starts a loopback-only Python
host with an actual Dart client. It does not modify NavPilot, install an SDK,
configure a gadget, run an inference engine or prove a physical USB/BLE link.

## Remaining integration order

1. Implement and test the pinned upstream v3 adapter in NGP10's
   `nagaspilot/jetlink`, including model ABI, queue reset/history, invalid output,
   deadlines and local vision/policy handover. EOP10 inherits shared policy.
2. Add the matching real NavPilot native JLP engine/transport and register its
   app boundary. Unsupported engines must remain unavailable.
3. Attach PLP USB and BLE through one live app connection/session owner and a
   lifetime-aware service facade. Disconnect must invalidate capabilities and
   pending requests; switching must not duplicate commands.
4. Implement matching baseline host/phone handlers service by service. USB
   carries the same advertised services as BLE, with negotiated richer limits.
5. Run real iOS/Android + NGP/EOP tests with JLP and PLP simultaneously: unplug,
   timeouts, stale/nonfinite inference, backgrounding, reconnect, MTU changes,
   pairing/authorization, thermal load and concurrent-frame latency.

No production wiring or native implementation was changed by this audit. The
existing full openpilot test gate remains blocked by the separately recorded
`nagaspilot/runtime/bigmodel_platform.py` portable-source mismatch.
