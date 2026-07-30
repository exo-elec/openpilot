#!/usr/bin/env python3
"""
Unit tests for RecordD - Unified Recording Daemon

Tests cover:
- Impact detection logic
- Recording mode transitions
- Storage management
- Pre-impact buffer
- Settings management
"""
import unittest
import time
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import numpy as np

# Import recordd components
from openpilot.selfdrive.recordd.recordd import (
    RecordingMode, ClipType, ClipInfo, StorageInfo, RecordingSettings,
    ImpactEvent, IMUSample, Snapshot, PreImpactBuffer, ImpactDetector,
    VideoEncoder, RecordD, SENSITIVITY_THRESHOLDS
)


class TestRecordingMode(unittest.TestCase):
    """Test recording mode enum."""
    
    def test_mode_values(self):
        """Test mode enum values."""
        self.assertEqual(RecordingMode.OFF.value, "off")
        self.assertEqual(RecordingMode.NORMAL.value, "normal")
        self.assertEqual(RecordingMode.TIMELAPSE.value, "timelapse")
        self.assertEqual(RecordingMode.EVENT.value, "event")
        self.assertEqual(RecordingMode.IMPACT.value, "impact")


class TestClipInfo(unittest.TestCase):
    """Test ClipInfo dataclass."""
    
    def test_to_dict(self):
        """Test ClipInfo serialization."""
        clip = ClipInfo(
            filename="test.mp4",
            timestamp="20240101_120000",
            duration_sec=60.5,
            size_bytes=1024*1024*50,  # 50MB
            clip_type="impact",
            is_event=True,
            is_preserved=True,
            event_reason="crash",
            impact_level=0.85
        )
        
        data = clip.to_dict()
        self.assertEqual(data['filename'], "test.mp4")
        self.assertEqual(data['timestamp'], "20240101_120000")
        self.assertEqual(data['duration_sec'], 60.5)
        self.assertEqual(data['size_mb'], 50.0)
        self.assertEqual(data['type'], "impact")
        self.assertTrue(data['preserved'])
        self.assertEqual(data['event_reason'], "crash")
        self.assertEqual(data['impact_level'], 0.85)


class TestStorageInfo(unittest.TestCase):
    """Test StorageInfo dataclass."""
    
    def test_to_dict(self):
        """Test StorageInfo serialization."""
        info = StorageInfo(
            total_bytes=100*1024**3,  # 100GB
            used_bytes=45*1024**3,    # 45GB
            free_bytes=55*1024**3,    # 55GB
            clips_count=100,
            events_count=5,
            impact_count=2,
            total_duration_hours=10.5,
            recording_mode="normal",
            impact_sensitivity=75
        )
        
        data = info.to_dict()
        self.assertEqual(data['total_gb'], 100.0)
        self.assertEqual(data['used_gb'], 45.0)
        self.assertEqual(data['free_gb'], 55.0)
        self.assertEqual(data['used_percent'], 45.0)
        self.assertEqual(data['clips_count'], 100)
        self.assertEqual(data['events_count'], 5)
        self.assertEqual(data['impact_count'], 2)
        self.assertEqual(data['total_duration_hours'], 10.5)
        self.assertEqual(data['recording_mode'], "normal")
        self.assertEqual(data['impact_sensitivity'], 75)


class TestRecordingSettings(unittest.TestCase):
    """Test RecordingSettings dataclass."""
    
    def test_default_values(self):
        """Test default settings."""
        settings = RecordingSettings()
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.quality, "medium")
        self.assertEqual(settings.segment_duration_sec, 60)
        self.assertEqual(settings.impact_sensitivity, 50)
        self.assertEqual(settings.impact_pre_buffer_sec, 10.0)
        self.assertEqual(settings.impact_post_buffer_sec, 30.0)
        self.assertTrue(settings.parking_mode_enabled)
        self.assertEqual(settings.parking_duration_hours, 12.0)


class TestImpactEvent(unittest.TestCase):
    """Test ImpactEvent dataclass."""
    
    def test_to_dict(self):
        """Test ImpactEvent serialization."""
        event = ImpactEvent(
            timestamp=1234567890.5,
            level=0.75,
            g_force=2.5,
            duration_ms=150.0,
            triggered_by='imu',
            accel=(10.0, 5.0, 20.0)
        )
        
        data = event.to_dict()
        self.assertEqual(data['timestamp'], 1234567890.5)
        self.assertEqual(data['level'], 0.75)
        self.assertEqual(data['g_force'], 2.5)
        self.assertEqual(data['duration_ms'], 150.0)
        self.assertEqual(data['triggered_by'], 'imu')
        self.assertEqual(data['accel'], [10.0, 5.0, 20.0])
        self.assertIn('timestamp_str', data)


class TestIMUSample(unittest.TestCase):
    """Test IMUSample dataclass."""
    
    def test_to_dict(self):
        """Test IMUSample serialization."""
        sample = IMUSample(
            timestamp_ns=1234567890123456789,
            accel=np.array([1.0, 2.0, 9.8]),
            gyro=np.array([0.1, 0.2, 0.3])
        )
        
        data = sample.to_dict()
        self.assertEqual(data['timestamp_ns'], 1234567890123456789)
        self.assertEqual(data['accel']['x'], 1.0)
        self.assertEqual(data['accel']['y'], 2.0)
        self.assertEqual(data['accel']['z'], 9.8)
        self.assertEqual(data['gyro']['x'], 0.1)
        self.assertEqual(data['gyro']['y'], 0.2)
        self.assertEqual(data['gyro']['z'], 0.3)


class TestSnapshot(unittest.TestCase):
    """Test Snapshot dataclass."""
    
    def test_compute_imu_stats(self):
        """Test IMU statistics computation."""
        imu_samples = [
            IMUSample(timestamp_ns=1, accel=np.array([0, 0, 9.8]), gyro=np.array([0, 0, 0])),
            IMUSample(timestamp_ns=2, accel=np.array([0, 0, 19.6]), gyro=np.array([0, 0, 0])),
            IMUSample(timestamp_ns=3, accel=np.array([0, 0, 29.4]), gyro=np.array([0, 0, 0])),
        ]
        
        snap = Snapshot(
            timestamp_ns=1234567890,
            image=np.zeros((100, 100, 3), dtype=np.uint8),
            imu_samples=imu_samples,
            metadata={}
        )
        
        stats = snap.compute_imu_stats()
        self.assertEqual(stats['max_g'], 3.0)  # 29.4 / 9.81 ≈ 3
        self.assertEqual(stats['min_g'], 1.0)  # 9.8 / 9.81 ≈ 1
        self.assertEqual(stats['sample_count'], 3)


class TestPreImpactBuffer(unittest.TestCase):
    """Test PreImpactBuffer class."""
    
    def setUp(self):
        self.buffer = PreImpactBuffer(max_frames=10)
    
    def test_add_frame(self):
        """Test adding frames to buffer."""
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        self.buffer.add_frame(frame, time.monotonic())
        self.assertEqual(len(self.buffer._buffer), 1)
    
    def test_maxlen(self):
        """Test buffer respects max length."""
        for i in range(20):
            frame = np.ones((100, 100, 3), dtype=np.uint8) * i
            self.buffer.add_frame(frame, time.monotonic())
        
        self.assertEqual(len(self.buffer._buffer), 10)
    
    def test_get_frames(self):
        """Test getting recent frames."""
        now = time.monotonic()
        for i in range(5):
            frame = np.ones((100, 100, 3), dtype=np.uint8) * i
            self.buffer.add_frame(frame, now - (4-i) * 0.1)  # 100ms apart
        
        frames = self.buffer.get_frames(0.25)  # Last 250ms
        self.assertEqual(len(frames), 3)  # Should get last 3 frames
    
    def test_clear(self):
        """Test clearing buffer."""
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        self.buffer.add_frame(frame, time.monotonic())
        self.buffer.clear()
        self.assertEqual(len(self.buffer._buffer), 0)


class TestImpactDetector(unittest.TestCase):
    """Test ImpactDetector class."""
    
    def setUp(self):
        self.detector = ImpactDetector(sensitivity=50)
    
    def test_sensitivity_thresholds(self):
        """Test sensitivity to threshold mapping."""
        # Test known values
        self.assertEqual(SENSITIVITY_THRESHOLDS[0], 4.0)
        self.assertEqual(SENSITIVITY_THRESHOLDS[50], 2.0)
        self.assertEqual(SENSITIVITY_THRESHOLDS[100], 1.0)
    
    def test_set_sensitivity(self):
        """Test setting sensitivity."""
        self.detector.set_sensitivity(75)
        self.assertEqual(self.detector._sensitivity, 75)
    
    def test_set_enabled(self):
        """Test enabling/disabling detection."""
        self.detector.set_enabled(False)
        self.assertFalse(self.detector._enabled)
        
        self.detector.set_enabled(True)
        self.assertTrue(self.detector._enabled)
    
    def test_no_trigger_normal_driving(self):
        """Test no false triggers during normal driving."""
        # Normal driving ~1G
        accel = np.array([0.5, 0.2, 9.5])  # ~1G total
        result = self.detector.process_imu(accel, np.zeros(3))
        self.assertIsNone(result)
    
    def test_trigger_on_impact(self):
        """Test impact detection."""
        callback_mock = Mock()
        self.detector.register_callback(callback_mock)
        
        # High G impact (3G > 2G threshold at sensitivity 50)
        accel = np.array([15.0, 5.0, 25.0])  # ~3G total
        
        # First call starts impact
        result = self.detector.process_imu(accel, np.zeros(3), timestamp=time.monotonic())
        self.assertIsNone(result)  # Impact not finished yet
        
        # Second call ends impact
        time.sleep(0.06)  # 60ms > 50ms min duration
        result = self.detector.process_imu(np.array([0.5, 0.2, 9.5]), np.zeros(3), timestamp=time.monotonic())
        
        self.assertIsNotNone(result)
        self.assertEqual(result.triggered_by, 'imu')
        self.assertGreater(result.g_force, 2.0)
        callback_mock.assert_called_once()
    
    def test_cooldown(self):
        """Test cooldown prevents multiple triggers."""
        # First impact
        accel = np.array([15.0, 5.0, 25.0])
        self.detector.process_imu(accel, np.zeros(3), timestamp=time.monotonic())
        time.sleep(0.06)
        self.detector.process_imu(np.array([0.5, 0.2, 9.5]), np.zeros(3), timestamp=time.monotonic())
        
        # Second impact during cooldown
        accel2 = np.array([20.0, 10.0, 30.0])
        result = self.detector.process_imu(accel2, np.zeros(3), timestamp=time.monotonic())
        
        # Should be None due to cooldown
        self.assertIsNone(result)
    
    def test_manual_impact(self):
        """Test manual impact marking."""
        callback_mock = Mock()
        self.detector.register_callback(callback_mock)
        
        event = self.detector.mark_manual_impact()
        
        self.assertEqual(event.triggered_by, 'manual')
        self.assertEqual(event.level, 1.0)
        callback_mock.assert_called_once()
    
    def test_get_status(self):
        """Test getting detector status."""
        status = self.detector.get_status()
        self.assertIn('enabled', status)
        self.assertIn('sensitivity', status)
        self.assertIn('threshold_g', status)
        self.assertIn('current_g', status)


class TestVideoEncoder(unittest.TestCase):
    """Test VideoEncoder class."""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.output_path = Path(self.temp_dir) / "test.mp4"
    
    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    @patch('openpilot.selfdrive.recordd.recordd.HAS_INFERENCED', False)
    def test_ffmpeg_fallback(self):
        """Test FFmpeg fallback when inferenced not available."""
        encoder = VideoEncoder()
        self.assertFalse(encoder._use_inferenced)
        self.assertTrue(encoder._use_ffmpeg)
    
    def test_start_stop(self):
        """Test encoder start/stop lifecycle."""
        encoder = VideoEncoder()
        
        quality_preset = {
            'width': 1920,
            'height': 1080,
            'fps': 20,
            'bitrate': 4000
        }
        
        # Should use FFmpeg fallback in test environment
        with patch.object(encoder, '_start_ffmpeg') as mock_start:
            encoder.start(self.output_path, quality_preset)
            mock_start.assert_called_once_with(quality_preset)


class TestRecordDSettings(unittest.TestCase):
    """Test RecordD settings management."""
    
    @patch('openpilot.selfdrive.recordd.recordd.Params')
    def test_load_settings(self, mock_params_class):
        """Test loading settings from params."""
        mock_params = Mock()
        mock_params.get.return_value = "75"
        mock_params.get_bool.return_value = True
        mock_params_class.return_value = mock_params
        
        # Create minimal RecordD for testing
        with patch('openpilot.selfdrive.recordd.recordd.set_daemon_affinity'):
            with patch('openpilot.selfdrive.recordd.recordd.messaging.SubMaster'):
                with patch('openpilot.selfdrive.recordd.recordd.messaging.PubMaster'):
                    recordd = RecordD()
                    recordd._load_settings()
                    
                    self.assertEqual(recordd.settings.impact_sensitivity, 75)


class TestIntegration(unittest.TestCase):
    """Integration tests for recordd components."""
    
    def test_impact_to_clip_info(self):
        """Test impact event creates proper clip info."""
        impact = ImpactEvent(
            timestamp=time.monotonic(),
            level=0.8,
            g_force=3.0,
            duration_ms=200.0,
            triggered_by='imu',
            accel=(20.0, 10.0, 25.0)
        )
        
        clip = ClipInfo(
            filename="impact_20240101_120000.mp4",
            timestamp="20240101_120000",
            duration_sec=30.0,
            size_bytes=50*1024*1024,
            clip_type="impact",
            is_event=True,
            is_preserved=True,
            event_reason=f"impact_{impact.triggered_by}",
            impact_level=impact.level
        )
        
        data = clip.to_dict()
        self.assertEqual(data['type'], "impact")
        self.assertTrue(data['preserved'])
        self.assertEqual(data['impact_level'], 0.8)


def run_tests():
    """Run all tests."""
    unittest.main()


if __name__ == '__main__':
    run_tests()
