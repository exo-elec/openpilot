# Common Module - Usage Audit

**Last Updated**: 2026-04-09

## Overview

The `common/` module contains shared utilities used across the codebase. This document tracks which files are actively used and which may be candidates for cleanup.

---

## Core Files (Actively Used)

| File | Used By | Purpose | Status |
|------|---------|---------|--------|
| `params.py` | System, SelfDrive | Parameter system interface | ✅ Essential |
| `params_keys.h` | System, SelfDrive | Parameter definitions (200+ EOP params) | ✅ Essential |
| `params_pyx.pyx` | Build | Cython params implementation | ✅ Essential |
| `realtime.py` | All daemons | Ratekeeper, timing utilities | ✅ Essential |
| `swaglog.py` | All daemons | Logging infrastructure | ✅ Essential |
| `basedir.py` | Manager, Build | Base directory paths | ✅ Essential |
| `core_config.py` | All daemons | Core affinity, daemon config | ✅ Essential |

---

## Hardware Interface (Used by HAL)

| File | Used By | Purpose | Status |
|------|---------|---------|--------|
| `gpio.py` | Potentially HAL | GPIO sysfs interface | ⚠️ Not currently used |
| `gps.py` | pigeond tests | GPS service selection | ✅ Used in tests |
| `sbu_detection.py` | socketd, can_driver | USB-C SBU detection + semantic CAN mapper | ✅ Active |

**Note**: `gpio.py` provides GPIO access but HAL daemons currently use direct sysfs access. Could be used for `IMU_INT`, `GPS_PWR_EN`, etc.

---

## Math & Control (Used by SelfDrive)

| File | Used By | Purpose | Status |
|------|---------|---------|--------|
| `simple_kalman.py` | radard, selfdrived | Kalman filter | ✅ Active |
| `pid.py` | latcontrol_pid, longcontrol | PID controller | ✅ Active |
| `filter_simple.py` | ? | Simple filters | ⚠️ Check usage |

---

## Logging & Data

| File | Used By | Purpose | Status |
|------|---------|---------|--------|
| `logging_extra.py` | logmessaged | Log formatting | ✅ Active |
| `logging_mcap_patch.py` | ? | MCAP logging patch | ⚠️ Check usage |
| `mcap_*.py` | mcapd | MCAP format support | ✅ Active |
| `file_helpers.py` | loggerd | File operations | ✅ Active |

---

## Utilities (Check Usage)

| File | Purpose | Status | Recommendation |
|------|---------|--------|----------------|
| `api.py` | API client | ⚠️ Check | May be unused |
| `git.py` | Git utilities | ⚠️ Check | May be unused |
| `markdown.py` | Markdown parsing | ⚠️ Check | May be unused |
| `dict_helpers.py` | Dict utilities | ⚠️ Check | May be unused |
| `time_helpers.py` | Time utilities | ⚠️ Check | May be unused |
| `timeout.py` | Timeout decorator | ⚠️ Check | May be unused |
| `retry.py` | Retry decorator | micd | ✅ Used |
| `run.py` | Process runner | ⚠️ Check | May be unused |
| `stat_live.py` | Live statistics | ⚠️ Check | May be unused |
| `spinner.py` | Build spinner | manager/build | ✅ Used |
| `text_window.py` | Text UI | manager | ✅ Used |
| `version.h` | Version info | ⚠️ Check | May be unused |

---

## C/C++ Files (Build Required)

| File | Purpose | Status |
|------|---------|--------|
| `clutil.cc/h` | OpenCL utilities | ⚠️ Check if used |
| `mat.h` | Matrix operations | ⚠️ Check if used |
| `params.cc/h` | C++ params interface | ✅ Used by build |
| `ratekeeper.cc/h` | C++ ratekeeper | ✅ Used by build |
| `swaglog.cc/h` | C++ logging | ✅ Used by build |
| `util.cc/h` | C++ utilities | ✅ Used by build |
| `timing.h` | Timing utilities | ✅ Used by build |
| `watchdog.cc/h` | Watchdog C++ | ✅ Used by build |
| `prefix.h/py` | Path prefix | ✅ Used |
| `queue.h` | Queue utilities | ⚠️ Check |

---

## Transformations (Coordinate Math)

| File | Used By | Purpose | Status |
|------|---------|---------|--------|
| `transformations/*.py` | Model, Calibration | Coordinate transforms | ✅ Essential |
| `transformations/*.cc/hpp` | Build | C++ transforms | ✅ Essential |

---

## Mock & Test

| File | Purpose | Status |
|------|---------|--------|
| `mock/*.py` | Test mocks | ✅ Used in tests |
| `tests/*.py` | Unit tests | ✅ Used |
| `tests/*.cc` | C++ tests | ✅ Used |

---

## Summary

| Category | Count | Status |
|----------|-------|--------|
| **Essential** | ~20 | Keep |
| **Actively Used** | ~15 | Keep |
| **Check Usage** | ~15 | Verify |
| **Potentially Unused** | ~10 | Consider removal |

### Candidates for Removal (Verify First)

1. `api.py` - If not used for cloud API
2. `git.py` - If version info not needed
3. `markdown.py` - If no markdown parsing needed
4. `dict_helpers.py` - If functions unused
5. `time_helpers.py` - If functions unused
6. `stat_live.py` - If statistics not used
7. `clutil.cc/h` - If no OpenCL used

### Safe to Keep

- All params files
- All logging files
- All realtime/timing files
- All transformation files
- All test files
