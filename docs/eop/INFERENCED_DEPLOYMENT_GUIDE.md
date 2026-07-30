# InferenceD Deployment & Operations Guide

**Phase 6.5 - Production Documentation**

---

## Table of Contents

1. [Hardware Requirements](#hardware-requirements)
2. [Deployment Guide](#deployment-guide)
3. [Configuration](#configuration)
4. [Troubleshooting](#troubleshooting)
5. [Performance Tuning](#performance-tuning)
6. [Monitoring & Diagnostics](#monitoring--diagnostics)
7. [FAQs](#faqs)

---

## Hardware Requirements

### Supported Platforms

| Platform | CPU | Memory | NPU | GPU | Notes |
|----------|-----|--------|-----|-----|-------|
| **RK3576** | 4×A72 + 4×A55 | 8GB LPDDR4X | 2-core 6 TOPS | Mali G52 | ExoPilot 02M — VisionPilot only |
| **RK3588** | Octa (4xA76 + 4xA55) | 8 GB LPDDR4 | 1x 3-core | Mali G78 MP20 | Reference platform |
| **Dev PC** | x86-64 | Variable | — | — | Testing only (mocked backends) |

### Minimum Requirements

- **RAM**: 4 GB (2 GB free for inference)
- **Storage**: 1 GB for system + 500 MB for models
- **Thermal**: Active cooling recommended (sustained inference >5W)
- **Power**: 5V/2A minimum (higher recommended for NPU-intensive workloads)

### Recommended Hardware Setup

```
ExoPilot Box
├── RK3588/3588S2 SoM
├── Heatsink + thermal paste
├── 5V/3A power supply (USB-C)
├── Optional: Fan for sustained operation
└── Camera input (CSI-2 native or USB)
```

---

## Deployment Guide

### 1. System Preparation

#### On Dev PC (Testing)

```bash
# Install OpenPilot dependencies
cd openpilot
python -m pip install -e .

# Verify InferenceD available
python -c "from openpilot.system.inferenced import get_hal; hal = get_hal(); print(hal.get_available_backends())"
# Output: [<BackendType.NPU: 1>, <BackendType.RGA: 4>, <BackendType.MPP: 5>]
```

#### On Edge Hardware (RK3588)

```bash
# Deploy openpilot to target
scp -r openpilot/ root@exopilot:/root/

# SSH into device
ssh root@exopilot

# Install runtime dependencies
apt-get update
apt-get install -y \
  python3 \
  libatlas-base-dev \
  libblas-dev \
  liblapack-dev \
  libopenblas-dev

# Install openpilot
cd /root/openpilot
python3 -m pip install -e .
```

### 2. Starting InferenceD

#### As Standalone Service

```bash
# Manual start (for testing)
python -c "
from openpilot.system.inferenced import get_hal
hal = get_hal()
hal.initialize()
print('✓ InferenceD initialized')
report = hal.get_diagnostic_report()
print(f'Status: {report[\"status\"]}')"
```

#### As System Daemon

```bash
# Create systemd service
cat > /etc/systemd/system/inferenced.service << EOF
[Unit]
Description=InferenceD Acceleration Daemon
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/openpilot
ExecStart=/usr/bin/python3 -c "from openpilot.system.inferenced import get_hal; hal = get_hal(); hal.initialize()"
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Enable and start
systemctl daemon-reload
systemctl enable inferenced
systemctl start inferenced
systemctl status inferenced
```

### 3. Model Preloading

Configure models to load on startup:

```python
from openpilot.system.inferenced import HAL, HALConfig, BackendType, ModelConfig

config = HALConfig(
    enable_npu=True,
    enable_acl=True,
    enable_rga=True,
    enable_mpp=True,
    inference_timeout_ms=1000.0,
    models_to_preload=[
        (BackendType.NPU, ModelConfig(
            name="modeld_vision",
            path="/data/models/modeld_vision.rknn",
            model_type="vision"
        )),
        (BackendType.NPU, ModelConfig(
            name="modeld_policy",
            path="/data/models/modeld_policy.rknn",
            model_type="policy"
        )),
    ]
)

hal = HAL(config)
hal.initialize()
```

### 4. Integration with Daemons

InferenceD is designed to work with existing OpenPilot daemons:

#### modeld (Vision Models)

```python
from openpilot.system.inferenced import get_hal, BackendType
import numpy as np

hal = get_hal()

# Prepare input
frame = np.zeros((384, 512, 3), dtype=np.float32)  # RGB frame

# Run inference
result = hal.infer(
    BackendType.NPU,
    "modeld_vision",
    {"input": frame},
    timeout_ms=100  # 100ms deadline for ADAS loop
)

if result.success:
    print(f"✓ Inference: {result.inference_time_ms:.2f}ms")
else:
    print(f"✗ Error: {result.error_message}")
```

#### stereod (Stereo Depth)

```python
from openpilot.system.inferenced import get_hal, BackendType

hal = get_hal()

# SGM stereo depth runs on ACL (GPU/CPU auto-select)
left_frame = np.zeros((480, 640), dtype=np.uint8)
right_frame = np.zeros((480, 640), dtype=np.uint8)

result = hal.infer(
    BackendType.ACL,
    "sgm_stereo",
    {
        "left": left_frame,
        "right": right_frame,
        "width": 640,
        "height": 480
    },
    timeout_ms=200  # 200ms for stereo
)
```

---

## Configuration

### HALConfig Parameters

```python
from openpilot.system.inferenced import HALConfig

config = HALConfig(
    # Backend enablement
    enable_npu=True,           # Neural Processing Unit (RKNN models)
    enable_acl=True,           # ARM Compute Library (GPU/CPU auto-select)
    enable_rga=True,           # Rockchip Graphics Accelerator (image ops)
    enable_mpp=True,           # Media Process Platform (video codec)
    enable_hailo=True,         # Hailo NPU (gracefully disabled if unavailable)
    
    # Threading
    num_workers=4,             # Worker threads in executor pool
    max_queue_size=100,        # Max inference requests in queue
    
    # Timing
    inference_timeout_ms=1000.0,  # CRITICAL: Max inference time (ADAS safety)
    
    # Model preloading
    models_to_preload=[
        # List of (BackendType, ModelConfig) tuples
        # Models loaded on hal.initialize()
    ]
)
```

### Environment Variables

```bash
# Enable debug logging
export OPENPILOT_LOGLEVEL=DEBUG

# Override default config
export INFERENCED_TIMEOUT_MS=500  # Custom timeout

# Select specific backends (comma-separated)
export INFERENCED_BACKENDS=NPU,ACL,RGA,MPP
```

---

## Troubleshooting

### Issue: "Backend not available"

**Symptom**: `Backend NPU not available` error

**Causes**:
1. Hardware not present (dev PC testing)
2. Missing SDK/library files
3. Kernel module not loaded

**Solutions**:

```bash
# Check hardware availability
lspci | grep -i gpu
lsusb | grep -i npu

# Check libraries
ldconfig | grep arm_compute
ldconfig | grep rknn

# On RK3588, verify NPU driver loaded
lsmod | grep rknn
# If missing: insmod /lib/modules/$(uname -r)/kernel/drivers/rknn/rknn_driver.ko

# Check device permissions
ls -la /dev/rknn*
chmod 666 /dev/rknn*
```

### Issue: Timeout Errors

**Symptom**: `Inference timeout (1000ms)` errors during normal operation

**Probable Causes**:
1. Timeout value too aggressive
2. Backend overloaded (>1 request queued)
3. Model too large for hardware

**Solutions**:

```bash
# Check system load
top -bn1 | head -15

# Check thermal throttling
cat /sys/class/thermal/thermal_zone*/temp

# Adjust timeout if needed
export INFERENCED_TIMEOUT_MS=2000

# Check for queued requests (if available in monitoring)
python -c "
from openpilot.system.inferenced import get_hal
hal = get_hal()
report = hal.get_diagnostic_report()
print('Performance:')
print(hal._performance_monitor.get_summary())"
```

### Issue: Out of Memory (OOM)

**Symptom**: `Resource exhausted` or `MemoryError`

**Solutions**:

```bash
# Check available memory
free -h

# Clear model cache if not needed
from openpilot.system.inferenced import get_hal
hal = get_hal()
hal.clear_model_cache()

# Reduce models_to_preload list
config.models_to_preload = []  # Don't preload large models

# Monitor memory usage during inference
watch -n 1 free -h
```

### Issue: NPU Not Detected

**Symptom**: NPU backend initialization fails on RK3588

**Check**:
1. Device support: Confirm RK3588 platform
2. SDK version: Update to latest RK NPU toolkit
3. Device node: `/dev/rknn*` should exist

**Solution**:
```bash
# Verify RKNN library
apt-cache policy librknpu

# Manually reinstall
apt-get install --reinstall librknpu-dev librknpu

# Reboot to load kernel modules
reboot
```

### Issue: ACL Backend Unavailable

**Symptom**: "ACL backend initialization failed" on RK3588

**Cause**: libarm_compute.so missing or incompatible

**Solution**:
```bash
# Install ARM Compute Library for your chip
wget https://github.com/ARM-software/ComputeLibrary/releases/download/v23.05/acl-static-x86_64.tar.gz
tar xzf acl-static-x86_64.tar.gz
cp lib/*.so* /usr/local/lib/

# If GPU is not needed, disable it
from openpilot.system.inferenced import HALConfig
config = HALConfig(enable_acl=False)
```

---

## Performance Tuning

### Timeout Optimization

```python
# ADAS safety critical: keep timeout < 100ms per inference
# For batch operations: timeout_ms = max_latency * num_operations

# Modeld inference: 50-100ms
hal.infer(BackendType.NPU, "modeld", inputs, timeout_ms=100)

# Stereo depth: 100-200ms
hal.infer(BackendType.ACL, "stereo", inputs, timeout_ms=200)

# Image operations: 10-50ms
hal.infer(BackendType.RGA, "resize", inputs, timeout_ms=50)
```

### Model Selection

| Scenario | Backend | Reason |
|----------|---------|--------|
| Real-time vision | NPU | Lowest latency (~10-20ms), power efficient |
| Stereo depth | ACL GPU | 640x480 → GPU, <100x100 → CPU |
| Image preprocessing | RGA | Dedicated 2D accelerator (resize, color convert) |
| Video encoding | MPP | H.264 codec, 0.5-1ms overhead |
| Fallback (no HW) | ACL CPU | NEON-optimized CPU compute |

### Monitoring for Tuning

```python
from openpilot.system.inferenced import get_hal

hal = get_hal()

# After running inference workload
report = hal.get_diagnostic_report()
print(f"Status: {report['status']}")
print(f"Avg Latency: {report['performance']['average_latency_ms']:.2f}ms")
print(f"Success Rate: {report['performance']['overall_success_rate']:.1f}%")

# Check for alerts
if report['alerts']['critical']:
    print("⚠ Critical alerts detected:")
    for alert in report['alerts']['critical']:
        print(f"  - {alert['message']}")

# Identify bottlenecks
for op, metrics in report['alerts']['by_operation'].items():
    print(f"Operation {op}: {[m['message'] for m in metrics]}")
```

### CPU Affinity Tuning

```bash
# Pin inferenced to specific cores
# On RK3588: Big cores (A76 0-3), Little cores (A55 4-7)

taskset -c 0-3 python inferenced_daemon.py  # RK3588: big cores
```

### Thermal Management

```bash
# Monitor GPU/NPU temperature
watch -n 1 'cat /sys/class/thermal/thermal_zone*/temp'

# Check thermal throttling
dmesg | grep -i throttle

# If temps >85°C: reduce num_workers
config = HALConfig(num_workers=2)

# If sustained >90°C: add cooling or reduce workload
```

---

## Monitoring & Diagnostics

### Health Checks

```python
from openpilot.system.inferenced import get_hal

hal = get_hal()

# Get comprehensive health report
report = hal.get_diagnostic_report()

# Check overall system health
print(f"Overall Health: {report['overall_health']}")
print(f"Status: {report['status']}")  # HEALTHY | DEGRADED | UNKNOWN

# Check individual backends
for name, check in report['health_checks'].items():
    status = "✓" if check['is_healthy'] else "✗"
    print(f"{status} {name}: {check['message']}")
```

### Performance Metrics

```python
# Get real-time performance data
metrics = hal.get_all_performance_metrics()

for op_key, metrics in metrics.items():
    print(f"Operation: {op_key}")
    print(f"  Latency: {metrics.avg_latency_ms:.2f}ms (min={metrics.min_latency_ms:.2f}ms, max={metrics.max_latency_ms:.2f}ms)")
    print(f"  Throughput: {metrics.throughput_ops_sec:.1f} ops/sec")
    print(f"  Success Rate: {metrics.success_rate:.1f}%")
```

### Error Summary

```python
# Get error statistics
error_summary = hal.get_error_summary()

print(f"Total Errors: {error_summary.get('total_errors', 0)}")
if 'errors_by_category' in error_summary:
    for category, count in error_summary['errors_by_category'].items():
        print(f"  {category}: {count}")

# Check last error
if 'last_error' in error_summary:
    last = error_summary['last_error']
    print(f"Last Error: {last['category']} - {last['message']}")
```

### Logging

```python
# Enable detailed logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Access logs
tail -f /var/log/openpilot/inferenced.log

# Filter by backend
grep "NPU" /var/log/openpilot/inferenced.log
grep "TIMEOUT" /var/log/openpilot/inferenced.log
grep "CRITICAL" /var/log/openpilot/inferenced.log
```

---

## FAQs

**Q: What's the expected inference latency?**

A: On real hardware (RK3588):
- RKNN models: 10-30ms depending on size
- ACL stereo depth: 50-150ms
- RGA image ops: 1-5ms
- Development mode (mocks): <1ms

**Q: Can I use multiple models simultaneously?**

A: Yes, via model caching:
```python
hal.cache_model(model1)
hal.cache_model(model2)
# Both available in memory for fast switching
```

**Q: How do I handle inference failures?**

A: Check `result.success` and `result.error_message`:
```python
result = hal.infer(...)
if not result.success:
    if result.timed_out:
        # Handle timeout
    else:
        # Handle other errors
```

**Q: What's the maximum inference timeout?**

A: For ADAS: <100ms per operation. Max system timeout is configurable, but longer timeouts defeat real-time requirements.

**Q: Can I disable specific backends?**

A: Yes, in HALConfig:
```python
config = HALConfig(
    enable_npu=False,   # Disable NPU
    enable_acl=False    # Disable ACL
)
```

**Q: How do I profile my custom models?**

A: Use performance monitoring:
```python
result = hal.infer(BackendType.NPU, "my_model", inputs)
metrics = hal.get_performance_metrics("my_model", "NPU")
print(f"Latency: {metrics.avg_latency_ms:.2f}ms")
```

---

## Support & Debugging

For issues not covered here:

1. Check INFERENCED_INDEX.md for architecture overview
2. Review PHASE4_PERFORMANCE_REPORT.md for baseline expectations
3. Enable DEBUG logging: `export OPENPILOT_LOGLEVEL=DEBUG`
4. Collect diagnostic report: `hal.print_diagnostic_report()`
5. Check system resources: `top`, `free -h`, thermal sensors

---

**Last Updated**: 2026-05-25  
**InferenceD Version**: Phase 6.4  
**Documentation Status**: Production Ready
