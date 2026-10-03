"""DrivableFusion: per-camera maps fused once on arrival, decaying to unknown,
moving with the car (after Autoware's log-odds fusion + LOBF updater)."""
import numpy as np

from openpilot.selfdrive.gridd.drivable_fusion import (
    DEGRADED_AFTER_S, DRIVABLE, LANE, OTHER, UNKNOWN, TAU_S, DrivableFusion)
from openpilot.selfdrive.gridd.fusion_costmap import COST_LANE, COST_ROAD, COST_UNCERTAIN, COST_UNKNOWN
from openpilot.selfdrive.gridd.lazy_bev import BEV_GRID


def _map(value=UNKNOWN):
    return np.full((BEV_GRID.rows, BEV_GRID.cols), value, dtype=np.uint8)


def _cell(forward, left):
    r, c, _ = BEV_GRID.cells(forward, left)
    return int(r), int(c)


def test_one_road_frame_is_confident_road_and_unseen_stays_unknown():
    f = DrivableFusion()
    cells = _map()
    cells[_cell(20.0, 0.0)] = DRIVABLE
    f.step(0.0)
    f.update('road', cells, 0.0)
    cost = f.cost()
    assert cost[_cell(20.0, 0.0)] <= COST_UNCERTAIN
    assert cost[_cell(20.0, 5.0)] == COST_UNKNOWN


def test_not_road_never_lowers_cost_and_never_exceeds_unknown():
    f = DrivableFusion()
    cells = _map(OTHER)
    f.update('road', cells, 0.0)
    assert (f.cost() == COST_UNKNOWN).all()   # segmentation alone: at most unknown


def test_road_camera_outweighs_a_side_camera_and_lane_marks_show():
    f = DrivableFusion()
    road, side = _map(), _map()
    road[_cell(10.0, 0.0)] = LANE
    side[_cell(10.0, 0.0)] = OTHER
    for _ in range(3):
        f.update('road', road, 0.0)
    f.update('side_left', side, 0.0)
    assert f.cost()[_cell(10.0, 0.0)] == COST_LANE
    assert f.probability()[_cell(10.0, 0.0)] > 0.9


def test_a_camera_that_stops_fades_to_unknown():
    f = DrivableFusion()
    cells = _map()
    cells[_cell(10.0, 0.0)] = DRIVABLE
    f.step(0.0)
    for _ in range(5):
        f.update('road', cells, 0.0)
    assert f.cost()[_cell(10.0, 0.0)] == COST_ROAD
    f.step(6 * TAU_S)
    assert f.cost()[_cell(10.0, 0.0)] == COST_UNKNOWN   # not stale road


def test_content_moves_back_with_the_car_and_reverse_moves_it_forward():
    f = DrivableFusion()
    cells = _map()
    cells[_cell(20.0, 0.0)] = DRIVABLE
    f.step(0.0)
    for _ in range(4):
        f.update('road', cells, 0.0)
    f.step(0.001, v_forward=0.0)
    f.step(0.001 + 0.5, v_forward=10.0)      # 5 m travelled, decay small
    p = f.probability()
    assert p[_cell(15.0, 0.0)] > p[_cell(20.0, 0.0)]
    f.step(1.001, v_forward=-10.0)           # reversed 5 m: back (to within the cell carry)
    assert f.probability()[_cell(19.5, 0.0)] > f.probability()[_cell(15.0, 0.0)]


def test_fractional_ego_travel_accumulates():
    f = DrivableFusion()
    cells = _map()
    cells[_cell(20.0, 0.0)] = DRIVABLE
    f.step(0.0)
    f.update('road', cells, 0.0)
    for i in range(1, 13):                   # 12 x 0.1 m = 1.2 m: two whole 0.5 m cells
        f.step(i * 0.01, v_forward=10.0)
    r, c = _cell(20.0, 0.0)
    assert f.probability()[r - 2, c] > 0.5 and f.probability()[r, c] == 0.5


def test_evidence_and_degradation_timing():
    f = DrivableFusion()
    assert not f.has_evidence(0.0)           # nothing yet: degraded
    f.update('road', _map(), 1.0)
    assert f.has_evidence(1.0 + DEGRADED_AFTER_S - 0.1)
    assert not f.has_evidence(1.0 + DEGRADED_AFTER_S + 0.1)   # segd silent: degraded, not a fault


def test_wrong_shape_is_ignored():
    f = DrivableFusion()
    f.update('road', np.zeros((3, 3), dtype=np.uint8), 0.0)
    assert not f.has_evidence(0.0)


def _bev_msg(camera, cells, **overrides):
    import cereal.messaging as messaging
    msg = messaging.new_message('drivableBev')
    d = msg.drivableBev
    d.resolution, d.width, d.height = BEV_GRID.resolution, BEV_GRID.cols, BEV_GRID.rows
    d.originX, d.originY = BEV_GRID.origin_left, BEV_GRID.origin_forward
    for key, value in overrides.items():
        setattr(d, key, value)
    entry = d.init('cameras', 1)[0]
    entry.camera, entry.data = camera, cells.tobytes()
    return d


class _Host:
    """What GridD._fuse_drivable_bev reads."""
    def __init__(self, msg):
        self.sm = type('SM', (), {'updated': {'drivableBev': True}, '__getitem__': lambda s, k: msg})()
        self.drivable = DrivableFusion()


def test_gridd_fuses_segds_message_into_the_cost_layer():
    from openpilot.selfdrive.gridd.fusion_costmap import FusionCostmap
    from openpilot.selfdrive.gridd.gridd import GridD
    cells = _map()
    cells[_cell(20.0, 0.0)] = DRIVABLE
    host = _Host(_bev_msg('road', cells))
    GridD._fuse_drivable_bev(host)
    cm = FusionCostmap()
    cm.apply_drivable(host.drivable.cost())
    assert cm.get_cost_at(20.0, 0.0) < COST_UNKNOWN
    assert cm.get_cost_at(20.0, 6.0) == COST_UNKNOWN


def test_gridd_ignores_a_map_on_another_grid():
    from openpilot.selfdrive.gridd.gridd import GridD
    host = _Host(_bev_msg('road', _map(DRIVABLE), originY=-5.0))
    GridD._fuse_drivable_bev(host)
    assert not host.drivable.has_evidence(0.0)
