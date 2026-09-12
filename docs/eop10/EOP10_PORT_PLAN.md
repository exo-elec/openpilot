# EOP10 on VisionPilot — Port Plan & Linkage Analysis

**Goal**: `dev/02M` — the ExoPilot 02M line. Same proven openpilot backend as
`dev/01M`, no ROS 2 anywhere, and a new UI built from the Nagasware and
VisionPilot design language in place of openpilot's C++/Qt UI — onroad *and*
offroad.

**Platform scope: ExoPilot 02M (RK3576) only, 1600×600 only.**
This line does not target 01M/RK3588, comma-3/TICI, or any other display
geometry. That is a narrowing, and it is load-bearing throughout this plan:

- The UI is laid out for exactly 1600×600. No breakpoints, no responsive
  reflow, no second geometry to keep working. Nagasware's design already
  hardcodes 1600×600 with 50px top and bottom bars over a 500px content band.
- Platform conditionals collapse to constants: 5 MIPI cameras, 160 mm stereo
  baseline, 2 NPU cores × 3 TOPS. Nothing dispatches on SoC.
- RK3588-only code paths are deleted rather than carried (§8), which is where
  a meaningful part of the "lower profile" gain actually comes from.
- But the narrowing also *promotes* three RK3588-only subsystems from
  "inherited caveat" to real work on the critical path: thermal/fan, PMIC
  rail monitoring, and NPU per-task core allocation (§7.4).

**Status**: planning only. Nothing in this document has been built or run.
All facts below were verified against the three working trees on 2026-09-10;
file paths and counts are real, not estimates.

---

## 0. Scope

**ExoPilot 02M (RK3576) only, 1600×600 only.** No 01M/RK3588, no comma-3/TICI,
no second display geometry — `dev/01M` is the 01M line. That narrowing is
load-bearing throughout:

- The UI is laid out for exactly 1600×600. No breakpoints, no responsive
  reflow, no second geometry to keep working. Nagasware's design already
  hardcodes 1600×600 with 50px top and bottom bars over a 500px content band.
- Platform conditionals collapse to constants: 5 MIPI cameras, 160 mm stereo
  baseline, 2 NPU cores × 3 TOPS. Nothing dispatches on SoC.
- RK3588-only code paths are deleted rather than carried (§8), which is where
  a meaningful part of the "lower profile" gain actually comes from.
- But the narrowing also *promotes* three RK3588-only subsystems from
  "inherited caveat" to real work on the critical path: thermal/fan, PMIC
  rail monitoring, and NPU per-task core allocation (§7.4).

The genuinely new engineering here is small and worth naming up front: the UI
replacement, and the 02M five-camera MIPI capture path (§7.2). Everything
else already runs — 02M platform support landed on 2026-08-26 and the daemon
fleet is shared with `dev/01M`.

---

## 1. Repo topology — settled

**`dev/02M` is a branch of the openpilot repo, alongside `dev/01M`.** Not a
separate repo, not a submodule.

| Repo | Branch | Platform | UI |
|---|---|---|---|
| `openpilot` | `dev/01M` | ExoPilot 01M — RK3588, 1024×600 | C++ Qt |
| `openpilot` | **`dev/02M`** | ExoPilot 02M — RK3576, 1600×600 | PyQt5, Nagasware |
| `visionpilot` | `EVP09` | 02M | the ROS 2 stack, unchanged |

Earlier drafts of this section argued between a fork of the visionpilot repo
(A) and a submodule pinning openpilot into it (B′). Both were answers to a
question that only existed because the line had been put in a different repo
in the first place. This branch *is* openpilot with a different UI and a
different platform, so it is a branch of openpilot.

What that buys, concretely:

- Backend fixes arrive by `git merge dev/01M` between sibling branches, which
  git handles well, instead of a submodule pin bump or a hand re-port.
- No `PYTHONPATH` wiring, no venv in a subdirectory, no `.gitmodules`.
  `import openpilot.*` resolves the way it always has.
- **The RK3576 platform layer simply stays here** when `dev/01M` removes it
  (§11). The submodule design needed an overlay registering RK3576 into
  `PlatformRegistry` at startup, and with it a genuine ordering hazard —
  `detect()` returning `'rk3576'` before anything had registered it. That
  entire risk is gone, not mitigated.
- Build, CI and tooling work unchanged, because this is the same tree.

The two branches diverge exactly where they should — UI and platform — and
share controls, planning, perception and the daemon fleet.

The cost is the one thing to stay honest about: two long-lived branches in one
repo drift if nobody merges. `dev/01M` → `dev/02M` merges should be routine
and frequent, not an end-of-project event.

---

## 2. What actually gets replaced

### 2.1 By the numbers (verified)

| Thing | Where | Size |
|---|---|---|
| VisionPilot ROS 2 packages | `src/**/package.xml` | **203 packages** |
| openpilot EOP10 managed daemons | `system/manager/process_config.py` | **~45 processes** |
| openpilot C++/Qt UI (to delete) | `selfdrive/ui/qt/` | 88 files, **13,572 LOC** |
| openpilot Python raylib UI (upstream, unwired) | `selfdrive/ui/*.py`, `layouts/`, `onroad/` | 3,515 LOC |
| Nagasware PyQt5 UI (**primary source**, §5.4) | `../nagasware/pyqt5/{domains,src,shared}` | 150 files, **48,545 LOC**, 272 classes, + 2,191 LOC QSS |
| VisionPilot PyQt5 UI (secondary source) | `../visionpilot/src/dashboard/ui/ui/` (EVP09) | 98 files, **22,563 LOC** |
| EOP-prefixed params to surface in settings | `common/params_keys.h` | **263** of 418 |

203 ROS 2 packages → ~45 cereal/msgq daemons is the "lower profile" claim in
numbers: no DDS discovery, no `colcon`, no `/opt/ros/humble`, no rclpy
executor per node.

### 2.2 Runtime substrate swap

| Concern | VisionPilot (EVP09) | EOP10 |
|---|---|---|
| IPC | ROS 2 DDS topics | cereal + msgq (SHM ring buffers) |
| Message schema | `evp_msgs`, `interface_msgs`, `std_msgs` | `cereal/log.capnp`, `cereal/custom.capnp` |
| Frame transport | `sensor_msgs/Image` + `cv_bridge` (**CPU copy**) | VisionIPC dmabuf (**zero-copy**) |
| Process supervision | `ros2 launch` + lifecycle nodes | `system/manager` + `process_config.py` |
| Config/params | rclpy parameters + YAML | `Params` (`common/params_keys.h`) |
| Node base | `VisionPilotNodeBase(Node)` | `SubMaster`/`PubMaster` loop |
| Build | `colcon build` (ament) | SCons (C++ only) + plain Python |
| Startup | `launch_visionpilot.sh` → `ros2 launch` | `launch_openpilot.sh` → `manager.py` |

---

## 3. Backend linkage analysis — ROS 2 layer → openpilot daemon

The mapping is close to 1:1 at the *layer* level and many-to-one at the *node*
level, because openpilot deliberately fuses what Autoware-style architecture
splits. This table is the porting contract: anything in the right column
already exists and is proven; anything marked **GAP** is real work.

| VisionPilot layer (pkg count) | openpilot `dev/EOP10` equivalent | Verdict |
|---|---|---|
| `sensing/` (8): `car_state_bridge`, `image_preprocessor`, `localization_bridge`, `stereo_matcher`, `sensing_quality`, `vehicle_dynamics_bridge` | `socketd` + `v4l2d` + `stereod` + `locationd` | covered |
| `sensing/radar4d`, `sensing/radar_corner` (BGT60TR13C, ESP32-S3 corner) | `radar2d` service consumer + `selfdrive/gridd`; driver lives in `../exopilot/hal` | **partial GAP** — openpilot consumes `radar2d`/`radar3d` but per its CLAUDE.md, "corner-radar / 4D point-cloud integration belongs to visionpilot". See §7.1 |
| `perception/` (34): detectors, fusion, trackers, seg, lanes | `modeld` + `monod` + `gridd` + `sided`/`reard` + `surfaced` + `stereod` | covered — openpilot fuses 34 packages into ~7 daemons |
| `localization/` (12): EKF, gyro odometer, YabLoc, SGM, OSM localizer | `locationd` + `paramsd` + `coordinationd` + `pigeond` | covered |
| `planning/` (22): behavior/velocity/trajectory/MPC | `plannerd` + `selfdrive/controls/lib/longitudinal_planner.py` (+ VTSC/MTSC/BRSC/SQSC/RCD/TLSC) | covered |
| `control/` (19): trajectory follower, gates, selectors | `controlsd` + `selfdrived` | covered |
| `safety/` (10): AEB, FCW, LDW, MRM, BSD | `selfdrived` events + `AEB_LONGITUDINAL_ENVELOPE.md` + LCA in `eop_panel` | covered |
| `safety/driver_monitor`, `face_pose_monitor` | — | **DROP** — no `dmonitoringd` in `process_config.py`, no driver camera on EOP hardware; `services.py` marks `driverStateV2` dormant |
| `vehicle/` (12) + `actuator/` (7) | `socketd` + opendbc + `card.py` | covered |
| `inference/` (2): `model_host`, `preprocessor` | `system/inferenced` (single device owner, IPC job queue) | covered — **and better**: solves the Hailo multi-process VDevice race documented in openpilot CLAUDE.md |
| `calibration/` (7) | `camera_calibrationd` + `locationd` + `torqued` + `lagd` | covered |
| `system/` (28) | `thermald`, `hardwared`, `stated`, `wdgd`, `imud`, `micd`, `spkd`, `rtcd`, `socketd`, `uvcd`, `bluetoothd`, `subscribed`, `updated` | covered |
| `system/camera` (OX03C10 ×3, GC4653 stereo) | `system/v4l2d` — hardcodes **4** MIPI cameras (01M) | **GAP** — replace outright with 02M's 5. See §7.2 |
| `telemetry/` (4): WebRTC, BLE, OBD, recorder | `steamd` (VR teleop) + `bluetoothd` + `obd2d` + `recordd` | covered |
| `navigation/` (7): Valhalla, POI, search, TTS | `mapd` + `navd` + `soundd` (Piper TTS) | covered |
| `voice/` (8): wake word, VAD, AEC, beamformer, barge-in | `micd` + `voiceCommandRequest` → `bluetoothd`/NCP | **partial GAP** — openpilot has mic + command transport but no on-device wake-word/VAD/beamformer chain. See §7.3 |
| `gemini/` (cloud vision assistant) | `selfdrive/ui/qt/widgets/assistant_card.cc` + `audioFeedback` | **partial GAP** — see §7.3 |
| `audio/` (3) | `soundd` + `spkd` | covered |
| `logger/` (5): mcap, snap, impact, loop | `loggerd` + `mcapd` + `impactEvent` + `deleter` | covered |
| `dashboard/` (3): ui, calib, onboarding | `selfdrive/ui/` — **this is the thing being replaced** | §4–§5 |
| `simulation/` (6): CARLA, MetaDrive, scenario runner | `tools/sim` | mostly covered; scenario_simulator is a **DROP** |
| `map/`, `evp_msgs/`, `common/`, `launch/` | `cereal/`, `common/`, `system/manager/` | N/A — substrate |

**Reading of this table**: of 203 packages, the overwhelming majority map onto
something already running in `dev/EOP10`. Four areas are genuine gaps (§7).
Read plainly: you are not porting a stack, you are porting a UI plus four
features. That is also why this is a branch rather than a repo (§1).

---

## 4. UI stack decision

### 4.1 Options

| | **PyQt5** (recommended) | raylib/pyray | keep C++ Qt |
|---|---|---|---|
| Design reuse from Nagasware/VisionPilot | **direct** — same toolkit, QSS carries over | none — immediate-mode redraw, no stylesheets | none — C++ rewrite |
| LOC to write from scratch | ~3–5k (bridges + camera widget) | ~20k+ (re-implement 84k LOC of design) | ~14k C++ |
| Build-time deps | `python3-pyqt5` (jammy/arm64) | `pyray` wheel | Qt5 dev headers, `lrelease`, SCons Qt env |
| Runtime deps | libQt5{Core,Gui,Widgets} — already on the device | libGL only | libQt5{Core,Gui,Widgets} |
| Licence | **GPLv3 (PyQt5)** — deferred, see §12.1 | zlib/libpng — permissive | LGPLv3 (Qt5 libs) |
| Camera zero-copy | needs new `QOpenGLWidget` + EGL glue (§4.3) | already written (`selfdrive/ui/onroad/cameraview.py`) | already written (C++) |
| Upstream drift | diverges from upstream's raylib direction | tracks upstream | dead end (upstream is removing it) |

**Recommendation: Qt Widgets + QSS on Qt 5.15.** Binding: PyQt5, decided (§12.1).
The design being ported *is* Qt Widgets — ~71k LOC across the two repos,
including 2,191 LOC of QSS theming that has no raylib equivalent. Choosing
raylib means re-drawing the entire Nagasware design by hand in immediate mode,
which is the opposite of "port the UI we already have".

The *binding* is a separate decision from the toolkit. External review (§12)
raised two objections to PyQt5 — it is **GPLv3**, incompatible with
openpilot's MIT licence without a Riverbank commercial licence, and its
maintenance is classified inactive. Both stand. The decision is still PyQt5,
with the licence question explicitly deferred rather than answered, because
nothing here is being distributed. See §12.1.

Note the dependency argument cuts **for** PyQt5, not against: deleting
`selfdrive/ui/qt/` removes the Qt5 *build* dependency (dev headers, `lrelease`,
the Qt SCons environment, the `.qrc`/`.ts` translation pipeline) and leaves only
the Qt5 *runtime* libs that the device already ships. Net: build gets lighter.

### 4.2 What gets deleted from the openpilot side

- `selfdrive/ui/qt/**` (88 files, 13,572 LOC)
- `selfdrive/ui/SConscript`'s Qt targets, `main.cc`, `ui.cc`, `ui.h`
- the `.ts`/`.qm`/`translations_assets.qrc` pipeline (replace with Qt Linguist
  on the Python side, or `gettext`)
- `NativeProcess("ui", "selfdrive/ui", ["./ui"], ...)` → `PythonProcess`

Keep `selfdrive/ui/installer/` (separate binary) and `system/ui/lib/egl.py`
(§4.3). The upstream raylib UI files can stay unwired as reference or be
removed; they cost nothing either way.

### 4.3 The one hard piece: zero-copy camera into PyQt5

This is the highest-risk item in the whole plan and should be prototyped in
Phase 1, before any design work.

Today VisionPilot's `widgets/overlays/camera_background.py` does
`sensor_msgs/Image` → `cv_bridge` → `QImage` → `QPixmap` — a full CPU copy per
frame. On EOP10 that path is replaced by VisionIPC dmabuf.

The good news, verified: `system/ui/lib/egl.py` is **toolkit-agnostic**. Its
API is `init_egl()`, `create_egl_image(width, height, stride, fd, uv_offset)`,
`bind_egl_image_to_texture(texture_id, egl_image)`, `destroy_egl_image()` —
it takes a raw GL texture id, not a raylib handle. Only
`selfdrive/ui/onroad/cameraview.py` is raylib-coupled (it wraps the same calls
around `rl.Texture` and `rl.load_shader_from_memory`).

So the port is: `QOpenGLWidget` → `glGenTextures` via PyOpenGL → feed that id
to `bind_egl_image_to_texture` → draw with the same NV12/`samplerExternalOES`
shaders already in `cameraview.py`. Roughly 200–300 LOC, no new C++.

Two gates:
- `system/hardware/__init__.py` sets `ROCKCHIP = isinstance(HARDWARE, RK3588Hardware)`,
  which covers RK3576 by subclassing — but `cameraview.py` branches on `TICI`,
  not `ROCKCHIP`, for the external-OES shader. On an 02M-only line neither flag
  should survive as a runtime branch: confirm the external-OES path works on
  RK3576, then make it unconditional and delete the fallback shader.
- Dev-PC fallback: VisionIPC NV12 → numpy → `QImage`, same shape as today's
  `cv_bridge` path. Required anyway — the dev PC has no dmabuf.

**A second hardware path exists**, and it came out of the Nagasware audit:
`domains/graphics/{rga_wrapper.py,acceleration_manager.py,hardware_detect.py,rga_map_renderer.py}`
is a self-contained ctypes binding to Rockchip's `librga.so` 2D blitter, and its
`RGABuffer` struct carries a **dma-buf `fd` field** — so it can consume VisionIPC
buffers directly for scale and format conversion without a CPU copy. openpilot
already publishes an `rgaStatus` service, so the accelerator is a known quantity
on this platform. Treat RGA as the fallback if the EGL/`QOpenGLWidget` path in P1
disappoints, and as the likely path for map and overlay compositing regardless.

One gate: `hardware_detect.is_rk3588()` gates the whole wrapper on RK3588. RGA
exists on RK3576 too, so on an 02M-only line this becomes an RK3576 check — not a
port, a one-line predicate change plus verification on real hardware.

**And one thing P1 must settle before anything else: which Qt platform plugin
the device runs.** Nothing in the openpilot tree pins `QT_QPA_PLATFORM` — the
only occurrence anywhere is `tools/clip/run.py` forcing `xcb` for a desktop dev
tool. So whether the target has X11, Wayland, or no display server at all is
currently *unknown from the repo*, and it decides whether this architecture
works: under the **EGLFS** plugin (the one Qt recommends for embedded devices
with a GPU and no windowing system), Qt documents a single-fullscreen-GL-window
constraint, and mixing additional native GL windows with QWidget content
terminates the application. A `QOpenGLWidget` composited as a *child* inside
the one window is the supported case — but "supported in principle" and
"verified on this board with Rockchip's libmali" are different claims, and Qt
has a known open issue with EGL/GLESv2 detection against Rockchip libmali
(QTBUG-116676). See §12.2.

P1's gate therefore grows one item: **name the platform plugin, and prove a
`QOpenGLWidget` composites with surrounding QWidget chrome on real 02M
hardware.** If it doesn't, the fallbacks in order are: render the camera into a
plain `QWidget` via RGA-converted frames (no GL window at all), or reconsider
raylib (§4.1).

---

## 5. UI porting linkage — file by file

### 5.1 Coupling survey (verified)

Of 95 Python files in `../visionpilot/src/dashboard/ui/ui/` (EVP09), **19 touch ROS**
(`rclpy`, `*_msgs`, `VisionPilotNodeBase`). The other **76 (80%) are pure
PyQt5 and port unchanged.**

The 19:

```
bridges/{assistant,safety,vehicle}_bridge.py     ← rewrite (§5.2)
core/{__init__,main,ui_config,ui_node,ui_node_base}.py  ← rewrite (§5.3)
managers/settings_manager.py                    ← rewrite params layer
resources/resource_manager.py                   ← trivial (ament share path)
theme/style_manager.py                          ← trivial (ament share path)
views/{navi,road,settings}_view.py              ← edit imports only
widgets/overlays/camera_background.py           ← rewrite (§4.3)
widgets/overlays/side_camera_overlay.py         ← rewrite (§4.3)
widgets/pages/{device,vehicle}_page.py          ← rewrite params layer
widgets/panels/trip_widget.py                   ← rewrite data source
```

Note `bridges/nav_bridge.py` is already ROS-free.

### 5.2 Bridge → cereal service mapping

The bridges are the whole integration surface. Each keeps its `pyqtSignal`
API exactly as-is so the 76 pure-Qt files never learn what changed underneath —
only the subscription side is rewritten from `create_subscription` to
`SubMaster`.

| Bridge / widget | Current ROS source | EOP10 cereal service(s) |
|---|---|---|
| `VehicleBridge` | `VelocityReport`, `/dashboard/ui/vehicle_bridge/*` | `carState`, `carControl`, `controlsState`, `selfdriveState` |
| `SafetyBridge` | safety aggregator topics | `onroadEvents`, `selfdriveState`, `driverAssistance`, `radarState` |
| `AssistantBridge` | voice/gemini topics | `voiceCommandRequest`, `audioFeedback`, `ttsRequest`, `ncpVehicleData` |
| `NavBridge` | Valhalla/POI topics | `navInstruction`, `navRoute`, `mapData` |
| `CameraBackground` | `sensor_msgs/Image` | VisionIPC `VISION_STREAM_ROAD` (§4.3) |
| `SideCameraOverlay` | side camera topics | `leftCameraState`/`rightCameraState`/`rearCameraState` + their VisionIPC streams |
| `LaneOverlay` | `LaneLineData`, `RoadEdgeData`, `TrajectoryData` | `modelV2` (`laneLines`, `roadEdges`, `position`) |
| `IndicatorOverlay` | blinker topic | `carState.leftBlinker` / `.rightBlinker` |
| `SpeedWidget` | velocity + cruise topics | `carState.vEgo`, `longitudinalPlan`, `speedLimitState` |
| ~~`BevWidget`~~ | `/perception/objects` | **dropped** — see §5.7; the top-down mini-map was removed on `dev/01M` |
| `TelemetryWidget` | diagnostics | `deviceState`, `managerState`, `obdState` |
| `HardwareDiagnosticsWidget` | diagnostic graph | `deviceState`, `inferencedStatus`, `rgaStatus`, `mppStatus`, `recordersHealth`, `micStatus`, `sounddStatus` |
| `CompassWidget` | localization pose | `livePose`, `liveLocationKalman` |
| `TripWidget` | trip_stats topic | `selfdrive/tripd` — **no cereal service today**; either add one or read tripd's store directly |
| `CalibrationAlertOverlay` | calibration topics | `calibrationState`, `liveCalibration` |
| `FaceStatusOverlay` | `face_pose_monitor` | **DROP** — no driver camera on EOP |
| `MapWidget` / `NavigationWidget` | Valhalla | `mapd`/`navd` via `navRoute`, `mapData` |
| `AIResponseWidget` | gemini topic | see §7.3 |

**One `SubMaster`, one QTimer.** Do not create a SubMaster per bridge. The
Nagasware CLAUDE.md's msgq lesson applies directly here: *"multiple PubMaster
instances for the same service crashed msgq on boot — fixed with one shared
session per process."* Build a single `UIState` singleton (mirroring
`selfdrive/ui/ui_state.py`, which already does exactly this with a shared
`SubMaster` over 12 services), tick it from one `QTimer` at 20 Hz, and have it
emit into the bridges' existing signals.

### 5.3 `core/` rewrite

`core/ui_node_base.py::VisionPilotNodeBase` (rclpy `Node` + `ParamsManager` +
QoS profiles + diagnostics) collapses to a plain `UIState` class:
`Params()` for config, `SubMaster` for data, `cloudlog` for logging. QoS
profiles have no analogue and are dropped — msgq has one delivery model.

`core/main.py` (rclpy init/spin + Qt event loop interleave) becomes a plain
`QApplication` with a `QTimer`. This removes the single ugliest part of the
current UI: the rclpy-executor / Qt-event-loop coexistence.

### 5.4 Design merge: Nagasware vs VisionPilot

Both are PyQt5 with the same shape (views / overlays / panels / QSS themes),
and both already target **1600×600** — Nagasware's `main.py` hardcodes
`1600×600` with 50px top and bottom bars over a 500px content band, and
openpilot's own 02M work assumes the same geometry
(`[UI] Default telemetry panel width to 576px (1600−1024)`).
The two designs are convergent, not competing, and since 1600×600 is now the
*only* target (§0), that geometry is a constant rather than one breakpoint
among several. Layouts may use fixed pixel positions where that is clearer;
there is no second screen to keep working.

This matters more than it sounds. A 1600×600 panel is extremely wide and
short — a 2.67:1 letterbox. Vertical space is the scarce resource: 500px of
content between the bars. Designs meant for a comma-3 (2160×1080, 2:1 but
twice the height) do not transfer, which is the second reason openpilot's
offroad settings idiom is being dropped (§5.5).

**Nagasware is the primary code source. VisionPilot is secondary.**

An earlier draft of this section had it the other way round — structure from
VisionPilot, visuals from Nagasware. The audit below overturned that. Prefer
Nagasware code wherever both have an implementation; take VisionPilot only for
things Nagasware lacks.

| | **Nagasware** `pyqt5/` | **VisionPilot** `../visionpilot/src/dashboard/ui/ui/` (EVP09) |
|---|---|---|
| Size | 150 files, 48,545 LOC, 272 classes | 98 files, 22,563 LOC |
| ROS coupling | **3 of 206 files (1.5%)** | 19 of 95 files (20%) |
| Organisation | domain packages — `graphics/`, `hardware/`, `navigation/`, `vehicle/`, each with its own `ui/` | layer packages — `bridges/`, `managers/`, `factories/`, `widgets/` |
| Gesture system | works (§5.6) | **does not run** (§5.6) |
| Tests | `tests/` present | 10 focused UI tests |

Four findings drove the reversal:

1. **Nagasware is already almost ROS-free** — 3 files of 206
   (`domains/vehicle/ros2_model_bridge.py`, `ros2_video_recorder.py`, one test).
   VisionPilot's UI has ROS in 19 of 95. The YAML-polling layer I previously
   called Nagasware's weakness is actually why: its state ingress is a *narrow,
   isolated seam*, and replacing that seam with the shared `SubMaster` (§5.2) is
   a smaller change than rewriting rclpy subscriptions. That earlier framing was
   wrong — it is the reason Nagasware ports easily, not a defect.
2. **A defect class that never ran.** VisionPilot's UI calls `.x_m()` / `.y_m()`
   on `QPoint`/`QRectF` in **9 places** — `side_widget.py`, `center_widget.py`,
   `road_view.py`, `navi_view.py`, `settings_view.py`, `topbar_overlay.py`,
   `camera_background.py`. Qt has no such methods; every one raises
   `AttributeError` at runtime, and every one sits in a gesture handler or a
   paint path. Nagasware has **zero** occurrences and uses `diff.x()` correctly.
   This is direct evidence that VisionPilot's interaction layer has never
   executed, and Nagasware's has.
3. **`NagaspilotModelRenderer` is already written against `modelV2`.**
   `domains/graphics/ui/nagaspilot_model_renderer.py` models `ModelV2Data`,
   `LaneLineData`, `RoadEdgeData`, `TrajectoryData`, `LeadData`, with
   `lane_line_probs`, `lane_line_stds` and `path_offset_z = 1.22` — a QPainter
   reimplementation of openpilot's own `qt/onroad/model.cc` semantics. Its
   dataclasses are explicitly placeholders standing in for the real structs, so
   wiring them to live `modelV2` is close to a drop-in. This is the single most
   valuable asset in either repo for §5.2's `LaneOverlay`.
4. **RGA hardware acceleration** (§4.3) exists only in Nagasware.

**Component inventory to adopt** — this is the "best design, code and UI" set,
all of it Nagasware:

| Area | Files | LOC | Note |
|---|---|---|---|
| Class spine | `components/base/` — `BaseWidget` → `BaseServiceWidget` / `BaseTimerWidget`, `BasePage`, `BaseOffroadPage`, `BaseOverlay` | — | signal-based lifecycle (`page_loaded`, `component_ready`, `data_updated`, `navigation_requested`). Use as the UI's class spine. **One defect to fix on the way in**: `base/base_overlay.py` and `base/onroad_base_overlay.py` both define a class named `BaseOverlay` with different signals — collapse to one. |
| Construction | `onroad_component_factory.py`, `offroad_component_factory.py` | — | `ComponentFactory` with type registry, instance caching, and explicit `cleanup_component()` / `cleanup_all_components()`. Adopt the factory *and* its teardown discipline — house rules forbid resource leaks, and a UI that swaps panels on gesture creates and destroys widgets constantly. |
| Onroad overlays | `components/onroad/` — `onroad_overlay` (311), `center_indicator_overlay` (297), `message_popup_overlay` (247), `time_display_overlay` (186), `hmi_camera_widget` (179), `nav_stream_overlay` (179), `border_overlay` (159), `hmi_rear_camera_widget` (148), `center_message_overlay` (107), `center_overlay` (80) | ~1,890 | the onroad HUD |
| Chrome | `components/overlays/top_overlay` (549), `bottom_overlay` (158) | ~710 | the 50px bars |
| Navigation UI | `domains/navigation/ui/` — `map_widget` (1387), `route_cards` (972), `map_controls` (820), `search_view` (773), `navigation_drawer` (682), `search_bar` (503), `action_buttons` (499) | **~5,636** | far richer than VisionPilot's `navi_view`. Retarget its Mapbox client onto `mapd`/`navd` + `navRoute`/`navInstruction`/`mapData` |
| Input | `components/common/` — `dvr_video_player` (501), `offroad_fullscreen_keyboard` (321), `on_screen_keyboard` (123) | ~945 | replaces `qt/widgets/keyboard.cc`; the DVR player pairs with `recordd` (§5.5) |
| Graphics | `domains/graphics/` — model renderer, RGA wrapper, acceleration manager | — | §4.3, and finding 3 above |
| Notifications | `components/notifications/tc275_notification_manager.py` | — | pairs with the OpenBLT/TC275 flow |

**Take from VisionPilot** only: the signal-shaped bridge interface (`pyqtSignal`
per data domain — a contract, not behaviour), the widget-registry idea in
`managers/widget_manager.py` (§5.6), and the 10 UI tests, which are toolkit
tests that survive the substrate swap.

**Still true from the earlier draft**: keep exactly one `StyleManager`.
Nagasware's (`src/ui/styles/`, `ThemeType`/`ComponentType` enums, layered
`common/base.qss` → `themes/*` → `components/*`) is the one to keep; delete
VisionPilot's `theme/style_manager.py` + `theme/schemes.py`. Duplicate
implementations of one concept are forbidden by the house rules in
`CLAUDE.md`.

**Nagasware's file-based state I/O still gets deleted, not ported** — its
`main.py` polls `../shared/onroad_collision_state.yaml`,
`onroad_blinker_state.yaml` and friends on a timer with mtime caching. Replaced
wholesale by the shared `SubMaster`.

**Nagasware's `shared/`** (config, logging, security/licensing, wifi_manager,
firmware_repository) maps onto existing openpilot equivalents — `Params`,
`swaglog`, `subscribed`, `system/ui/lib/wifi_manager.py`, `updated`. Port none
of it by default.

**Two Nagasware domains map onto existing openpilot daemons rather than porting
as UI**: `domains/vehicle/` (OBD2 service, J1979 + vendor protocols, PID
polling) belongs behind `obd2d`, and `domains/hardware/` (TC275 flashing,
OpenBLT service, S-record parser) behind the flow `openblt_update_widget.cc`
already drives. Take their `ui/` sub-packages; leave the services.

### 5.5 Offroad menu — Nagasware structure, openpilot content

**The offroad menu is Nagasware's, not openpilot's.** This is a structural
decision, not a re-skin: openpilot's settings and the Nagasware menu are
different navigation models, and only one of them fits a 1600×600 letterbox.

| | openpilot `qt/offroad/settings.cc` | Nagasware `views/offroad_view.py` |
|---|---|---|
| Navigation | left vertical sidebar + `ScrollView` | horizontal tab bar (`QTabWidget#settingsTabs`) across the top |
| Page model | one long scrolling list of `ParamControl` rows | `BaseOffroadPage` subclasses, content grouped into named **sections** |
| Vertical budget | assumes ~1080px of height | designed for a 500px content band |
| Styling | C++ `setStyleSheet` strings inline | layered QSS (`components/offroad.qss`) |

**Adopt**: Nagasware's `OffRoadView` + `BaseOffroadPage` + `OffRoadPageContainer`,
its top tab bar, its section grouping, and its QSS. On a screen 1600 wide and
600 tall, horizontal tabs cost 50px of the scarce axis and spend the abundant
one; a left sidebar does the opposite.

**Page set**, starting from Nagasware's (`components/offroad/`) and reconciled
against what EOP10 actually has:

| Nagasware page | EOP10 disposition |
|---|---|
| `device_page` | keep — `deviceState`, reboot/shutdown, calibration, device ID |
| `network_page` | keep — port `_WifiRow` + `system/ui/lib/wifi_manager.py`; add BLE/cellular (EC25) |
| `software_page` / `software_page_integrated` | keep — retarget `GitOperationWorker` onto `system/updated` and `openblt_update_widget`'s firmware flow |
| `lateral_page` | keep — the natural home for `eop_panel.cc`'s ALCC / RED / LCA / DLP / Auto Lane Change group |
| `cruise_page` | keep — longitudinal: VTSC / MTSC / BRSC / SQSC / RCD / TLSC |
| `map_page` | keep — `mapd`/`navd`, OSM, Valhalla |
| `dvr_page` | keep — retarget `VideoRecorderThread` onto `recordd`; do not re-implement recording in the UI |
| `driver_page` | **drop** — no driver camera on EOP (§5.2) |
| `language.py` | keep as the i18n entry point |
| — | **add**: `radar_page` (§7.1, if adopted), `voice_page` (§7.3), `developer_page` (from `developer_panel.cc`), `safety_page` (from `safety_panel.cc`), `model_page` (from `model_selector.cc`) |

**Content** comes from the openpilot side: **263 EOP-prefixed params** must all
be reachable. `eop_panel.cc`, `settings.cc`, `developer_panel.cc`,
`software_settings.cc`, `safety_panel.cc`, `model_selector.cc`,
`openblt_update_widget.cc` and `onboarding.cc` are the reference for grouping,
ranges, units and copy — but only for *what* each control is, never for how the
page is laid out.

Do **not** hand-transcribe 263 params into PyQt5. Build a declarative
descriptor (toggle / spinbox / slider / button-group as data, mirroring
`ParamControl`, `ParamSpinBox`, `ParamSlider`, `ButtonParamControl`), have each
`BaseOffroadPage` render its sections from that descriptor, and write a test
asserting every `EOP*` key in `common/params_keys.h` is either present in the
descriptor or on an explicit exclusion list. That test is the port's
completeness gate for settings.

Onboarding is also Nagasware/VisionPilot-structured: port
`../visionpilot/src/dashboard/ui/ui/widgets/onboarding/` (welcome / terms / training_guide /
setup_complete) rather than `qt/offroad/onboarding.cc`.

---

### 5.6 Floating, swipeable widgets — the interaction model

Both source UIs put **movable, swappable information panels over the camera**,
paged by gesture rather than by chrome. This is the interaction model for the
EOP10 onroad view, and it is what replaces openpilot's fixed HUD plus the 02M
telemetry panel's two-page `QStackedWidget`.

**Gesture vocabulary** — from Nagasware's `main.py` event filter, which is the
implementation that actually runs:

| Gesture | Threshold | Action |
|---|---|---|
| Horizontal swipe | \|Δx\| > 10 px, no Y constraint | page the panel under the touch to next/previous widget |
| Press-and-hold | 1000 ms (`timing_config.hold_timer_duration`) | swap the left and right panels |
| Vertical swipe | drag threshold | change mode — onroad / offroad / mapnav |
| Triple tap | 3 taps < 400 ms apart | reset panel to default widget |
| Hold + drag | `_camera_adjust_mode` | adjust camera framing in place |

Which panel a swipe targets is decided by touch x-position against the 1600px
width (Nagasware splits at x < 500 for the left panel). Panels are 500×500,
which fits the 500px content band exactly (§5.4).

**What to port from where:**

- **Behaviour and thresholds: Nagasware.** It runs; VisionPilot's does not.
- **Shape: VisionPilot's `SideWidget` signal contract** —
  `widget_changed(panel, direction)`, `swap_panels_requested()`,
  `reset_requested()` — plus `Widgets`' type registry (`WIDGET_REGISTRY`,
  `set_left_widget`/`set_right_widget`, persisted through settings) and
  `CenterWidgets`' priority model. These are *interfaces and plumbing with no
  behaviour of their own*, so "unproven" doesn't apply to them the way it
  applies to the gesture maths.
- Move Nagasware's gesture logic **out of `main.py`'s event filter** and onto
  the panel base class as it lands. Nagasware's version hardcodes the 500px
  boundary and carries a leftover `print()` debug statement; both go. This is a
  relocation of proven logic, not a reimplementation.

**Widget set** to register, from VisionPilot's `widgets/panels/` (pure QPainter,
no ROS — they port unchanged) plus Nagasware's: telemetry, trip, BEV,
navigation, hardware diagnostics, clock, map, speed, compass, vehicle status.
Data for each comes from §5.2's table.

**Safety interlocks are part of the feature, not decoration** — VisionPilot
already encodes two, and both must survive the port:

- `CenterWidgets.cycle_widget()` refuses to cycle while a safety warning is
  displayed.
- `road_view`'s swipe-up to the nav view is allowed only when ADAS is
  disengaged **or** the vehicle is stopped, and it force-returns to the road
  view if the vehicle starts moving while engaged.

Re-derive both against `selfdriveState` / `carState.vEgo` / `onroadEvents`, and
test them explicitly — a gesture that can pull the driving view off-screen at
speed is a safety defect, not a UX preference.

---

### 5.7 Onroad cameras and blind spot — settled behaviour to port

Implemented on openpilot `dev/01M` (2026-09-10) and now settled product
behaviour. The Python UI must reproduce it, not re-decide it.

**Camera overlays**

| Trigger | Shows | Notes |
|---|---|---|
| Single blinker | that side's camera, **full screen** | not a corner tile — when the driver signals, the side camera *is* the thing to look at |
| Both blinkers | **nothing** | hazards are not an intent to move sideways; leave the road view alone |
| Reverse | rear camera, full screen | outranks a blinker rather than fighting it for the same screen |

Alerts render above every camera. A full-screen image must never bury one.

**Source edge.** A full-screen image carries no chrome saying which camera it
is, so a bar blinks on the edge that camera looks out of — left, right, or
bottom for rear. Blink phase must come from a clock shared across all
overlays, not from each widget's own frame delivery, or the three drift apart.

**Blind spot.** Two layers, because either can be occluded:

- Edge bands down the left/right of the road view: amber at caution
  severity, red at warning, warning breathing on a raised cosine rather than
  blinking (a hard on/off this far into peripheral vision reads as a
  distraction).
- When a full-screen camera overlay covers those bands — exactly when the
  driver signals toward a car already alongside — the warning moves to the
  overlay's own border, which takes the blind-spot colour and widens.

Severity is `0/1/2` fused from `controlsState`'s Int8 and `carState`'s bools,
in **one shared function**. On the C++ side that rule had been written out
separately in four consumers, two of which skipped the validity check; three
spellings of a safety-relevant rule can drift apart and contradict each other
on screen. Port it as one function from the start.

**No BEV mini-map.** The 130×180 top-down corner widget was removed: lane
lines, road edges and leads all duplicate what the main camera view draws,
and the blind spot — the one thing it uniquely carried — is far more legible
as an edge band.

#### What Nagasware already has

Better than expected, and one gap:

- **`components/onroad/border_overlay.py::BorderOverlay` is already this
  primitive.** `BorderOverlay(side, color_theme, blink_interval_ms, mode)`
  paints a per-side gradient band via `_create_gradient()`, takes a colour by
  hex or preset (`set_color`), and toggles blink vs solid (`set_mode`). Its
  blink is driven by `_update_shared_blink`, a **classmethod** — a single
  clock shared across every instance, which is a cleaner answer to the drift
  problem than the wall-clock phase used in C++. Both the blind-spot bands
  and the source-edge bar are configurations of this one widget.
- **`components/onroad/hmi_camera_widget.py::CameraStreamOverlay` and
  `hmi_rear_camera_widget.py::RearStreamOverlay` are placeholders.**
  `startStream(path)` loads a *static image* and ticks a 200 ms timer
  described in its own comment as "stream simulation". The widget shell,
  layout and `.ui` loading are real and worth keeping; the frame source is
  not. Replace their innards with the VisionIPC path from §4.3 — this is the
  same work as the road camera, on the side/rear streams
  (`VISION_STREAM_SIDE_LEFT`/`_SIDE_RIGHT`/`_REAR`, published by `sided`,
  `reard` and `uvcd`).

At 1600×600 the full-screen camera is wider than 01M's 1024×600, and the
Nagasware chrome reserves 50 px top and bottom (§5.4) — decide whether a
full-screen camera covers those bars or sits between them. It should cover
them: the bars carry no information that outranks the camera at the moment
you are signalling, and stopping short of them would reintroduce exactly the
letterboxing this replaced.

---

---

## 6. Phased plan

Each phase has an exit gate. Do not start the next phase until the gate passes.

### P0 — Branch & skeleton (0.5 wk) — **done**

- `dev/02M` cut from `dev/01M` (§1). No submodule, no `PYTHONPATH` wiring, no
  platform overlay — this is the openpilot tree, so `import openpilot.*`,
  `scons`, CI and tooling all work unchanged.
- `launch_openpilot.sh` gates on `rk3576` instead of `rk3588`, with an
  `EOP_PLATFORM=rk3576` override for dev PCs. The same two sensor modules
  (`gc4653`, `ov03c10`) apply — 02M uses five cameras rather than four.
- `tools/systemd/openpilot-rk3576.service` added and
  `openpilot-rk3588.service` removed, so `switch.sh`'s
  `tools/systemd/<name>-<platform>.service` lookup resolves correctly on an
  02M device.

Two bugs in the inherited 01M unit were not carried over here, and have since
been fixed on `dev/01M` as well:

- The camera wait was an unbounded `until [ -e /dev/video-camera0 ]` loop, so a
  device whose cameras never enumerated sat in `ExecStartPre` until the start
  timeout with nothing in the journal saying why. Now bounded at 30 s with a
  message, letting `Restart=` retry.
- `LimitAS=3G` / `LimitRSS=2G` dropped. Both are `setrlimit` values applied to
  the service process and inherited by every daemon `manager` forks, and
  neither bounds memory the way it reads: `LimitAS` caps *virtual address
  space*, so the large VisionIPC dmabuf mappings and RKNN VA reservations
  `camerad` and `modeld` make fail with `ENOMEM` while the board still has
  free physical RAM; and `RLIMIT_RSS` has not been enforced by Linux since
  2.4, making `LimitRSS` dead configuration that reads like a working bound.
  A real bound is `MemoryMax=` on the cgroup, which needs a number measured
  on hardware — deliberately not guessed. Both units now carry
  `OOMScoreAdjust=-100` instead.

**Gate**: `manager.py` boots with the stock C++ UI still in place. Not yet
verified — needs `uv sync` and a `scons` build, and no `scons` existed in the
environment this was staged from. First task on a machine with the toolchain.

### P1 — Camera spike (1 wk) — *do this before any design work*
- `QOpenGLWidget` + `system/ui/lib/egl.py` + VisionIPC, standalone window.
- CPU `QImage` fallback for dev PC.
- Establish which Qt platform plugin the target runs (§12.2) — nothing in the
  tree pins `QT_QPA_PLATFORM`.
- **Gate**: road camera at 20 fps on 02M hardware, compositing with surrounding
  QWidget chrome, with GPU-path frame timing *and GPU load* measured, not
  assumed. If this fails, revisit §4.1 — it is the only thing that would
  justify raylib.

### P2 — UI shell + state plumbing (2 wk)
- Nagasware tree drops in unconverted (PyQt5), behind the binding shim
  (§12.1). The licence question is deferred, not skipped.
- `UIState` singleton: one `SubMaster`, one 20 Hz `QTimer`, offroad/onroad
  transitions (mirror `selfdrive/ui/ui_state.py`).
- Port the 76 ROS-free files verbatim; rewrite the 4 bridges (§5.2).
- Merge the theme systems (§5.4), single `StyleManager`.
- **Gate**: onroad view renders live `carState` / `modelV2` / camera on dev PC
  against a replayed route.

### P3 — Onroad parity (2 wk)
- Lane/path overlay from `modelV2` — port `NagaspilotModelRenderer` and wire its
  placeholder dataclasses to live capnp (§5.4).
- Floating/swipeable panel system per §5.6: Nagasware gesture logic on
  VisionPilot's `SideWidget` signal contract, widget registry, safety interlocks.
- BEV from `gridObjects`+`radar2d`/`radar3d`, alerts from
  `onroadEvents`/`selfdriveState`, side/rear views, calibration overlay.
- Drop `FaceStatusOverlay` and the driver-monitor surface.
- **Gate**: side-by-side against the C++ Qt UI on the same replay — no missing
  alert, no missing indicator.

### P4 — Offroad & settings (2 wk)
- Nagasware `OffRoadView` + top tab bar + `BaseOffroadPage` sections (§5.5).
- Declarative param descriptor + generated sections.
- Network/wifi/BLE/cellular, software update (`updated`), DVR (`recordd`),
  onboarding from VisionPilot's `widgets/onboarding/`, developer panel.
- **Gate**: the params-coverage test passes over all 263 `EOP*` keys, and every
  page fits the 500px content band without vertical clipping.

### P5 — Cut over (0.5 wk)
- `NativeProcess("ui")` → `PythonProcess("ui")`; delete `selfdrive/ui/qt/**`
  and the Qt SCons/translation pipeline.
- Collapse SoC and geometry conditionals to 02M/1600×600 constants (§8).
- **Gate**: full boot to onroad on 02M, watchdog stable, no Qt build step.

### P6 — Gap features (§7), sized separately
02M camera capture is the only one on the critical path for real hardware.

Rough total to P5: **~8 weeks** of focused work. Keeping `dev/01M` merged in
regularly is not counted as project time but is not optional either (§1).

---

## 7. Real gaps — things openpilot `dev/EOP10` does not have

### 7.1 Corner radar / 4D point cloud
`../visionpilot/src/sensing/radar4d` (BGT60TR13C + Kalman tracker + LiDAR-style output) and
`../visionpilot/src/sensing/radar_corner` (ESP32-S3, pose calibration, wire format, link store)
have no openpilot equivalent. openpilot's CLAUDE.md explicitly scopes this
*to VisionPilot*: *"OpenPilot no longer owns a `radar4d` runtime. Future ESP32
corner-radar point-cloud work is scoped to VisionPilot."*

This is a genuine architectural question the EOP10 line forces: if VisionPilot's
ROS2 stack is retired on 02M, that scoping sentence has no home. Either port
`radar4d`/`radar_corner` as cereal daemons publishing into the existing
`radar2d` service, or accept that EOP10 ships without them. **Needs a decision.**

### 7.2 02M 5-camera MIPI capture (critical path)
`system/v4l2d/_default_camera_configs()` hardcodes 01M's 4 MIPI cameras.
02M has 5: mono_narrow / mono_wide / mono_tele / stereo_left / stereo_right.

Because this line is 02M-only (§0), this is a **replacement, not a
per-platform dispatch** — write the 5-camera config as the config, and delete
the 4-camera one. Same for the stereo baseline: the hardcoded 80 mm constant
becomes 160 mm, not a call to `get_stereo_baseline_mm()`.

`../visionpilot/src/system/camera/camera/drivers/{ox03c10_driver.py,gc4653_driver.py}`
(on `EVP09`) has working register-level driver code for **the exact same two sensors**
(OX03C10 ×3, GC4653 stereo pair), behind a clean `BaseCameraDriver` interface.
openpilot's own RK3576 doc names it as the porting reference. Adaptation, not
re-derivation: ROS2 topics → V4L2 + VisionIPC.

This is the single item that blocks real-hardware bring-up. It exists nowhere
today — not on either openpilot branch, not in `../exopilot` — except as
VisionPilot's ROS 2 drivers.

### 7.3 Voice pipeline and Gemini assistant
`src/voice/` (8 packages: wake_word, vad, aec, beamformer, barge_in,
noise_suppress, command_routers, cloud_assistant) and `src/gemini/` have no
openpilot equivalent beyond `micd` + the `voiceCommandRequest` transport.
`RK3576Hardware` sets `has_voice_input → True` (unlike 01M), so 02M hardware
expects this. Port as cereal daemons consuming `microphoneData`/`rawAudioData`
and publishing `voiceCommandRequest` — the transport already exists, the DSP
chain does not.

### 7.4 RK3576 platform subsystems promoted by the 02M-only scope
On a dual-platform line these were acceptable no-ops. On an 02M-only line they
are the platform, and they do nothing today:

- **Thermal / fan control**: `system/thermald/thermald.py`, `fan_control.py`,
  `thermal_zones.py` read RK3588 devfreq paths (e.g.
  `/sys/class/devfreq/ffa30000.npu/governor`) and import
  `hal.platform.rk3588_thermal`. `hal.platform.rk3576_thermal` does not exist.
  They fail closed, so today 02M runs with no governor forcing and no fan curve.
- **PMIC rail monitoring**: `system/hardware/hardwared.py` carries RK806S rail
  names and nominal voltages for 01M. Under-voltage detection is silently off
  on 02M.
- **NPU per-task core allocation**: `NPU_ALLOCATION_MAP[PlatformType.RK3576]`
  is an **empty dict** — `get_core_mask()` falls back to core 1 for every task,
  so both driving model and all perception land on one of the two cores. Core
  *count* is right; the allocation is not. VisionPilot's own budget
  (core 0: 2.5 TOPS driving+policy, core 1: 2.4 TOPS perception, both under the
  85% line) is the reference for what the map should contain.

Each needs real RK3576 devfreq/PMIC/tuning data. None is blocked by the UI work;
all three should be scheduled alongside §7.2 for hardware bring-up.

### 7.5 Smaller items
- `TripWidget` has no cereal service (§5.2).
- Localization extras: `yabloc`, `sgm_*`, `osm_localizer` partially exist as
  `coordinationd`/`osmCorrectedPose`; audit before assuming parity.
- Simulation: `tools/sim` covers CARLA/MetaDrive; `scenario_simulator_adapter`
  does not port.

---

## 8. What NOT to port

- All 203 `package.xml` / `setup.py` / `resource/` ament marker files.
- `evp_msgs` / `interface_msgs` — `cereal` is the schema.
- Every `VisionPilotNodeBase` subclass's QoS/diagnostics boilerplate.
- Nagasware's YAML file-IPC layer (§5.4).
- Driver-monitoring UI (§5.2).
- `docker/ros_entrypoint.sh`, `colcon` tooling, `/opt/ros/humble` sourcing.
- **RK3588 / 01M code paths**, TICI/comma-3 paths, and every SoC conditional
  they justify — this line is 02M-only (§0). Delete rather than carry: the
  4-camera MIPI config, the 80 mm baseline constant, dual-platform dispatch in
  `PlatformRegistry`, and any layout branch on display geometry.
- openpilot's offroad settings navigation model (§5.5) — content only, not
  structure.

---

## 9. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| PyQt5 camera path can't hit 20 fps zero-copy | **high** | P1 spike before design work; raylib is the fallback |
| 02M MIPI capture is unimplemented anywhere | **high** | P6/§7.2, port VisionPilot's drivers; blocks real-hardware bring-up |
| 263 params hand-transcribed → silent omissions | medium | declarative descriptor + coverage test (§5.5) |
| Two `StyleManager` implementations survive the merge | medium | pick one in P2, delete the other |
| Corner radar has no owner once VisionPilot's ROS 2 stack retires on 02M | medium | decide §7.1 explicitly |
| Thermal/fan and PMIC monitoring are silent no-ops on RK3576 | **high** | §7.4 — on an 02M-only line this is thermal safety, not a caveat; schedule with P6 |
| All NPU tasks land on core 1 (empty allocation map) | medium | §7.4 — port VisionPilot's TOPS budget into `hal.tuning.npu` |
| Nagasware offroad pages assume its own config layer, not `Params` | medium | rewrite the params layer per §5.5; keep the page/section structure |
| PyQt5's GPLv3 licence vs openpilot's MIT | **high** | unresolved and deferred — not distributing. Qt imports are confined to `qt.py` so a PySide2 conversion stays mechanical (§12.1) |
| EGLFS forbids mixing GL windows with QWidget content; target plugin unknown | **high** | P1 gate — name the plugin and prove `QOpenGLWidget` composites on real 02M (§4.3, §12.2) |
| Binding not packaged for the target | low | resolved — `python3-pyqt5` is in jammy for arm64 |
| VisionPilot UI's `.x_m()`/`.y_m()` defect class (9 sites) | medium | don't port those paths — take Nagasware's equivalents (§5.4, §5.6); grep the tree before reusing any VisionPilot interaction code |
| Gesture can pull the driving view off-screen at speed | medium | port both safety interlocks in §5.6 and test them explicitly |
| RGA wrapper is gated on `is_rk3588()` | low | one-line predicate change to RK3576 + hardware verification (§4.3) |
| Nothing verified on real RK3576 hardware | medium | inherited condition; keep host-side tests as the gate |
| `SubMaster` per bridge → msgq boot crash | low | one shared `UIState` (§5.2) — this bug already happened once in `ncp_session.py` |

---

## 10. Testing

- Keep and port VisionPilot's 10 UI tests (`../visionpilot/src/dashboard/ui/test/`) — they are
  toolkit tests (`test_style_manager`, `test_overlay_factory`,
  `test_resource_manager`, `test_ui_config`, `test_road_controller`,
  `test_safety_bridge`, …) and mostly survive the substrate swap.
- Add: params-coverage test (§5.5), bridge tests driving each bridge from
  synthetic capnp messages, camera-widget test against a VisionIPC fixture.
- Reuse openpilot's `selfdrive/ui/tests/test_ui/` screenshot harness against the
  PyQt5 window.
- `./test.sh` from the openpilot side stays the pre-push gate.

---

## 11. The 01M / 02M split

`dev/01M` goes back to RK3588 only; the 02M platform layer lives here. Because
these are two branches of one repo (§1), this is not a migration — nothing
moves. `dev/01M` deletes what it no longer needs, `dev/02M` keeps it, and the
two share everything else by merge.

### 11.1 Status

**The UI half is done** (2026-09-10, on `dev/01M`). Removed there:

- `selfdrive/ui/qt/onroad/telemetry_panel.{cc,h}` and its SConscript entry
- `getTelemetryPanelWidth()` and the `EOP_TELEMETRY_PANEL_*` constants
  (`selfdrive/ui/qt/qt_window.{h,cc}`) — `deviceScreenSize()` is now a constant
  `{1024, 600}` there
- the `EOPTelemetryPanelWidth` param (`common/params_keys.h`) and its
  `ParamSpinBoxControl` (`selfdrive/ui/qt/offroad/eop_panel.cc`)
- `MainWindow`'s `stack_wrapper` / `QHBoxLayout` split and its `updateState`
  slot (`selfdrive/ui/qt/window.{cc,h}`)
- `nagaspilot/docs/TELEMETRY_PANEL.md`

`dev/02M` inherits all of that removal, which is correct: this branch replaces
the entire Qt UI (§4.2), so the 02M telemetry panel would have been deleted
here anyway. It is not a loss — the panel's swipeable pages become the panel
system in §5.6, at full 1600×600 rather than in 576px of leftover width.

`BEVWidget` was also removed on `dev/01M`, replaced by an edge blind-spot
indicator; `AnnotatedCameraWidget`'s corner overlay became unconditional and
the side/rear camera overlays went full screen (§5.7). Those are the settled
onroad behaviours this branch must reproduce.

### 11.2 What stays here that `dev/01M` drops

When `dev/01M` removes the RK3576 platform layer, this branch simply does not
take that commit. Nothing is ported, registered, or overlaid:

- `system/hardware/rk3576/` — `hardware.py`, `camera_config.py`, tests
- `PlatformType.RK3576` and its NPU allocation map
  (`selfdrive/modeld/runners/rknn_platform.py`)
- the 5-camera v4l2d config (which P6 writes anyway, §7.2)
- `detect_exopilot_platform()`'s RK3576 branch (`eop_utils.py`)
- `PlatformRegistry.detect()`'s `rk3576` device-tree string and register call
- 02M entries in `core_config.py`, `realtime.py`, `transformations/camera.py`,
  `calibration_storage.py`, `surface_quality_db.py`, `stereo_correction.py`,
  `surface_detector.py`, `rockchip_npu.py`
- `Hardware::RK3576()` (`system/hardware/hw.h`) — note `dev/01M` keeps this
  too, since `ROCKCHIP()`, `get_name()` and `get_device_type()` depend on it

~40 files carry `rk3576` / `02M` references, verified by grep across `.py`,
`.cc`, `.h`, `.capnp`, `SConstruct` and `.sh`.

**An earlier draft of this section described an overlay registering RK3576
into `PlatformRegistry` at startup, and rated its ordering hazard —
`detect()` returning `'rk3576'` before anything registered it — as a high
risk.** That was an artefact of the submodule design. It does not exist here.

### 11.3 Merge discipline

The one real cost of two long-lived branches. `dev/01M` → `dev/02M` merges
should be routine, not an end-of-project event; the longer the gap, the more
the UI divergence turns ordinary backend merges into conflicts.

Conflicts will concentrate in exactly one place — `selfdrive/ui/` — because
that is the only tree the two branches genuinely disagree about. Once P5 has
deleted `selfdrive/ui/qt/**` here (§6), even that mostly stops: `dev/01M`
edits files this branch no longer has, which git resolves as a clean delete.

### 11.4 Branch naming

| Branch | Platform | Was |
|---|---|---|
| `dev/01M` | ExoPilot 01M — RK3588 | `dev/EOP10` |
| `dev/02M` | ExoPilot 02M — RK3576 | — (cut from `dev/01M`) |

`dev/EOP10` still exists and should be deleted once the default branch is
repointed and any CI filters or systemd units naming it are updated. The old
`EOP10` and `dev/02M` branches in the *visionpilot* repo are dead and should
go too — that repo is `EVP09` only.

### 11.5 Scope note — "telemetry" (confirmed)

"Telemetry code" meant the **02M telemetry side panel and the dual-platform
window-width machinery** — the code that existed specifically to support both
platforms, which is what disappears when a branch targets one. Confirmed
2026-09-10.

It did **not** touch the telemetry *subsystems* — `steamd`, `obd2d`,
`recordd`, `mcapd`, and VisionPilot's `src/telemetry/`. Those are vehicle-data
features marked *covered* in §3, not dual-platform artefacts, and they stay.

---

## 12. External review — cross-checked findings

Researched 2026-09-10 against current sources, to test this plan's assumptions
rather than restate them. Three findings changed recommendations; one is a
counter-argument worth recording even though it doesn't.

### 12.1 Binding: PyQt5, decided — no PySide

**Settled 2026-09-12: PyQt5 only, and the PySide fallback has been removed.**

This section previously recommended migrating to PySide2 on licence grounds
and the code carried a three-binding shim in the meantime. That is no longer
the plan. The reasoning for the change, and the cost being accepted:

**What the fallback actually cost.** Supporting more than one binding meant
every Qt spelling had to be checked against all of them, and the abstraction
leaked anyway at exactly the places it was supposed to cover: `QDBusMessage`
enums are scoped in PySide6 and unscoped in PyQt5, `QSpinBox` coerces floats
in PySide and rejects them in PyQt5, `QOpenGLWidget` lives in a different
module in Qt6. Each of those needed a bespoke workaround rather than being
absorbed by the shim. The tests ran twice, against a binding nothing ships.

**What is kept.** `selfdrive/ui/eop/qt.py` still exists and every Qt name the
UI uses still comes through it. That is worth having with one binding: it is
the single place a future Qt move gets edited, and it is where `exec_()` and
`horizontalAdvance()` are wrapped.

**The licence position, stated plainly.** PyQt5 is GPLv3 or a paid Riverbank
commercial licence. openpilot is MIT. Distributing a PyQt5 UI would force
GPLv3 on the combined work or require commercial seats. **This is not
resolved — it is deferred**, deliberately, because this is a research project
that is not being distributed. It has to be answered before anything ships,
and the options remain what they were: buy Riverbank seats, or convert to
PySide2 (LGPLv3, `python3-pyside2` 5.15.2 is in jammy universe for arm64).
The conversion is mechanical — `pyqtSignal`→`Signal`, `pyqtSlot`→`Slot`,
`exec_()` unchanged, no Qt6 scoped enums — and confining Qt imports to
`qt.py` keeps it that way.

**Maintenance.** PyQt5 is classified *Inactive* on maintenance signals, and
Qt 5.15 standard support ended 26 May 2025. That is not new exposure: Qt 5.15
is already on the device running the C++ UI, so this is the status quo rather
than something the UI port introduces. When the image moves to Qt6 this UI
moves with it — a BSP decision, made once for the whole device, not forced by
the UI.

### 12.1a Which binding is actually in use, and when that changes

**PyQt5, and only PyQt5.** `python3-pyqt5` is packaged for jammy on arm64,
Nagasware's 48,545 LOC is already written against it, so the port starts with
zero conversion. The multi-binding shim that used to sit here has been
removed; see §12.1 for why it was not paying for itself.

Every Qt name still comes through `selfdrive/ui/eop/qt.py`. With one binding
that is no longer about keeping an escape route provably open — it is simply
the one file a Qt move gets edited in, and where `exec_()` and
`horizontalAdvance()` are wrapped. It keeps a future PySide2 conversion
mechanical without the cost of pretending to support it today.

**The licence question is deferred, not answered.** PyQt5 is GPLv3 or a paid
Riverbank licence and openpilot is MIT, so distributing a PyQt5 UI forces GPL
on the combined work. Nothing here is being distributed, which is why this is
acceptable now and why it must be settled before that changes.

**Also check** whether any Nagasware component was written against a
GPL-incompatible or PyQt-specific API (`sip`, `PyQt5.QtChart`) before assuming
a clean conversion.

### 12.2 The Qt platform plugin is unverified — and it can break the design

Qt recommends **EGLFS** for embedded Linux devices with a GPU and no windowing
system; `eglfs_kms` (DRM/KMS + GBM) is the usual backend on modern boards.
EGLFS supports *one* fullscreen GL window; opening additional OpenGL windows,
or mixing such windows with QWidget content, is documented as unsupported and
terminates the application. A `QOpenGLWidget` composited as a child inside the
single window is the supported case — but there is a documented open Qt issue
with EGL/GLESv2 detection against **Rockchip's libmali** (QTBUG-116676), and
PySide6 users report `eglfs_kms` failing to load.

Nothing in the openpilot tree pins `QT_QPA_PLATFORM`, so the target's plugin is
unknown from the repo. This is now an explicit P1 gate (§4.3).

Related: upstream openpilot's raylib migration **deleted Weston**, so comma's
device now runs with no compositor at all. If EOP's BSP follows, Qt on 02M
means EGLFS, not X11 — which is exactly the configuration with the constraint
above.

### 12.3 Neither binding has official aarch64 PyPI wheels — use the distro

`pip install PySide6` does not resolve on arm64 Linux from official wheels.
With the target pinned to Ubuntu 22.04 this stops being a research question:
`python3-pyside2` 5.15.2 is in jammy universe for arm64, so the answer is the
distro package, pinned. `python3-pyside6` does not exist in jammy — it arrives
in 24.04 — which is the practical half of why §12.1 now lands on PySide2.

### 12.4 The counter-argument, recorded

comma's stated reasons for leaving Qt were that it "adds a bunch of conceptual
and dependency complexity" and that the raylib UI "uses less GPU, so more time
for the driving model." Both are fair, and this plan is deliberately going the
other way.

The honest weighing: the dependency argument is largely neutralised here
because deleting `selfdrive/ui/qt/` removes the Qt *build* dependency (§4.2),
and the design being ported is already Qt Widgets — raylib's saving is
front-loaded onto a rewrite this plan exists to avoid. The GPU argument is
weaker on EOP than on comma hardware specifically because **the driving model
runs on the RKNN NPU, not the GPU**, so UI GPU use does not contend with
inference the way it does on comma's Snapdragon. It is not zero, though: RGA
(§4.3) offloading compositing to the 2D blitter is the mitigation, and P1
should measure GPU load, not assume it.

If P1 finds both the EGLFS constraint biting *and* GPU headroom tight, that is
the signal to revisit §4.1 — and at that point comma's reasoning applies
directly.

### Sources

- [PyQt vs PySide licensing — GPL, LGPL and commercial use](https://www.pythonguis.com/faq/pyqt-vs-pyside/)
- [PyQt5 vs PySide2 licensing](https://www.pythonguis.com/faq/licensing-differences-between-pyqt5-and-pyside2/)
- [PyQt6 vs PySide6 — signals, enums, compatible code](https://www.pythonguis.com/faq/pyqt6-vs-pyside6/)
- [Migrating PyQt5 code to PySide6 (Hex-Rays)](https://docs.hex-rays.com/developer/publishing-plugins/how-tos/migrating-pyqt5-code-to-pyside6)
- [QtPy — abstraction layer over PyQt5/PySide2/PyQt6/PySide6](https://pypi.org/project/QtPy/)
- [Qt 5.15 standard support ends (The Qt Company)](https://www.qt.io/blog/qt-5.15-support-ends)
- [Extended Security Maintenance for Qt 5.15 begins May 2025](https://www.qt.io/blog/extended-security-maintenance-for-qt-5.15-begins-may-2025)
- [PyQt5 maintenance signals (Snyk Advisor)](https://snyk.io/advisor/python/pyqt5)
- [Qt for Embedded Linux — EGLFS](https://doc.qt.io/qt-6/embedded-linux.html)
- [QTBUG-116676 — EGL and GLESv2 not detected with Rockchip libmali](https://bugreports.qt.io/browse/QTBUG-116676)
- [PySide6 on embedded Linux (Qt Forum)](https://forum.qt.io/topic/159208/pyside6-on-embedded-linux)
- [Qt for Python — building from source / cross-compilation](https://doc.qt.io/qtforpython-6/building_from_source/index.html)
- [openpilot 0.10.1 release — raylib UI, Weston deleted](https://blog.comma.ai/0101release/)
- [Rewrite ui in Raylib (from Qt) — commaai/openpilot#33301](https://github.com/commaai/openpilot/issues/33301)

---

## 13. Immediate next steps

1. Close the P0 gate: `uv sync` + `scons`, then boot `manager.py` (§6).
2. Run the P1 camera spike — including naming the Qt platform plugin (§12.2).
   This is the one finding that could still send the toolkit choice back to
   raylib, so it comes before any design work.
3. Keep the binding shim honest — run the component tests under both
   bindings, so the PySide2 switch stays a one-file edit (§12.1a).
4. Decide §7.1 — corner-radar ownership. Still open, still needs an owner.

Settled: 02M/RK3576 only at 1600×600 (§0); `dev/02M` as a branch of openpilot
alongside `dev/01M`, no submodule (§1); Qt Widgets + QSS on Qt 5.15, bound
with **PyQt5 for now and PySide2 before shipping**, behind a shim that keeps
the switch to one file (§4.1, §12.1a); Nagasware as the primary UI source, code and
design, for both onroad and offroad (§5.4–§5.7); telemetry removal scoped to
the 02M panel only (§11.5).

**Last updated**: 2026-09-10
