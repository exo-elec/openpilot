# InferenceD Quick Start Guide

**For**: Operators, integrators, developers  
**Time**: 5-10 minutes  
**Goal**: Get InferenceD running on your system

---

## 1-Minute Setup

### Dev PC (Testing)

```bash
# Clone openpilot EOP10 branch
git clone https://github.com/your-fork/openpilot.git -b EOP10
cd openpilot

# Install
python -m pip install -e .

# Verify
python -c "from openpilot.system.inferenced import get_hal; hal = get_hal(); print('✓ Ready')"
```

### Edge Hardware (RK3588)

```bash
# SSH into device
ssh root@<device-ip>

# Install openpilot (same as dev PC)
cd /root/openpilot && python3 -m pip install -e .

# Verify NPU available
ls -la /dev/rknn* && echo "✓ NPU detected"
```

---

## 5-Minute Test

```python
#!/usr/bin/env python3
from openpilot.system.inferenced import get_hal, BackendType
import numpy as np

hal = get_hal()

# Show available backends
backends = hal.get_available_backends()
print(f"Backends: {[b.name for b in backends]}")

# Run quick inference
dummy_frame = np.random.randn(1, 224, 224, 3).astype(np.float32)
result = hal.infer(BackendType.NPU, "test_model", {"input": dummy_frame})

# Check result
if result.success:
    print(f"✓ Inference OK ({result.inference_time_ms:.2f}ms)")
else:
    print(f"✗ {result.error_message}")

# Show health
report = hal.get_diagnostic_report()
print(f"Status: {report['status']}")
```

---

## Key Operations

### Get Status

```python
from openpilot.system.inferenced import get_hal

hal = get_hal()
report = hal.get_diagnostic_report()

print(f"Health: {report['overall_health']}")
print(f"Status: {report['status']}")  # HEALTHY | DEGRADED | UNKNOWN

# Show metrics
perf = report['performance']
print(f"Avg Latency: {perf['average_latency_ms']:.2f}ms")
print(f"Success Rate: {perf['overall_success_rate']:.1f}%")

# Show alerts
if report['alerts']['critical']:
    for alert in report['alerts']['critical']:
        print(f"CRITICAL: {alert['message']}")
```

### Run Inference

```python
from openpilot.system.inferenced import get_hal, BackendType
import numpy as np

hal = get_hal()

# Prepare input
frame = np.random.randn(1, 224, 224, 3).astype(np.float32)

# Run with timeout (1000ms default)
result = hal.infer(
    BackendType.NPU,
    "model_name",
    {"input": frame},
    timeout_ms=500  # ADAS constraint
)

# Check result
if result.success:
    print("✓ Success")
    print(f"  Latency: {result.inference_time_ms:.2f}ms")
elif result.timed_out:
    print("✗ Timeout")
else:
    print(f"✗ Error: {result.error_message}")
```

### Monitor Performance

```python
from openpilot.system.inferenced import get_hal

hal = get_hal()

# Get all metrics
metrics = hal.get_all_performance_metrics()

for op_key, m in metrics.items():
    print(f"{op_key}:")
    print(f"  Latency: {m.avg_latency_ms:.2f}ms (min={m.min_latency_ms:.1f}, max={m.max_latency_ms:.1f})")
    print(f"  Throughput: {m.throughput_ops_sec:.1f} ops/sec")
    print(f"  Success: {m.success_rate:.1f}%")
```

### Print Diagnostic Report

```python
from openpilot.system.inferenced import get_hal

hal = get_hal()
hal.print_diagnostic_report()

# Output:
# ======================================================================
# INFERENCE HAL DIAGNOSTIC REPORT
# ======================================================================
# Status: HEALTHY
# Overall Health: ✓ HEALTHY
#
# --- Health Checks ---
#   ✓ npu_available: NPU backend available
#   ✓ acl_available: ACL backend not available
#   ✓ backends_healthy: All backends healthy
#
# --- Performance ---
#   Total Operations: 42
#   Success Rate: 100.0%
#   Average Latency: 12.34ms
# ======================================================================
```

---

## Troubleshooting

### "Backend not available"
```bash
# Dev PC: Normal (mocked backends)
# Hardware: Check kernel module
lsmod | grep rknn
ls -la /dev/rknn*

# Load if missing:
insmod /lib/modules/$(uname -r)/kernel/drivers/rknn/rknn_driver.ko
```

### "Inference timeout"
```python
# Increase timeout if needed
result = hal.infer(model, inputs, timeout_ms=2000)

# Or check if system is overloaded
report = hal.get_diagnostic_report()
print(f"Load: {report['performance']['total_operations']}")
```

### "Out of memory"
```python
# Clear model cache
hal.clear_model_cache()

# Or reduce preloaded models
config.models_to_preload = []
```

---

## Configuration

### Minimal Setup

```python
from openpilot.system.inferenced import HAL

hal = HAL()  # Default config
hal.initialize()
```

### Custom Setup

```python
from openpilot.system.inferenced import HAL, HALConfig, BackendType, ModelConfig

config = HALConfig(
    enable_npu=True,
    enable_acl=False,  # GPU not available
    inference_timeout_ms=500,  # ADAS tight deadline
    models_to_preload=[
        (BackendType.NPU, ModelConfig(
            name="modeld_vision",
            path="/path/to/model.rknn"
        ))
    ]
)

hal = HAL(config)
hal.initialize()
```

---

## Performance Expectations

### Dev PC (Mock Backends)

| Backend | Typical Latency | Notes |
|---------|-----------------|-------|
| NPU | <1ms | Mock (numpy) |
| ACL | <1ms | Mock (numpy) |
| RGA | 1-5ms | OpenCV fallback |
| MPP | <1ms | ffmpeg fallback |

### RK3588 (Real Hardware)

| Backend | Typical Latency | Notes |
|---------|-----------------|-------|
| NPU | 10-30ms | Real RKNN inference (3-core) |
| ACL GPU | 20-100ms | Mali G78 (size-dependent) |
| ACL CPU | 50-200ms | NEON fallback |
| RGA | 1-5ms | Real 2D accel |

---

## Common Tasks

### Preload Models on Startup

```python
config = HALConfig(
    models_to_preload=[
        (BackendType.NPU, ModelConfig(name="m1", path="/path/m1.rknn")),
        (BackendType.NPU, ModelConfig(name="m2", path="/path/m2.rknn")),
    ]
)
hal = HAL(config)
hal.initialize()  # Models loaded here
```

### Switch Models

```python
# Model 1 inference
result = hal.infer(BackendType.NPU, "model1", inputs)

# Model 2 inference (cached if preloaded)
result = hal.infer(BackendType.NPU, "model2", inputs)
```

### Handle Timeouts

```python
result = hal.infer(model, inputs, timeout_ms=100)

if result.timed_out:
    print("Operation exceeded deadline")
    # Fallback: use simplified model or skip frame
```

### Check Backend Health

```python
# Individual backend
is_healthy = hal.is_backend_healthy(BackendType.NPU)

# All backends
health = hal.get_backend_health_report()
for name, status in health.items():
    print(f"{name}: {'✓' if status['is_healthy'] else '✗'}")
```

---

## Integration Example

### With modeld Daemon

```python
from openpilot.system.inferenced import get_hal, BackendType
from selfdrive.modeld.modeld import MODELD_MODELS

hal = get_hal()

# Run modeld models
for model_name, model_path in MODELD_MODELS.items():
    frame = get_camera_frame()
    result = hal.infer(
        BackendType.NPU,
        model_name,
        {"input": frame},
        timeout_ms=100
    )
    if result.success:
        process_output(result.outputs)
    else:
        print(f"Inference failed: {result.error_message}")
```

---

## Environment Variables

```bash
# Enable debug logging
export OPENPILOT_LOGLEVEL=DEBUG

# Override timeout (ms)
export INFERENCED_TIMEOUT_MS=500
```

---

## Further Reading

- **Full Deployment Guide**: INFERENCED_DEPLOYMENT_GUIDE.md
- **Hardware Readiness**: PHASE5_HARDWARE_READINESS.md
- **Implementation Details**: INFERENCED_IMPLEMENTATION_SUMMARY.md
- **Architecture Overview**: INFERENCED_INDEX.md

---

## Support

| Issue | Solution | Docs |
|-------|----------|------|
| Backend not available | Check kernel module / dev PC normal | Deployment guide § Troubleshooting |
| Timeout errors | Increase timeout_ms or check load | Deployment guide § Performance Tuning |
| Out of memory | Clear cache or reduce preloads | Deployment guide § Error Recovery |
| Slow inference | Check thermal throttling | Deployment guide § Thermal Management |

---

**Status**: Production Ready  
**Last Updated**: 2026-05-25  
**Version**: InferenceD Phase 6.4
