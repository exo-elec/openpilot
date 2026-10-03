#!/usr/bin/env python3
"""segd: card segmentation round-robin, summaries, and the rcd consumer."""

from __future__ import annotations

import sys
import unittest  # noqa: TID251
from types import SimpleNamespace
from unittest.mock import MagicMock, patch  # noqa: TID251

import cv2  # noqa: F401  (loaded before the stubbed imports below)
import numpy as np

from cereal import custom

from openpilot.selfdrive.segd.card_segmenter import DRIVABLE, LANE, OTHER, CardSegmenter, summarize
from openpilot.system.inferenced.compute import BackendType, InferenceResult

with patch.dict(sys.modules, {m: sys.modules.get(m, MagicMock()) for m in ('cereal.messaging', 'msgq', 'msgq.visionipc')}):
  from openpilot.selfdrive.segd.segd import decode_frame
  from openpilot.selfdrive.gridd import gridd


class TestCardSegmenter(unittest.TestCase):

  def _seg(self, result):
    client = MagicMock()
    client.submit_job.return_value = result
    return CardSegmenter('seg_road', 'segd', client=client), client

  def test_sends_accel_job_at_transport_size(self):
    seg, client = self._seg(InferenceResult(success=True, outputs={'output': np.zeros((540, 960), np.uint8)}))
    self.assertEqual(seg.segment(np.zeros((1080, 1920, 3), np.uint8)).shape, (540, 960))
    kw = client.submit_job.call_args.kwargs
    self.assertEqual(kw['backend_type'], BackendType.ACCEL)
    self.assertEqual(kw['input_array'].shape, (1, 540, 960, 3))
    self.assertFalse(kw['allow_direct_fallback'])

  def test_no_card_switches_off(self):
    seg, _ = self._seg(InferenceResult(success=False, error_message='Camera accelerator not available'))
    self.assertIsNone(seg.segment(np.zeros((64, 64, 3), np.uint8)))
    self.assertFalse(seg.is_available)

  def test_other_failure_keeps_trying(self):
    seg, _ = self._seg(InferenceResult(success=False, error_message='IPC timeout'))
    self.assertIsNone(seg.segment(np.zeros((64, 64, 3), np.uint8)))
    self.assertTrue(seg.is_available)


class TestSummaries(unittest.TestCase):

  def test_open_road(self):
    m = np.full((100, 100), OTHER, np.uint8)
    m[50:] = DRIVABLE
    m[50:, 40] = LANE   # lane lines are road surface
    self.assertEqual(summarize(m), (True, False, True))

  def test_road_with_its_edge_in_view(self):
    m = np.full((100, 100), OTHER, np.uint8)
    m[50:, 20:80] = DRIVABLE
    self.assertEqual(summarize(m), (True, True, True))

  def test_edge_without_drivable_ahead(self):
    m = np.full((100, 100), OTHER, np.uint8)
    m[50:, :20] = DRIVABLE   # road off to the side, nothing ahead
    has_road, has_edge, has_drivable = summarize(m)
    self.assertTrue(has_road and has_edge)
    self.assertFalse(has_drivable)

  def test_no_road(self):
    self.assertEqual(summarize(np.full((100, 100), OTHER, np.uint8)), (False, False, False))


class TestDecode(unittest.TestCase):

  def test_decode_bgr_and_nv12(self):
    bgr = SimpleNamespace(data=np.zeros(4 * 6 * 3, np.uint8).tobytes())
    self.assertEqual(decode_frame(bgr, 6, 4).shape, (4, 6, 3))
    nv12 = SimpleNamespace(data=np.zeros(4 * 6 * 3 // 2, np.uint8).tobytes())
    self.assertEqual(decode_frame(nv12, 6, 4).shape, (4, 6, 3))


class TestRcdReadsTheRoadEntry(unittest.TestCase):

  def test_road_segment_is_picked_from_the_list(self):
    from openpilot.selfdrive.controls.lib import rcd
    detector = rcd.RCD.__new__(rcd.RCD)
    detector.classifier = MagicMock()
    detector.classifier.speed_limits = {rcd.RoadCondition.DEBRIS: 10.0}
    segs = [SimpleNamespace(camera='side_left', hasRoad=True, hasEdge=False, hasDrivable=True),
            SimpleNamespace(camera='road', hasRoad=True, hasEdge=True, hasDrivable=False)]
    sm = {'monoSegments': SimpleNamespace(segments=segs)}
    sm_obj = MagicMock()
    sm_obj.valid = {'monoSegments': True}
    sm_obj.__getitem__ = lambda _self, k: sm[k]
    result = detector._update_from_segmentation(sm_obj)
    self.assertIsNotNone(result)
    self.assertEqual(result.condition, rcd.RoadCondition.DEBRIS)


class TestGriddDegradesWithoutTheCard(unittest.TestCase):
  """No RKNN substitute: no camera map from segd = degraded, reported, never a fault."""

  def test_gridd_has_no_card_client_of_its_own(self):
    """segd is the card's only segmentation client (one owner)."""
    self.assertFalse(hasattr(gridd, 'CardSegmenter'))
    self.assertFalse(hasattr(gridd.GridD, '_road_class_map'))

  def test_nothing_on_rknn_takes_over(self):
    self.assertFalse(hasattr(gridd, 'PPLiteSeg'))

  def test_degraded_is_its_own_status_field(self):
    status = custom.GridStatus.new_message(segmentationDegraded=True)
    self.assertTrue(status.segmentationDegraded)
    self.assertFalse(status.fault)


if __name__ == '__main__':
  unittest.main()
