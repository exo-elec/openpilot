# PLP v5, the Pilot Link Protocol: design and specification (draft 1)

Status: draft for owner review. The framing, registry, session, hello, USB cable server and locate handlers are implemented and tested on the comma side (`nagaspilot/plp/`, 42 tests) and the registry is shared with the phone (`packages/plp/registry/plp_registry.json`). The Dart framing, the BLE transport, the Android bulk transport and every on-device or on-phone run are **not** done. Items marked **[verify]** come from secondary sources or are my inference and must be checked on hardware or against the primary document before they become requirements.

## 1. Purpose and history

PLP is the link between a pilot device (comma 3 running NGP10; ExoPilot 01M and 02M running EOP10 and up) and the NavPilot phone app. It carries navigation, vehicle and OBD data, perception status, radar pairing, auth, voice, the phone's GNSS fix and its map horizon, over a USB-C cable and, on ExoPilot hardware, over BLE.

It replaces **NCP** (NavPilot Companion Protocol; the v3.x docs even expand it as "Communication"), which grew message by message from a navigation link. Problems that PLP fixes, all from the code:

| NCP v4.1 today | PLP v5 |
|---|---|
| flat 16-bit type with ad hoc ranges and 0x06xx leftovers; direction only by naming convention (`voice.tts` was listed with phone-to-device commands) | `(service, message)` from one registry; direction recorded in the registry and enforced |
| `RESPONSE_ACK` without a request id | request id in every request, response and error |
| version string repeated inside each JSON body, no negotiation | one hello per link; per-service message-set versions |
| unknown type: undefined | critical or elective, with a defined error |
| JSON only, 64 KB frame cap, CRC-16 over payload only | encoding field, 1 MiB frame cap, CRC-32C over header and payload |
| no priorities or fragments: a large message blocks small ones | four channels, fragmentation, strict priority with an anti-starvation rule |
| constants kept in sync by hand across Dart and Python | one registry file; Python reads it, Dart code is generated from it |

## 2. Constraints that shape the design

1. **comma 3 has no BLE hardware.** On NGP10 the cable is the only link; PLP over USB must be complete on its own. BLE exists only on ExoPilot boards (EOP10, 01M, 02M).
2. **Two kinds of phone cable.** iPhone: the comma shows a CDC-NCM network (192.168.60.1) and the phone dials in. Android: the comma shows a FunctionFS vendor interface with bulk endpoints and the phone, as USB host, talks to it. Same session on both.
3. **One protocol, two speeds.** USB moves megabytes per second; BLE tens of kilobytes at best. BLE must carry everything needed to keep driving advice sane; USB adds convenience, never necessity.
4. **Nothing is safety-critical on this link.** PLP carries advice and telemetry; the driving model and the vehicle controls do not take commands from it (see D10, and the navigation plan: map data is advisory).
5. **v4.1 must keep working** for phones and boards that have not moved: on EOP10 and up both protocols run; on NGP10 there is no legacy to carry.

## 3. Layers

```
services      core device vehicle nav drive perception auth voice locate system     (registry.json)
session       hello, negotiation, request/response/error, handlers, ping            (PlpSession)
channels      4 priority channels, fragmentation, reassembly                        (Outbox, Reassembler)
framing       16-byte header, payload, CRC-32C, resync                              (Frame, FrameDecoder)
transport     USB TCP (iPhone) | USB bulk (Android) | BLE GATT | SPP (legacy)      (byte stream in, byte stream out)
```

A transport is only a byte pipe with a name (`usb`, `ble`, `spp`). It never parses a frame.

## 4. Transports

### 4.1 USB-C cable

- **iPhone.** The comma is the USB device and presents CDC-NCM; the phone gets 192.168.60.x without a gateway and connects to **TCP port 5601** (proposed; the big-model link uses 5600). The server binds the cable address only, refuses peers outside 192.168.60.x, and serves one phone at a time (a new connection replaces the old). Implemented: `nagaspilot/plp/cable.py`.
  - **Proven by Jetlink, not by us.** Jetlink's iOS gadget is a CDC-NCM network where the comma is 192.168.60.1 and runs DHCP (192.168.60.2 to .254, 10-minute leases, no router or DNS options), the iPhone keeps its internet route on Wi-Fi and dials the comma by address ([Jetlink link protocol](https://github.com/exo-elec/jetlink/blob/main/docs/link-protocol.md), "What the comma presents"). Our `ngp-bigmodel-root.sh` follows the same design (NCM, 192.168.60.1/24, dnsmasq with the same range and lease). That answers the question Apple's forums leave open ([thread](https://origin-devforums.apple.com/forums/thread/836307)) for a gadget built this way. **[verify]** still on our own comma and iPhone: the comma's kernel sends NCM blocks slowly when a Linux host lets it pack many packets (Jetlink measured 22 Mbit/s at the default block size against 190 Mbit/s at 2 KB, fixed on a Linux host with `rx_max`); Apple's own driver showed no slow path. Plain Ethernet adapters work on iPhone 15 and 16 with iOS 17 or later ([Plugable](https://kb.plugable.com/question/3306827)); Apple's private NCM interface is described in the Linux patch and in [Synacktiv](https://synacktiv.com/en/publications/ios-a-journey-in-the-usb-networking-stack).
- **Android.** The phone is the USB host and the comma exposes vendor interfaces with a bulk IN and OUT pair each. Jetlink does it this way: one vendor-specific interface, one bulk pair, opened through the Android USB host API; iOS gives apps no vendor USB access, which is why the iPhone uses the network ([Jetlink](https://github.com/exo-elec/jetlink/blob/main/docs/link-protocol.md)). **Decision (from that precedent): PLP gets its own vendor interface** on the comma's composite gadget beside the big-model one. Jetlink's iOS gadget is already composite (vendor interface plus NCM) and Jetlink never runs two protocols on one pipe, so there is no precedent for sharing; a second interface leaves the NPBM hot path (400 KB frames at 20 Hz) untouched. Padding follows Jetlink's rule exactly (`plp/bulk.py`): the comma pads each frame to 16 KB, the phone adds one byte when a frame is an exact multiple of 512. The Android Open Accessory protocol ([AOA 2.0](https://source.android.com/docs/core/interaction/accessories/aoa2)) is **not** used: AOA makes the Android phone the *device*, the opposite of our arrangement. Not built: the second FunctionFS function in `ngp-bigmodel-root.sh` and `ffs.py`, and the app claiming two interfaces.
- **Keepalive.** TCP detects a dead peer slowly; the session pings. Proposed: ping every 3 s, link down after 3 missed answers **[verify on bench]**.
- **Framing size.** Frame payload up to 64 KiB on USB (hello negotiates it down). Link power: Jetlink turns USB 3 link power management off on its port while a session runs (waking the link cost 2.2 ms a frame on its bench); `plpd` does not, since PLP traffic is light, but the big-model link may.

### 4.2 BLE (ExoPilot boards only)

- **Baseline: GATT.** A dedicated **PLP service** with two characteristics: *PLP-TX* (device to phone, notify) and *PLP-RX* (phone to device, write without response), carrying the same byte stream as USB. A separate service UUID means an old NCP-only app never sees PLP and a new app can prefer it: no sniffing of the first bytes. Proposed service UUID (random, to be reserved): `0ac2bfc2-457f-4979-b921-4ff0946bd329`. The existing Nordic-UART-style NCP service stays untouched for legacy phones.
- **Chunking.** A frame may span several ATT notifications; the receiver feeds the byte stream to the decoder, which already handles split frames and resyncs after loss. The transport splits at **negotiated ATT MTU − 3**, read from the stack (the current NCP code sends fixed 500-byte chunks regardless; to be fixed). Frame payload on BLE defaults to 512 B so the 20-byte header and trailer cost under 4%; tune on the bench.
- **Flow control.** A notification can be dropped when the peripheral's queue is full. The BLE transport must pace sends (credit or queue-depth based) and the per-message sender must hold a lock for a whole frame, not a chunk. Because PLP frames carry CRC-32C, a lost chunk costs one frame, not the stream. Reliable resend is added only for messages that ask for it (the map channel, `FLAG_ACK_REQ`, reserved).
- **Connection parameters.** The phone, as central, decides; the device can only request. Apple's QA1931 summary: minimum interval a multiple of 15 ms and at least 15 ms, peripheral latency at most 30, interval × (latency + 1) at most 2 s, supervision timeout more than three times that product and between 2 and 6 s ([Apple QA1931](https://developer.apple.com/library/archive/qa/qa1931/_index.html), summarised by [Silicon Labs](https://docs.silabs.com/bluetooth/3.3/bluetooth-miscellaneous-mobile/selecting-suitable-connection-parameters-for-apple-devices)). A Nordic forum post cites 6 to 18 s for the timeout, so **[verify]** against the current Accessory Design Guidelines. Design to these limits and expect to get no better than 15 ms.
- **Speed.** Theoretical GATT throughput passes 1 Mbit/s with data-length extension and the 2M PHY; Nordic reports about 244 user bytes per GATT packet against 247 for L2CAP CoC, so CoC is not worth the extra risk for speed alone ([Nordic](https://devzone.nordicsemi.com/f/nordic-q-a/92201/l2cap-connection-oriented-channel-coc---how-is-it-different-then-hvx/387447), [RF Essentials](https://rfessentials.com/resources/rf-glossary/ble-throughput/)). No measured iPhone or Android numbers were found: **bench first.**
- **L2CAP CoC: optional.** iOS 11 and later expose it (`CBL2CAPChannel`) and Android 10 (API 29) added `createL2capChannel`; the PSM is not discoverable and must be published in a GATT characteristic ([Apple docs](https://developer.apple.com/documentation/corebluetooth/cbl2capchannel.md), [Android API 29 diff](https://developer.android.com/sdk/api_diff/29/changes/android.bluetooth.BluetoothDevice)). Reports of encrypted-channel failures between iOS and Android and of fragmentation above 2 KB ([Apple forum](https://developer.apple.com/forums/thread/89644)) make it a negotiated extra (`hello.link_options`, v5.1), never the baseline.
- **Minimum.** A BLE link whose usable payload is below 64 bytes stays on NCP v4.1 or refuses PLP.
- **Background.** iOS background BLE limits make USB the primary iPhone path; BLE is the fallback that must still deliver the lean map horizon.

### 4.3 SPP (Classic Bluetooth, OBD scanners)

Stays NCP v4.1 only. No PLP on SPP in v5.0.

## 5. Framing

Big-endian. Header 16 bytes, header CRC-32C u32, payload, and a payload CRC-32C u32 unless the NO_PAYLOAD_CRC flag is set.

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 2 | magic | `0x504C` ("PL"); a sanity check and resync marker, not a discriminator |
| 2 | 1 | version | 5 |
| 3 | 1 | kind | 0 notify, 1 request, 2 response, 3 error |
| 4 | 1 | flags | bit0 MORE (more fragments follow), bit1 CRITICAL, bit2 ACK_REQ (reserved), bit3 NO_PAYLOAD_CRC; other bits must be 0 |
| 5 | 1 | channel | 0 control, 1 realtime (vehicle, safety), 2 interactive (nav, voice, auth), 3 bulk |
| 6 | 1 | service | registry |
| 7 | 1 | message | registry |
| 8 | 1 | encoding | 0 raw, 1 JSON, 2 protobuf, 3 CBOR |
| 9 | 1 | reserved | must be 0 |
| 10 | 2 | request id | 0 for notify; non-zero pairs request, response, error |
| 12 | 4 | payload length | up to 1 MiB per frame, further limited by the hello |
| 16 | 4 | header CRC-32C | over the 16 header bytes |
| 20 | n | payload | |
| 20+n | 4 | payload CRC-32C | over the payload; absent with NO_PAYLOAD_CRC |

- **Header CRC always, payload CRC only where needed.** Copied from Jetlink's link header and our NPBM, which CRC the header and leave the payload to the transport: a corrupt length is caught before the decoder waits for payload bytes, and a 400 KB frame is not checksummed in pure Python. USB bulk and TCP protect the payload themselves; BLE does too at the link layer, but a dropped notification inside the stack can shift a frame boundary under an intact header, so BLE keeps the payload CRC. Each side says what it needs in its hello (`payload_crc`: true means "frames sent *to me* carry a payload CRC") and the other side sends accordingly, setting NO_PAYLOAD_CRC when it was told not to. USB hellos say false, BLE true.
- **CRC-32C (Castagnoli)** instead of CRC-32 IEEE: it is the iSCSI/SCTP/RDMA polynomial, has hardware instructions on x86 and ARM, and is described as having better error detection ([Go source notes](https://go.googlesource.com/go/+/go1.16/src/hash/crc32/crc32.go), [Intel forum](https://community.intel.com/t5/Intel-ISA-Extensions/Question-about-CRC32-instruction-polynomial/m-p/882142)). Hamming-distance tables for our frame sizes were not found; **[verify]** against Koopman's tables if a different polynomial is wanted. Check value for `"123456789"` is `0xE3069283` (in the tests).
- **Fragments.** All fragments of one message repeat the same header (kind, channel, service, message, encoding, request id); MORE is set on all but the last. Frames of different channels may interleave; one channel's message may not. A fragment that does not belong, or a message over the negotiated limit, is dropped and counted.
- **Priority.** Strict by channel number at frame boundaries. A channel that has waited 16 frames is served once, so bulk still moves.
- **Resync.** The decoder never throws. Bad magic, version, flags, kind, length or header CRC skips one byte and searches for the next magic; a bad payload CRC does the same. Zero padding between frames (the USB bulk rule) is skipped the same way. Counters: `bad_crc`, `bad_header`, `skipped_bytes`.

## 6. Session

1. **Hello** (service `core`, message `hello`): the phone sends a request, the device answers with a response. Content (JSON): version range, encodings, `max_payload`, `max_message`, `payload_crc`, services with their message-set version ranges, `link`. The agreed values are the intersection; frames sent to a peer stay within the *peer's* limits. No common version: error `unsupported`, and the link stays NCP v4.1 where that exists.
2. Before the hello: requests get `no_hello`, everything else is dropped.
3. **Ping** (`core.ping`): request with any payload, response echoes it with the same request id.
4. **Direction.** A message the registry marks device-to-phone that arrives at the device (or the reverse) is answered with `wrong_direction` if it was a request and dropped otherwise.
5. **Unknown messages.** An elective notify is ignored. A CRITICAL message or any request gets an `unsupported` error. This follows the CoAP idea of critical and elective, in which an unknown critical option is rejected and an unknown elective one ignored ([CoAP notes](https://doc.riot-os.org/rustdoc/latest/src/coap_numbers/option.rs.html), RFC 7252 itself not read), but it uses an explicit flag and an error kind instead of encoding criticality in the option number, which an IETF draft says limited CoAP's extension point ([draft-ietf-core-uri-path-abbrev](https://datatracker.ietf.org/doc/html/draft-ietf-core-uri-path-abbrev-05)).
6. **Handler errors.** A bad payload (`ValueError`, which `BadFix` and `BadHorizon` are) answers a request with `bad_payload` and never ends the session.
7. **Error payload** (JSON): `{"code": ..., "detail": ...}`. Codes: `unsupported`, `bad_payload`, `no_hello`, `wrong_direction`.
8. **One phone at a time.** A new connection replaces the old.

## 7. Services and compatibility rules

`registry.json` lists 42 messages in ten services (core, device, vehicle, nav, drive, perception, auth, voice, locate, system) with each message's direction, status (`active`, `retired`, `planned`) and the NCP v4 type numbers it replaces. `registry.v4_to_v5` and `v5_to_v4` map both ways; ack, error and batch have no PLP message (a response, an error kind, and nothing).

Evolution rules, adapted from Protocol Buffers practice ([summary](https://oneuptime.com/blog/post/2026-01-24-protocol-buffer-evolution/view), [more](https://gravitee.io/corpus/gen-2415/protocol-buffers/protobuf-compatibility-management.html)); they apply to the registry and to each message's fields:

- A `(service, message)` number is never reused. A removed message stays in the registry as `retired`.
- A field is added, never repurposed. A reader ignores fields it does not know and treats an unknown enum value as "other".
- Changing a field's type, meaning or cardinality is a new message.
- A message-set version bump per service is announced in the hello; peers use the lower one.
- Compatibility is tested against *all* previous versions, not only the last (transitive).
- JSON messages keep their cross-repo fixtures (`map_horizon_navpilot.json`, `plp_golden.json`) in both repos; the Dart and Python tests each read the other side's output.

Encoding: control messages stay JSON for now. Binary payloads (protobuf recommended, owner to confirm) come only when a message needs them; there is no tile traffic (HD map plan, revised).

## 8. Security

- **USB.** The physical cable is the trust anchor; the server binds the cable address only and refuses other subnets.
- **BLE.** The link must be encrypted and bonded (LE Secure Connections where the stack allows) before PLP is served; the PLP service characteristics require encryption. NCP's pairing messages (`auth.pair`) carry over unchanged.
- **Not in v5.0: payload encryption.** Reserved path: a Noise XX handshake (three messages, mutual static-key authentication, resistant to replay) with an AEAD cipher, started after the hello and bound to the pairing record ([Noise-based pairing examples](https://github.com/waku-org/specs/blob/master/standards/application/device-pairing.md)). It would protect a stolen-phone or hostile-network case that the cable assumption does not. **[verify]** an audited Noise implementation exists for both Dart and Python before committing.
- **Input limits.** Every decoder bound is explicit (frame 1 MiB, message 4 MiB default, horizon 600 points, fix accuracy and speed ranges). Handlers never trust a payload.
- **Advisory data.** Phone map and GNSS data never drive the vehicle controls; the comma cross-checks them against its own sensors and OSM.

## 9. Migration and branches

| Branch | Hardware | Links | Protocols |
|---|---|---|---|
| NGP10 | comma 3 | USB only | PLP only; `nagaspilot/plp/` lives here and flows down |
| EOP10 | ExoPilot 01 line | USB, BLE, SPP | PLP (USB and BLE) and NCP v4.1 (BLE, SPP) |
| 01M, 02M | ExoPilot | same | same |

On BLE the phone looks for the PLP service; if present it speaks PLP, if not it speaks NCP v4.1. `bluetoothd` gains a PLP transport that feeds a `PlpSession`; its NCP code is not touched until v4.1 is retired. Names: classes and files that implement v4.1 keep the NCP name (`ncp_session.py`, `CableNCP`); nothing is renamed until a phone and a board have run PLP together. When v4.1 is retired, `ncp_*` goes and `bluetoothd`'s session becomes PLP's.

Handlers move over one service at a time through the registry's v4 mapping. Nothing in the driving path changes.

## 10. Verification plan

| # | Check | Where |
|---|---|---|
| 1 | Golden frames and registry round trip, Python and Dart | repos (Python done) |
| 2 | Fuzz the decoder with random and mutated frames | repos |
| 3 | iPhone against the comma's CDC-NCM gadget: connect, hello, 1 h soak | hardware |
| 4 | Android bulk transport on a real phone | hardware |
| 5 | BLE: granted connection interval, throughput, notification drops on iPhone and Android, MTU | hardware |
| 6 | Unplug and replug USB mid-message; BLE out of range and back | hardware |
| 7 | v4.1 phone against a PLP-capable EOP10 board and the reverse | hardware |

## 11. Open points and decisions

1. Cable port 5601 and the BLE PLP service UUID (proposed above). Jetlink uses 5599 for its link and 5600 for its status page; ours: 5600 NPBM, 5601 PLP.
2. Protobuf (or another) for future binary messages.
3. Noise layer: wanted for v5.x or not.
4. Keepalive timing and BLE frame size after the bench.
5. Whether `auth.pair` is redone on PLP or carried as is.
6. Where the cereal output for the locate service goes (blocks wiring `LocateSink` into localization).
7. Reserving the `FLAG_ACK_REQ` behaviour now, or removing it until a message needs it.

## 12. Migration status (2026-10-13)

| Piece | State |
|---|---|
| Registry (42 messages, v4 mapping), framing, channels, hello, dispatch, session | **Done**, comma (`nagaspilot/plp/`, 61 tests with the neighbouring suites) and phone (`packages/plp`, 18 tests); golden frames, hello bytes and a full hello/error exchange are shared fixtures |
| USB cable, iPhone (TCP 5601) | **Code done**: `cable.py`, `plpd.py`, started by the manager when `ngp_bigmodel_link = 2` (NGP10 `system/manager`, EOP line `nagaspilot/manager`). Loopback-tested only. **Not run on a board or an iPhone**; the gadget design itself is Jetlink's, which is proven on an iPhone |
| Locate service (GNSS fix, map horizon) | Done on both sides; the comma holds the results in `LocateSink`, the phone sends through `PlpLocate`, falling back to NCP when PLP is not agreed. **Nothing reads `LocateSink` yet** (cereal output undecided) |
| BLE, comma side | **Code done, untested on BlueZ**: `ble_stream.py` (chunking, pacing; tested, including a 70-byte MTU), `bluetoothd/plp_gatt.py` (second GATT application, own service UUID, hooked into `bluetoothd`). The D-Bus shell has been exercised only with the D-Bus parts stubbed |
| BLE, phone side | **Not done.** Needs the native BLE layer to hand bytes to a `PlpSession` and read the ATT MTU |
| Android cable | **Decided, not built.** A second vendor interface for PLP (Jetlink's composite-gadget precedent); `plp/bulk.py` has the padding rule and is tested. The second FunctionFS function and the app side are not done |
| NCP v4.1 | Unchanged except one fix: the BLE notify holds one lock per frame (it took it per chunk, so frames from two threads could interleave). The fixed 500-byte chunk size is **not** fixed: the legacy path is left alone |
| v4 handlers moved to PLP | **Not started.** The registry maps every v4 type; moving `ncp_session` handlers service by service needs a board and a phone, and `ncp_session` cannot be imported in the dev container (capnp) |
| Renames | None yet, by design: `ncp_*` files and `CableNCP` keep their names until PLP has run end to end |

What "clean" means here: PLP exists beside NCP, nothing in v4.1 behaviour changed, nothing was renamed, and every unverified piece is listed above. The order to finish: (1) iPhone against `plpd` on a comma 3; (2) BLE bench and the Android second interface; (3) move `auth.pair` and the vehicle and nav handlers; (4) retire NCP and rename.

## References

[Jetlink link protocol and USB reference](https://github.com/exo-elec/jetlink/blob/main/docs/link-protocol.md) (the proven design this follows for the iPhone network, the Android bulk interface, padding, and header-only CRC) · [Apple QA1931](https://developer.apple.com/library/archive/qa/qa1931/_index.html) · [Silicon Labs, connection parameters for Apple devices](https://docs.silabs.com/bluetooth/3.3/bluetooth-miscellaneous-mobile/selecting-suitable-connection-parameters-for-apple-devices) · [Apple CBL2CAPChannel](https://developer.apple.com/documentation/corebluetooth/cbl2capchannel.md) · [Android API 29 BluetoothDevice changes](https://developer.android.com/sdk/api_diff/29/changes/android.bluetooth.BluetoothDevice) · [Apple forum, iOS 11 L2CAP throughput](https://developer.apple.com/forums/thread/89644) · [Android Open Accessory 2.0](https://source.android.com/docs/core/interaction/accessories/aoa2) · [Plugable, Ethernet on iPhone](https://kb.plugable.com/question/3306827) · [Apple forum, iPhone USB-C accessory](https://origin-devforums.apple.com/forums/thread/836307) · [Linux CDC-NCM Apple interface patch](https://lkml.iu.edu/hypermail/linux/kernel/2406.0/07870.html) · [Synacktiv, iOS USB networking](https://synacktiv.com/en/publications/ios-a-journey-in-the-usb-networking-stack) · [Nordic, L2CAP CoC vs GATT](https://devzone.nordicsemi.com/f/nordic-q-a/92201/l2cap-connection-oriented-channel-coc---how-is-it-different-then-hvx/387447) · [RF Essentials, BLE throughput](https://rfessentials.com/resources/rf-glossary/ble-throughput/) · [Go crc32 source](https://go.googlesource.com/go/+/go1.16/src/hash/crc32/crc32.go) · [Intel forum, CRC32C](https://community.intel.com/t5/Intel-ISA-Extensions/Question-about-CRC32-instruction-polynomial/m-p/882142) · [CoAP option numbers (RIOT)](https://doc.riot-os.org/rustdoc/latest/src/coap_numbers/option.rs.html) · [CoAP uri-path-abbrev draft](https://datatracker.ietf.org/doc/html/draft-ietf-core-uri-path-abbrev-05) · [Waku, Noise device pairing](https://github.com/waku-org/specs/blob/master/standards/application/device-pairing.md) · [Protobuf evolution guide](https://oneuptime.com/blog/post/2026-01-24-protocol-buffer-evolution/view)
