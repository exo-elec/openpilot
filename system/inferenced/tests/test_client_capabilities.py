#!/usr/bin/env python3
"""Tests for InferenceClient capability-discovery helpers."""

from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

from openpilot.system.inferenced.client import InferenceClient
from openpilot.system.inferenced.compute import BackendType


class _MockStatus:
    """Mock inferencedStatus message."""

    def __init__(self, backends=None, models=None):
        self.availableBackends = backends or []
        self.availableModels = models or []


class TestInferenceClientCapabilities(unittest.TestCase):

    def _make_client(self, status=None):
        """Create an IPC client whose status query returns a fixed value."""
        with patch('openpilot.system.inferenced.client._CEREAL_AVAILABLE', True):
            with patch('openpilot.system.inferenced.client.messaging') as mock_messaging:
                mock_messaging.PubMaster = lambda _: MagicMock()
                mock_messaging.SubMaster = lambda _: MagicMock()
                client = InferenceClient('testd', use_ipc=True)
                client._sm_status = MagicMock()
        # Patch the public status-query helper so no real IPC is needed.
        client._query_status = lambda timeout=0.2: status
        return client

    def test_get_available_backends_from_status(self):
        client = self._make_client(status=_MockStatus(backends=['NPU', 'EGPU']))
        self.assertEqual(sorted(client.get_available_backends()), ['EGPU', 'NPU'])

    def test_get_available_models_from_status(self):
        client = self._make_client(status=_MockStatus(models=['side_yolo_egpu', 'rear_yolo_egpu']))
        self.assertEqual(sorted(client.get_available_models()), ['rear_yolo_egpu', 'side_yolo_egpu'])

    def test_can_run_model_true_when_advertised(self):
        client = self._make_client(status=_MockStatus(models=['front_road_seg_egpu']))
        self.assertTrue(client.can_run_model('front_road_seg_egpu'))

    def test_can_run_model_false_when_not_advertised(self):
        client = self._make_client(status=_MockStatus(models=['side_yolo_egpu']))
        self.assertFalse(client.can_run_model('rear_yolo_egpu'))

    def test_wait_for_backend_succeeds_when_present(self):
        client = self._make_client(status=_MockStatus(backends=['EGPU']))
        self.assertTrue(client.wait_for_backend(BackendType.EGPU, timeout=0.1))

    def test_wait_for_backend_times_out_when_absent(self):
        client = self._make_client(status=_MockStatus(backends=['NPU']))
        start = time.monotonic()
        self.assertFalse(client.wait_for_backend(BackendType.EGPU, timeout=0.05))
        self.assertLess(time.monotonic() - start, 0.2)

    def test_direct_hal_fallback_when_status_unavailable(self):
        client = self._make_client(status=None)
        hal = MagicMock()
        hal.get_available_backends.return_value = [BackendType.NPU, BackendType.EGPU]
        hal._models_cache = {'side_yolo_egpu': MagicMock()}
        client._InferenceClient__hal = hal
        self.assertEqual(sorted(client.get_available_backends()), ['EGPU', 'NPU'])
        self.assertEqual(client.get_available_models(), ['side_yolo_egpu'])


if __name__ == '__main__':
    unittest.main()
