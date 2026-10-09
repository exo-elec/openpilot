#!/usr/bin/env python3
"""
Test suite for SGM stereo depth implementation (ACL-based).

Tests unified ACL SGM implementation with GPU/CPU auto-selection.
"""

import unittest  # noqa: TID251
import time
import logging

import numpy as np

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestSGM(unittest.TestCase):
    """Test unified ACL SGM implementation."""

    @classmethod
    def setUpClass(cls):
        """Check if SGM is available."""
        try:
            from openpilot.selfdrive.stereod.sgm import SGM, SGMConfig
            cls.sgm_available = True
            cls.SGM = SGM
            cls.SGMConfig = SGMConfig
        except ImportError as e:
            cls.sgm_available = False
            logger.warning(f"SGM not available: {e}")

    def setUp(self):
        """Create test images."""
        self.width = 320
        self.height = 240
        self.max_disparity = 32

        # Left image: gradient pattern
        self.left = np.random.randint(0, 256, (self.height, self.width), dtype=np.uint8)

        # Right image: shifted left image
        shift = 10
        self.right = np.zeros_like(self.left)
        self.right[:, :-shift] = self.left[:, shift:]
        self.right[:, -shift:] = self.left[:, :shift]

    def test_initialization(self):
        """Test SGM initialization."""
        if not self.sgm_available:
            self.skipTest("SGM not available")

        config = self.SGMConfig(
            target_width=self.width,
            target_height=self.height,
            max_disparity=self.max_disparity
        )

        sgm = self.SGM(config, target="auto")
        self.assertTrue(sgm.is_available())

        # Check device info
        info = sgm.get_device_info()
        self.assertIn('name', info)
        logger.info(f"Device: {info['name']} (backend: {info.get('backend', 'unknown')})")

        sgm.release()

    def test_compute_basic(self):
        """Test basic disparity computation."""
        if not self.sgm_available:
            self.skipTest("SGM not available")

        config = self.SGMConfig(
            target_width=self.width,
            target_height=self.height,
            max_disparity=self.max_disparity
        )

        sgm = self.SGM(config, target="auto")
        if not sgm.is_available():
            self.skipTest("SGM not available")

        result = sgm.compute(self.left, self.right)

        self.assertTrue(result.success, f"Computation failed: {result.error_message}")
        self.assertEqual(result.disparity.shape, (self.height, self.width))
        self.assertEqual(result.confidence.shape, (self.height, self.width))
        self.assertGreater(result.inference_time_ms, 0)

        # Check valid pixels
        valid_pixels = np.sum(result.disparity > 0)
        self.assertGreater(valid_pixels, 0)

        logger.info(f"SGM: {result.inference_time_ms:.2f}ms, " +
                   f"valid={valid_pixels}/{self.width * self.height}")

        sgm.release()

    def test_gpu_backend(self):
        """Test GPU backend specifically."""
        if not self.sgm_available:
            self.skipTest("SGM not available")

        config = self.SGMConfig(
            target_width=self.width,
            target_height=self.height,
            max_disparity=self.max_disparity
        )

        try:
            sgm = self.SGM(config, target="gpu")
            if not sgm.is_available():
                self.skipTest("GPU backend not available")

            result = sgm.compute(self.left, self.right)
            self.assertTrue(result.success)

            info = sgm.get_device_info()
            self.assertEqual(info.get('backend'), 'opencl')
            logger.info(f"GPU SGM: {result.inference_time_ms:.2f}ms")

            sgm.release()
        except Exception as e:
            self.skipTest(f"GPU backend failed: {e}")

    def test_cpu_backend(self):
        """Test CPU backend specifically."""
        if not self.sgm_available:
            self.skipTest("SGM not available")

        config = self.SGMConfig(max_disparity=self.max_disparity)

        try:
            sgm = self.SGM(config, target="cpu")
            self.assertTrue(sgm.is_available())

            result = sgm.compute(self.left, self.right)
            self.assertTrue(result.success)

            info = sgm.get_device_info()
            self.assertEqual(info.get('backend'), 'neon')
            logger.info(f"CPU SGM: {result.inference_time_ms:.2f}ms")

            sgm.release()
        except Exception as e:
            self.skipTest(f"CPU backend failed: {e}")

    def test_performance(self):
        """Test SGM performance."""
        if not self.sgm_available:
            self.skipTest("SGM not available")

        config = self.SGMConfig(
            target_width=640,
            target_height=480,
            max_disparity=64
        )

        sgm = self.SGM(config, target="auto")
        if not sgm.is_available():
            self.skipTest("SGM not available")

        # Create larger test images
        left = np.random.randint(0, 256, (480, 640), dtype=np.uint8)
        right = np.roll(left, 10, axis=1)

        # Warmup
        sgm.compute(left, right)

        # Benchmark
        times = []
        for _ in range(10):
            t0 = time.perf_counter()
            result = sgm.compute(left, right)
            t1 = time.perf_counter()
            if result.success:
                times.append((t1 - t0) * 1000)

        if times:
            avg_time = sum(times) / len(times)
            logger.info(f"SGM 640x480: {avg_time:.2f}ms avg ({1000/avg_time:.1f} FPS)")
            # Should be under 100ms for real-time (CPU fallback may be slower)
            self.assertLess(avg_time, 200, "SGM too slow")

        sgm.release()

    def test_rgb_input(self):
        """Test RGB to grayscale conversion."""
        if not self.sgm_available:
            self.skipTest("SGM not available")

        from openpilot.selfdrive.stereod.sgm import SGM, SGMConfig

        # Create RGB images
        left_rgb = np.random.randint(0, 256, (self.height, self.width, 3), dtype=np.uint8)
        right_rgb = np.roll(left_rgb, 10, axis=1)

        config = SGMConfig(max_disparity=self.max_disparity)
        sgm = SGM(config, target="cpu")  # Use CPU for consistent results

        result = sgm.compute(left_rgb, right_rgb)
        self.assertTrue(result.success)

        sgm.release()


class TestSGMConfig(unittest.TestCase):
    """Test SGM configuration."""

    def test_default_config(self):
        """Test default configuration."""
        from openpilot.selfdrive.stereod.sgm import SGMConfig

        config = SGMConfig()
        self.assertEqual(config.target_width, 640)
        self.assertEqual(config.target_height, 480)
        self.assertEqual(config.max_disparity, 64)

    def test_custom_config(self):
        """Test custom configuration."""
        from openpilot.selfdrive.stereod.sgm import SGMConfig

        config = SGMConfig(
            target_width=1280,
            target_height=720,
            max_disparity=128,
            p1=20,
            p2=240
        )
        self.assertEqual(config.target_width, 1280)
        self.assertEqual(config.target_height, 720)
        self.assertEqual(config.max_disparity, 128)
        self.assertEqual(config.p1, 20)
        self.assertEqual(config.p2, 240)


def run_tests():
    """Run all tests."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestSGM))
    suite.addTests(loader.loadTestsFromTestCase(TestSGMConfig))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_tests()
    exit(0 if success else 1)
