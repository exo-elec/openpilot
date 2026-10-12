# Chestnut parity: the phone in the GPU's place

Compared 2026-10-11 against upstream `commaai/openpilot` master `6f4c0faf1` (2026-10-10). The goal is the same behaviour as
upstream's Chestnut, with the compute on a phone over USB instead of an AMD GPU behind an ASM2464 bridge. Source files read:
`selfdrive/modeld/modeld.py`, `selfdrive/modeld/helpers.py`, `selfdrive/selfdrived/selfdrived.py`, `selfdrive/selfdrived/events.py`,
`selfdrive/selfdrived/alerts_offroad.json`, `system/hardware/hardwared.py`, `system/hardware/chestnut/{status,monitoring,flash}.py`,
`cereal/log.capnp`, `selfdrive/ui/mici/{layouts/home,onroad/hud_renderer}.py`. Since the 2026-08-21 commit this work started from,
upstream renamed `UsbGpu*` to `Chestnut*`, added `chestnut/status.py` and `monitoring.py`, **deleted `compile_modeld.py`**, and added
`cable_connected()` detection and a settling window.

Nothing below has run on a device. "Matches" means the logic is the same and tested against fakes.

## Behaviour

| # | Upstream Chestnut (master `6f4c0faf1`) | Phone path | Status |
|---|---|---|---|
| 1 | Tried only if `chestnut_compiled()` and `chestnut_present() or cable_connected()` at start. `cable_connected()` is `typec_cc_orientation != 0` (`common/hardware/usb.py`), true whatever the data role | Link setting on, a model built (Jetlink's record `Status.runnable`; unknown counts as no), and the cable seen inside the first 60 s: the same CC orientation read where it exists (AGNOS), else the device controller's state (the ExoPilot boards have no Type-C pin) | Matches on AGNOS. **On the ExoPilot boards the cable signal is the controller state, a weaker one** (it needs VBUS and the device role) |
| 2 | `ChestnutLoading` true from start until the load ends; `ChestnutActive` removed at start | `BigModelLoading` true while a connected phone is awaited; stale flags removed at start | Matches (names differ per branch, `bigmodel_platform.py`) |
| 3 | Load in a thread, `BIG_MODEL_TIMEOUT = 60`; `wait_for_chestnut(10)` gives up if the Chestnut has not enumerated 10 s after the cable; no model by then sets `ChestnutActive = False` | 60 s window; a cable not enumerated within 10 s (controller 'configured') releases engagement then and is a failed load; a phone expected and not driving by 60 s sets active false once | Matches |
| 4 | Small model always built as the warm fallback | The small model is always loaded and authoritative | Matches |
| 5 | Big model drives from the first frame after the load | The joining model swaps when nothing is in control; engagement is held off during the window, so the swap lands before the first engagement | Matches in effect |
| 6 | Any exception: `ChestnutActive = False`, small model, **never switched back onroad** | After a hand-back the join is stopped (`close()`); no rejoin until the next start | Matches (the `CHESTNUT` policy, default). Jetlink's own rejoin is the `JETLINK` policy |
| 7 | Non-finite output raises, then falls back | The phone server flags `NOT_FINITE` and keeps its state; the comma holds that frame, and a reply with non-finite floats raises and the joining model demotes | Matches (server side) |
| 8 | `selfdrived`: loading blocks entry (`bigModelLoading`), failure raises `bigModelFailed`, soft-disables if it was driving while engaged, latch clears on disengage | `ngp_bigmodel_gate.py`, same logic, reads both the phone and the eGPU flags | Matches |
| 9 | `modelV2.big = model.chestnut` | `modelV2.big` (`@27`, upstream's ordinal) | Matches |
| 10 | `modeld` sets realtime after loading | `prepare()` runs before the process goes realtime (the link's listener thread must be started before, so it can be moved off the realtime core) | Different order, same intent |
| 11 | Dropped camera frames: the big model has no `prepare_only` and runs every frame | Dropped frames run the big model; the small model stays prepare-only | Matches |
| 12 | `action_t = lateralDelay + smoothing + frame + half-frame` with the big model's smoothing | `action_times()`, same formula | Matches |
| 13 | **Settling window**: `big_model_settling` (`warmup_sec = 5`) suppresses `all_checks` (commIssue), the localizer checks and `frameDropPerc` for 5 s after the load ends, and again after a failure | `NGPBigModelGate.settling()`, the same rule and 5 s, applied to the same three checks in `selfdrived` | Matches. NGP10's own frame-drop threshold is still its v0.10.0 20 % |
| 14 | `after_enqueue` publishes `chestnutGpuState` (temps, power, fan) while the GPU computes; `hardwared` merges it into `chestnutState` | The phone's own telemetry is not carried by the protocol yet and is not mapped to any message | **Not done** |
| 15 | `hardwared` `ChestnutStatus`: offroad alerts NotDetected, UsbSlow, ModelError, Overheated, PcieUnavailable, UpdateFailed, Branch | NotDetected, UsbSlow (< 5000 Mbps), ModelError with the same rules and thresholds, severity 0 (`ngp_bigmodel_status.py`, `bigmodel_link.py`) | Matches where a phone equivalent exists. Overheated, PCIe/12 V power, firmware update, branch check have none |
| 16 | UI: `ChestnutState` icon on the home screen and the onroad HUD (loading pulses, ready green, failed orange, active) | Not wired. `Joining.big_model_state` carries the states (`joining`, `ready`, `running`, `retrying`, `unavailable`) | **Not done** |
| 17 | `deviceState.chestnutPresent` | Not added; the gate and monitor use the UDC state | Different on purpose |
| 18 | Firmware validation and flashing offroad | n/a (a phone has no firmware to flash) | n/a |

## Different on purpose

- **Compute and transport**: a phone's Neural Engine or NPU over a USB cable (NPBM v1, 50 ms frame budget) in place of
  an AMD GPU tunnelled over PCIe. The phone needs a USB 3 port and cable; Chestnut's USB speed alert uses the same 5 Gbps threshold.
- **Model contract**: upstream's pipeline moved again (`compile_modeld.py` is gone). The phone runs the ONNX with its own history on
  the server, so it does not depend on tinygrad pickles; NGP10 and EOP10 keep their v0.10.0 `modeld` and feed it the small
  model's own OpenCL frames.
- **Late join**: A phone that joins at any time and rejoins is available as the `LATE_JOIN` policy (`JETLINK` is its old name)
  (`BigModelPolicy` in `bigmodel.py`) and is **not** the default, because upstream's Chestnut does neither.

## Open

Rows 14 and 16. Also confirm on a device that a swap does not trip `modeldLagging` or `commIssue` even with the settling window.
