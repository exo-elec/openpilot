#!/usr/bin/env python3
"""Unit tests for side-camera tracking stack."""

import pytest

from openpilot.selfdrive.sided.simple_tracker import SimpleTracker, SideObject, _iou
from openpilot.selfdrive.sided.bev_reprojector import (
  make_default_geometry, reproject_side_camera,
)
from openpilot.selfdrive.sided.handover_manager import HandoverManager


class TestIoU:
  def test_identical_boxes(self):
    bbox = (10.0, 10.0, 50.0, 50.0)
    assert _iou(bbox, bbox) == pytest.approx(1.0)

  def test_no_overlap(self):
    a = (0.0, 0.0, 10.0, 10.0)
    b = (20.0, 20.0, 30.0, 30.0)
    assert _iou(a, b) == 0.0

  def test_partial_overlap(self):
    a = (0.0, 0.0, 20.0, 20.0)
    b = (10.0, 10.0, 30.0, 30.0)
    inter = 10.0 * 10.0
    union = 400.0 + 400.0 - inter
    assert _iou(a, b) == pytest.approx(inter / union)


class TestSimpleTracker:
  def test_new_detection_gets_uid(self):
    tracker = SimpleTracker()
    dets = [SideObject(label='car', confidence=0.9, bbox_2d=(10, 10, 50, 50))]
    tracks = tracker.update(dets)
    assert len(tracks) == 1
    assert tracks[0].uid == 1

  def test_same_detection_persists(self):
    tracker = SimpleTracker()
    dets = [SideObject(label='car', confidence=0.9, bbox_2d=(10, 10, 50, 50))]
    tracks1 = tracker.update(dets)
    tracks2 = tracker.update(dets)
    assert tracks2[0].uid == tracks1[0].uid

  def test_detection_moves_gets_new_uid(self):
    tracker = SimpleTracker()
    dets1 = [SideObject(label='car', confidence=0.9, bbox_2d=(10, 10, 50, 50))]
    tracker.update(dets1)
    dets2 = [SideObject(label='car', confidence=0.9, bbox_2d=(200, 200, 250, 250))]
    tracks2 = tracker.update(dets2)
    assert tracks2[0].uid == 2

  def test_empty_input_ages_tracks(self):
    tracker = SimpleTracker()
    dets = [SideObject(label='car', confidence=0.9, bbox_2d=(10, 10, 50, 50))]
    tracker.update(dets)
    for _ in range(4):
      tracker.update([])
    assert len(tracker._tracks) == 0

  def test_velocity_ema(self):
    tracker = SimpleTracker()
    dets1 = [SideObject(label='car', confidence=0.9, bbox_2d=(10, 10, 50, 50), distance_m=10.0)]
    tracker.update(dets1)
    dets2 = [SideObject(label='car', confidence=0.9, bbox_2d=(12, 12, 52, 52), distance_m=8.0)]
    tracks2 = tracker.update(dets2)
    # Decreasing absolute range means approaching (negative range rate).
    assert tracks2[0].velocity_mps == pytest.approx(-0.8)

  def test_reset(self):
    tracker = SimpleTracker()
    tracker.update([SideObject(label='car', confidence=0.9, bbox_2d=(10, 10, 50, 50))])
    tracker.reset()
    assert len(tracker._tracks) == 0
    assert tracker._next_uid == 1


class TestBEVReprojector:
  def test_default_geometry_left(self):
    geo = make_default_geometry('side_left')
    assert geo.cam_y_m > 0
    assert geo.fx > 0

  def test_default_geometry_right(self):
    geo = make_default_geometry('side_right')
    assert geo.cam_y_m < 0
    assert geo.fx > 0

  def test_reprojection_returns_sensible_values(self):
    geo = make_default_geometry('side_left', img_w=1280.0, img_h=720.0)
    bbox = (500.0, 300.0, 700.0, 600.0)  # bottom-centre near bottom of image
    x, y, z, w, l = reproject_side_camera(bbox, 'car', (720, 1280), geo)
    assert w == pytest.approx(1.8)
    assert l == pytest.approx(4.5)
    assert z <= geo.cam_z_m  # ground plane intersection

  def test_class_widths(self):
    from openpilot.selfdrive.sided.bev_reprojector import CLASS_WIDTHS_M, CLASS_LENGTHS_M
    assert CLASS_WIDTHS_M['person'] < CLASS_WIDTHS_M['truck']
    assert CLASS_LENGTHS_M['bus'] > CLASS_LENGTHS_M['car']


class TestHandoverManager:
  def test_single_camera_tracks_passthrough(self):
    hm = HandoverManager()
    obj = SideObject(uid=1, label='car', confidence=0.9, distance_m=5.0, lateral_m=1.0)
    result = hm.update({'side_left': [obj]})
    assert len(result) == 1
    assert result[0].uid == 1

  def test_cross_camera_merge(self):
    hm = HandoverManager()
    obj_left = SideObject(uid=1, label='car', confidence=0.9, distance_m=5.0, lateral_m=1.0)
    hm.update({'side_left': [obj_left]})
    obj_right = SideObject(uid=1, label='car', confidence=0.9, distance_m=5.1, lateral_m=0.9)
    result = hm.update({'side_right': [obj_right]})
    assert len(result) == 1
    assert result[0].uid == 1  # same global UID

  def test_cross_camera_new_object(self):
    hm = HandoverManager()
    obj_left = SideObject(uid=1, label='car', confidence=0.9, distance_m=5.0, lateral_m=1.0)
    hm.update({'side_left': [obj_left]})
    obj_right = SideObject(uid=1, label='car', confidence=0.9, distance_m=20.0, lateral_m=-5.0)
    result = hm.update({'side_right': [obj_right]})
    assert len(result) == 2  # far apart → new track

  def test_coasting(self):
    hm = HandoverManager(coast_max_age=2)
    obj = SideObject(uid=1, label='car', confidence=0.9, distance_m=5.0, lateral_m=1.0)
    hm.update({'side_left': [obj]})
    hm.update({})
    hm.update({})
    result = hm.update({})
    assert len(result) == 1  # still coasting
    result = hm.update({})
    assert len(result) == 0  # aged out

  def test_low_confidence_filtered(self):
    hm = HandoverManager()
    obj = SideObject(uid=1, label='car', confidence=0.1, distance_m=5.0, lateral_m=1.0)
    result = hm.update({'side_left': [obj]})
    assert len(result) == 0

  def test_reset(self):
    hm = HandoverManager()
    hm.update({'side_left': [SideObject(label='car', confidence=0.9, distance_m=5.0, lateral_m=1.0)]})
    hm.reset()
    assert len(hm._tracks) == 0
