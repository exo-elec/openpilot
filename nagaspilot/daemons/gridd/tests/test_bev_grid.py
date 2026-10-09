"""One BEV layout end to end: gridd stamps and publishes, pathd decodes.

Rows forward, cols left positive (like every yRel); pathd queries with its
path-frame lateral (modelV2, y right) and flips once (CLAUDE.md "Frame
conventions", as sunnypilot/comma).
"""
from types import SimpleNamespace

import numpy as np

import cereal.messaging as messaging
from openpilot.selfdrive.gridd.fusion_costmap import (
    COST_OBSTACLE, COST_ROAD, COST_UNCERTAIN, COST_UNKNOWN, FusionCostmap)
from openpilot.selfdrive.gridd.gridd import GridD, fill_grid_objects
from openpilot.selfdrive.gridd.lazy_bev import BEV_GRID, LazyBEV
from openpilot.selfdrive.pathd.pathd import OccupancyGridView


def test_grid_extent_is_the_decided_one():
    assert (BEV_GRID.origin_forward, BEV_GRID.forward_max) == (-20.0, 55.0)
    assert (BEV_GRID.origin_left, BEV_GRID.origin_left + BEV_GRID.cols * BEV_GRID.resolution) == (-15.0, 15.0)


def test_stamp_reads_back_where_it_was_put():
    cm = FusionCostmap()
    cm.add_obstacle(-8.0, 3.0, 1.0, 1.0)  # behind and to the left
    assert cm.get_cost_at(-8.0, 3.0) == COST_OBSTACLE
    assert cm.get_cost_at(-8.0, -3.0) == COST_UNKNOWN   # not mirrored
    assert cm.get_cost_at(3.0, -8.0) == COST_UNKNOWN    # not axis-swapped


def test_drivable_layer_never_exceeds_unknown():
    cm = FusionCostmap()
    cm.apply_drivable(np.full((BEV_GRID.rows, BEV_GRID.cols), COST_ROAD, dtype=np.uint16))
    cm.add_obstacle(10.0, 0.0, 1.0, 1.0)
    assert cm.get_cost_at(20.0, 0.0) == COST_ROAD
    assert cm.get_cost_at(10.0, 0.0) == COST_OBSTACLE


def test_lazy_bev_puts_a_left_stereo_point_on_the_left():
    bev = LazyBEV()
    bev.update_from_points(np.array([[-1.0, 0.0, 10.0]] * 5, dtype=np.float32))  # stereo X right: -1 = left
    assert bev.get_cell_probability(10.0, 1.0) > 0.9
    assert bev.get_cell_probability(10.0, -1.0) == 0.5


def test_surfaced_normal_road_is_road_not_obstacle():
    data = np.zeros((20, 21), dtype=np.uint8)
    data[:, :] = 2          # surfaced DRIVABLE_NORMAL
    data[10, 5] = 3         # rough
    data[4, 15] = 255       # obstacle
    da = SimpleNamespace(height=20, width=21, resolution=1.0, originX=-10.5, originY=0.0, data=data.tobytes())
    cost, obstacle = GridD._drivable_area_to_costmap(GridD.__new__(GridD), da)
    r, c, _ = BEV_GRID.cells(10.5, -5.0)  # surfaced row 10, col 5: 10.5 m ahead, 5 m right
    assert cost[r, c] == COST_UNCERTAIN
    r, c, _ = BEV_GRID.cells(4.5, 5.0)
    assert obstacle[r, c]
    r, c, _ = BEV_GRID.cells(15.5, 0.0)
    assert cost[r, c] == COST_ROAD and not obstacle[r, c]


def test_pathd_decodes_what_gridd_publishes():
    cm = FusionCostmap()
    cm.add_obstacle(20.0, 3.0, 1.0, 1.0)  # 20 m ahead, 3 m LEFT
    occupancy = np.full((BEV_GRID.rows, BEV_GRID.cols), 0.5, dtype=np.float32)
    msg = messaging.new_message('gridObjects')
    fill_grid_objects(msg.gridObjects, 0, occupancy, cm.costmap)
    view = OccupancyGridView.from_message(msg.gridObjects)

    # pathd asks in its path frame: left is negative
    assert view.cost_at(20.0, -3.0) == COST_OBSTACLE
    assert view.score_at(20.0, -3.0) == 1.0
    assert view.cost_at(20.0, 3.0) == COST_UNKNOWN
    assert view.score_at(20.0, 3.0) < 0.6   # unknown never reads as an obstacle


def test_drivable_layer_round_trips_to_the_off_road_filter():
    from openpilot.selfdrive.controls.lib.object_drivable_area_filter import ObjectDrivableAreaFilter
    p = np.full((BEV_GRID.rows, BEV_GRID.cols), 0.9, dtype=np.float32)
    r, c, _ = BEV_GRID.cells(np.array([10.0]), np.array([8.0]))     # 10 m ahead, 8 m LEFT: not road
    p[max(int(r[0]) - 6, 0):int(r[0]) + 6, max(int(c[0]) - 3, 0):int(c[0]) + 3] = 0.05
    msg = messaging.new_message('gridObjects')
    fill_grid_objects(msg.gridObjects, 0, np.full_like(p, 0.5), FusionCostmap().costmap, p)
    assert [layer.name for layer in msg.gridObjects.layers] == ['occupancy', 'cost', 'drivable']
    f = ObjectDrivableAreaFilter()
    f.update_grid(msg.gridObjects, 0.0)
    assert f.off_road({'dRel': 10.0, 'yRel': 8.0}) and not f.off_road({'dRel': 10.0, 'yRel': -8.0})
    # pathd still finds the layers it reads by name
    view = OccupancyGridView.from_message(msg.gridObjects)
    assert view.cost_at(10.0, 0.0) == COST_UNKNOWN
