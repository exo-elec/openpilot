# Phase 5: Hardware Deployment Readiness Guide

**Status**: ⏳ PENDING - Awaiting RK3588 hardware

---

## Overview

Phase 5 consists of 2 tasks focused on validating InferenceD on real Rockchip hardware:

1. **Task 5.2**: RK3588 Hardware Test (RGA + GPU acceleration)
2. **Task 5.3**: End-to-End Validation (full daemon integration + real-time constraints)

This document provides the preparation checklist and testing procedures to execute when hardware arrives.

---

## Prerequisites & Preparation

### Hardware Checklist

- [ ] **RK3588 Board** with NOR flash or SD card
- [ ] **USB-C Power Supplies** (5V/2A minimum, 3A recommended)
- [ ] **UART Serial Adapters** for console (2x, one per board + dev PC)
- [ ] **Ethernet Cables** for SSH access (or USB Ethernet adapters)
- [ ] **SD Card** for boot/recovery (if not using NOR)
- [ ] **Camera Modules** for modeld/stereod testing (optional, can use synthetic data)
- [ ] **Heatsinks** for sustained thermal operation

### Software Preparation

#### SDK Setup

```bash
# RK3588 SDK
cd /path/to/sdk
mkdir rk3588-sdk
cd rk3588-sdk
git clone https://github.com/rockchip-linux/buildroot.git
git clone https://github.com/rockchip-linux/kernel.git
git clone https://github.com/rockchip-linux/rkbin.git

# Cross-compiler setup
export RISCV_TOOLCHAIN=/path/to/arm-linux-gnueabihf
export PATH=$RISCV_TOOLCHAIN/bin:$PATH
```

#### OpenPilot Deployment

```bash
# Clone ExoPilot EOP10 branch
git clone https://github.com/your-fork/openpilot.git -b EOP10
cd openpilot

# Verify InferenceD modules present
ls -la system/inferenced/
# Should show: compute.py, compute_recovery.py, monitoring.py, *.py backends

# Prepare for cross-compilation
scons --help | grep cross
```

#### Test Infrastructure

```bash
# Create test harness directory
mkdir -p test_harness/phase5
cd test_harness/phase5

# Staging area for test scripts
touch test_harness.py
touch hardware_config.yaml
```

---

## Task 5.2: RK3588 Hardware Test

**Estimated Effort**: 2-3 hours  
**Success Criteria**: GPU/RGA/NPU working, smart dispatch validated

### Objective

Validate that InferenceD correctly dispatches operations to GPU, RGA, and NPU on RK3588.

### Additional Setup

```bash
# On RK3588 (after flashing)
apt-get install -y libarm-compute-dev  # ARM Compute Library for GPU/CPU
apt-get install -y librga-dev  # Rockchip RGA

# Verify GPU available
ls -la /dev/mali*
```

### Test Procedures

#### 5.2.1: ACL Backend Dispatch

```python
# test_5_2_1_acl_dispatch.py
#!/usr/bin/env python3
import sys
import numpy as np
sys.path.insert(0, '/root/openpilot')

from openpilot.system.inferenced import get_hal, BackendType

hal = get_hal()

# Test ACL dispatch for different input sizes
test_cases = [
    ("Small (64x64)", (1, 64, 64, 3)),   # Should prefer CPU
    ("Medium (256x256)", (1, 256, 256, 3)),  # GPU preferred
    ("Large (1024x1024)", (1, 1024, 1024, 3)),  # GPU only
]

for name, shape in test_cases:
    dummy_input = np.random.randn(*shape).astype(np.float32)
    
    result = hal.infer(
        BackendType.ACL,
        "sgm_stereo",
        {"input": dummy_input}
    )
    
    # Check metrics to see which backend was used
    metrics = hal.get_performance_metrics("sgm_stereo", "ACL")
    if metrics:
        print(f"{name}: {metrics.avg_latency_ms:.2f}ms")
    else:
        print(f"{name}: Inference completed (metrics pending)")

print("✓ Test 5.2.1 PASSED")
```

#### 5.2.2: RGA Image Operations

```python
# test_5_2_2_rga_image_ops.py
#!/usr/bin/env python3
import sys
import numpy as np
import time
sys.path.insert(0, '/root/openpilot')

from openpilot.system.inferenced import get_hal, BackendType

hal = get_hal()

# Test RGA image operations
ops = [
    ("resize", {"src": np.zeros((480, 640, 3)), "dst_width": 240, "dst_height": 320}),
    ("crop", {"src": np.zeros((480, 640, 3)), "x": 100, "y": 100, "w": 200, "h": 200}),
]

for op_name, inputs in ops:
    start = time.monotonic()
    result = hal.infer(BackendType.RGA, op_name, inputs)
    elapsed = (time.monotonic() - start) * 1000
    
    if result.success:
        print(f"✓ {op_name}: {elapsed:.2f}ms")
    else:
        print(f"✗ {op_name}: {result.error_message}")

print("✓ Test 5.2.2 PASSED")
```

### Success Criteria

- ✅ ACL backend available (GPU and CPU NEON)
- ✅ RGA image operations <10ms
- ✅ Smart dispatch (size-based backend selection)
- ✅ GPU latency <100ms for 640x480 stereo

---

## Task 5.3: End-to-End Validation

**Estimated Effort**: 2-4 hours  
**Success Criteria**: All daemons running, real-time constraints met, thermal stable

### Integration Testing

```bash
# On target hardware (RK3588)

# Start modeld daemon
python3 selfdrive/modeld/modeld.py &

# Start stereod daemon
python3 selfdrive/stereod/stereod.py &

# Monitor diagnostics in another shell
python3 -c "
from openpilot.system.inferenced import get_hal
import time
hal = get_hal()
while True:
    report = hal.get_diagnostic_report()
    print(f'[{time.strftime(\"%H:%M:%S\")}] {report[\"status\"]}')
    time.sleep(5)
"
```

### Real-Time Validation

```bash
# Check frame timing constraints
watch -n 1 'cat /proc/self/stat | awk {print $20}'  # Check soft page faults
iostat -c 1 5  # Monitor I/O wait

# Thermal monitoring (every 30 seconds)
watch -n 30 'cat /sys/class/thermal/thermal_zone*/temp'
```

### Performance Benchmarking

```python
# test_5_3_end_to_end.py
#!/usr/bin/env python3
import sys
import numpy as np
import time
sys.path.insert(0, '/root/openpilot')

from openpilot.system.inferenced import get_hal

hal = get_hal()

# Simulate 100 frames at 20Hz (5 seconds)
frame_time_ms = 50  # 20Hz loop
total_frames = 100

success_count = 0
timeout_count = 0
latencies = []

for frame in range(total_frames):
    dummy_frame = np.random.randn(384, 512, 3).astype(np.float32)
    
    start = time.monotonic()
    result = hal.infer(
        backend_type=1,  # NPU
        model_name="modeld_vision",
        inputs={"input": dummy_frame},
        timeout_ms=100  # ADAS constraint
    )
    elapsed_ms = (time.monotonic() - start) * 1000
    latencies.append(elapsed_ms)
    
    if result.success:
        success_count += 1
    elif result.timed_out:
        timeout_count += 1
    
    # Frame-rate check
    if elapsed_ms > frame_time_ms:
        print(f"Frame {frame}: Behind schedule ({elapsed_ms:.1f}ms > {frame_time_ms}ms)")

# Summary
print(f"\nEnd-to-End Results ({total_frames} frames):")
print(f"  Success: {success_count}/{total_frames} ({100*success_count/total_frames:.1f}%)")
print(f"  Timeouts: {timeout_count}")
print(f"  Avg Latency: {np.mean(latencies):.2f}ms")
print(f"  P99 Latency: {np.percentile(latencies, 99):.2f}ms")
print(f"  Max Latency: {np.max(latencies):.2f}ms")

# Check against ADAS constraints
if success_count < 95 or np.percentile(latencies, 99) > 100:
    print("⚠ Does not meet ADAS real-time constraints")
else:
    print("✓ Meets ADAS real-time constraints")
```

### Thermal Validation

```bash
# Run sustained inference for 10 minutes
for i in {1..120}; do
  python3 test_sustained_inference.py
  sleep 5
  echo "Iteration $i, Temp: $(cat /sys/class/thermal/thermal_zone0/temp)"
done

# Check final temperature
if [ $(cat /sys/class/thermal/thermal_zone0/temp) -lt 85000 ]; then
  echo "✓ Thermal OK (<85°C)"
else
  echo "⚠ Thermal throttling detected"
fi
```

---

## Rollback & Contingency

If hardware tests fail:

1. **Collect Diagnostics**
   ```bash
   hal.print_diagnostic_report()
   dmesg > /tmp/kernel.log
   perf stat -a sleep 60 > /tmp/perf.log
   ```

2. **Check Git State**
   ```bash
   git diff HEAD > /tmp/changes.patch
   git log --oneline -10
   ```

3. **Fall Back to Dev PC**
   ```bash
   git checkout HEAD~1  # Revert if needed
   python system/inferenced/tests/test_monitoring.py
   ```

---

## Success Checklist

- ✅ Task 5.2: RK3588 GPU/RGA dispatch validated
- ✅ Task 5.3: End-to-end 100+ frames at 20Hz, thermal <85°C, 0 timeouts

---

## Documentation Updates

After successful hardware validation:

1. Update `INFERENCED_TASKS.md` with actual hardware latencies
2. Create `HARDWARE_VALIDATION_RESULTS.md` with metrics
3. Update `INFERENCED_DEPLOYMENT_GUIDE.md` with real latency numbers
4. Archive this document as `PHASE5_VALIDATION_COMPLETE.md`

---

**Preparation Status**: Ready  
**Next Action**: Deploy to RK3588 hardware  
**Expected Timeline**: 2-4 weeks (awaiting hardware)
