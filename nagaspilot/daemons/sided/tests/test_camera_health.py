#!/usr/bin/env python3
"""Tests for CameraHealthTracker."""

from __future__ import annotations

import time

from openpilot.selfdrive.sided.camera_health import CameraHealthTracker


def test_no_fault_during_startup_grace():
    tracker = CameraHealthTracker(timeout_s=0.1, startup_grace_s=10.0)
    fault, reason = tracker.check(['side_left'])
    assert not fault
    assert reason == ""


def test_fault_when_camera_silent():
    tracker = CameraHealthTracker(timeout_s=0.05, startup_grace_s=0.0)
    fault, reason = tracker.check(['side_left'])
    assert fault
    assert 'side_left' in reason


def test_no_fault_when_frames_arrive():
    tracker = CameraHealthTracker(timeout_s=0.5, startup_grace_s=0.0)
    tracker.mark_frame('side_left')
    tracker.mark_frame('side_right')
    time.sleep(0.05)
    fault, reason = tracker.check(['side_left', 'side_right'])
    assert not fault
    assert reason == ""


def test_fault_only_for_silent_camera():
    tracker = CameraHealthTracker(timeout_s=0.2, startup_grace_s=0.0)
    tracker.mark_frame('side_left')
    # side_left is recent; side_right has never produced a frame.
    fault, reason = tracker.check(['side_left', 'side_right'])
    assert fault
    assert 'side_right' in reason
    assert 'side_left' not in reason


def test_reset_clears_history():
    tracker = CameraHealthTracker(timeout_s=0.05, startup_grace_s=0.5)
    tracker.mark_frame('side_left')
    tracker.reset()
    # After reset the startup grace period starts again.
    fault, reason = tracker.check(['side_left'])
    assert not fault
    assert reason == ""
