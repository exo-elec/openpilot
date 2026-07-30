# Design Document: Traffic Light Speed Control (TLSC)

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |
| **Code** | ✅ `selfdrive/controls/lib/tlsc.py` |

---


> **Controller Type:** Longitudinal (speed target)
> **Feature Category:** Core Vision (uses stereo YOLO detection)
> **EOP Integration:** `selfdrive/controls/lib/tlsc.py`, `longitudinal_planner.py`
> **Detection Source:** `stereoObjects` cereal (published by gridd at 20 Hz)
> **Status:** ✅ Implemented

---

## 1. Objective

TLSC decelerates the vehicle to a comfortable stop when a red or yellow traffic
light is detected ahead **and no lead vehicle is present**. When a lead vehicle
is present, ACC handles the stop; TLSC activates only for open-intersection
approach with no car ahead.

---

## 2. Architecture

```
gridd.py (20 Hz)
  YoloObjectDetector (COCO class 9 "traffic light")
      ↓
  traffic_light_classifier.py  — HSV crop → red/yellow/green/unknown
      ↓
  stereoObjects cereal  [dRel, trafficLightState, trafficLightConfidence]
      ↓
longitudinal_planner.py
  tlsc.update(sm)
      ↓
  v_target → v_cruise clamp (same pattern as VTSC/MTSC)
```

### Detection pipeline

| Step | Module | Notes |
|------|--------|-------|
| YOLO detection | `yolo_objdet.py` class 9 | COCO "traffic light"; 320×320 YOLOv8-nano (NPU Core 2 on RK3588) |
| Color classification | `traffic_light_classifier.py` | HSV ranges on bbox crop; ~1ms CPU |
| Distance estimate | `gridd._detect_traffic_lights()` | Stereo depth map Z at bbox bottom-center; fallback to `f*h_real/bbox_h` |
| Publish | `gridd._publish()` | `stereoObjects` at 20 Hz |

### Speed calculation

Standard constant-deceleration formula (same as VTSC):

```
v_target = sqrt(2 × TLSC_DECEL × dRel)
```

`v_target` is the speed at the current position such that, decelerating at
`TLSC_DECEL` (1.5 m/s²), the car reaches 0 exactly at the traffic light.
This is then clamped to `[0, v_ego]` and applied as a floor on `v_cruise`.

---

## 3. Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `EOPTLSCEnabled` | Bool | `0` | Master toggle |

---

## 4. Tuning Constants

| Constant | Value | Notes |
|----------|-------|-------|
| `TLSC_DECEL` | 1.5 m/s² | Comfortable stop; matches VTSC's lateral-limited decel |
| `TLSC_MIN_DIST` | 3.0 m | Below this — already in intersection |
| `TLSC_MAX_DIST` | 80.0 m | Beyond this — light may change before arrival |
| `TLSC_CONFIDENCE` | 0.06 frac | Minimum HSV color pixel fraction for classification |

---

## 5. Activation Conditions

All must be true simultaneously:

1. `EOPTLSCEnabled == 1`
2. No lead vehicle: `radarState.leadOne.status == False`
3. Traffic light detected: `stereoObjects` contains at least one object with
   `obstacleType == trafficLight` AND `trafficLightState ∈ {red, yellow}`
4. Distance in range: `dRel ∈ [3m, 80m]`
5. Confidence: `trafficLightConfidence ≥ 0.06`

---

## 6. HSV Color Ranges

```
Red:    [0°–10°, S>80, V>80]  ∪  [160°–180°, S>80, V>80]
Yellow: [15°–35°, S>80, V>80]
Green:  [45°–85°, S>60, V>60]
```

The classifier picks the dominant color above `_MIN_FRAC = 0.06`. Red wraps
around hue=0 (requires two range checks). The confidence returned is the pixel
fraction of the dominant color in the bounding box crop.

---

## 7. Files

| File | Description |
|------|-------------|
| `selfdrive/gridd/yolo_objdet.py` | COCO class 9 added to `CLASSES_OF_INTEREST` |
| `selfdrive/gridd/traffic_light_classifier.py` | HSV-based color classifier |
| `selfdrive/gridd/gridd.py` | `_detect_traffic_lights()`, `stereoObjects` publishing |
| `selfdrive/controls/lib/tlsc.py` | `TLSC` controller class |
| `selfdrive/controls/lib/longitudinal_planner.py` | TLSC integration (after MSLC) |
| `selfdrive/controls/plannerd.py` | `stereoObjects` added to SubMaster |

---

---

## Simulation Testing

For CARLA-based testing without a real traffic-light classifier, use the `TrafficLightPublisher` in the sim bridge:

```bash
# Enable TLSC and traffic light publisher
params put_bool EOPTLSCEnabled 1

# Run bridge (publisher starts automatically when EOPTLSCEnabled is true)
./run_bridge.py --simulator carla --dual_camera
```

The `TrafficLightPublisher` (`tools/sim/lib/traffic_light_publisher.py`) queries CARLA ground-truth traffic lights near the ego vehicle and publishes their states as `stereoObjects` with `obstacleType = trafficLight`. This lets TLSC react to red/yellow lights in simulation exactly as it would with real stereo YOLO detections.

**Test scenarios:**
- Town10HD has dense traffic light coverage for intersection testing
- Set `EOPSimWeather` to `rain` or `fog` to test reduced-visibility behavior

---

## Implementation

### Function

Traffic light-aware speed planning:
- Slows down for red lights
- Prepares to stop at intersections

### Algorithm

```
Input: modelV2.meta.stopLine, modelV2.meta.trafficState
Output: tlscSpeedLimit, stopDistance
```

### Code Location

- `selfdrive/controls/lib/longitudinal_planner.py`


## 8. Limitations

- YOLOv8-nano at 320×320 has limited range for small objects (traffic lights
  reliably detected from ~5–40m; beyond 40m detections may be sparse)
- HSV classification is sensitive to unusual lighting conditions (bright sun
  backlighting, old incandescent bulbs). `TLSC_CONFIDENCE = 0.06` is conservative
- No temporal filtering: a single missed detection frame clears the target.
  The constant-decel formula is smooth enough that this is acceptable.
- Green light passthrough: TLSC does not accelerate — it only removes the v_target
  constraint when the light is green or unclassified.
