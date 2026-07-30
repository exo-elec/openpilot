from dataclasses import dataclass
import math

from openpilot.selfdrive.controls.radar4d_tracker import (
    ABGTrackManager, KalmanTrackManager, TrackManager,
    CONFIRM_HITS, DROP_MISSES, EKF_COAST_MAX_S,
    EXISTENCE_TPR, EXISTENCE_FPR, EXISTENCE_HALF_LIFE_S, EXISTENCE_EXPIRE,
)


@dataclass
class _Det:
    """RadarDetection-shaped stand-in — avoids a hard dependency on hal for this test."""
    range_m: float
    vel_mps: float
    azimuth_deg: float
    elevation_deg: float
    snr_db: float
    is_static: bool = False


def _det(range_m=5.0, azimuth_deg=0.0, vel_mps=-1.0, is_static=False, elevation_deg=0.0):
    return _Det(range_m=range_m, vel_mps=vel_mps, azimuth_deg=azimuth_deg, elevation_deg=elevation_deg, snr_db=20.0, is_static=is_static)


class TestTrackerAlias:
    def test_track_manager_defaults_to_kalman(self):
        assert TrackManager is KalmanTrackManager


class _TrackerSuite:
    """Shared behavioural tests for any tracker that follows the same lifecycle."""
    MANAGER = None

    def _mgr(self):
        return self.MANAGER()

    def test_track_not_published_until_confirmed(self):
        mgr = self._mgr()
        for i in range(CONFIRM_HITS - 1):
            confirmed = mgr.update([_det()])
            assert confirmed == [], f"frame {i}: should still be tentative"
        confirmed = mgr.update([_det()])
        assert len(confirmed) == 1
        assert confirmed[0].hit_streak == CONFIRM_HITS

    def test_track_id_stable_across_confirmed_lifetime(self):
        mgr = self._mgr()
        for _ in range(CONFIRM_HITS):
            confirmed = mgr.update([_det()])
        track_id = confirmed[0].track_id
        for _ in range(5):
            confirmed = mgr.update([_det()])
            assert confirmed[0].track_id == track_id

    def test_intermittent_dropout_does_not_reset_below_drop_threshold(self):
        mgr = self._mgr()
        for _ in range(CONFIRM_HITS):
            confirmed = mgr.update([_det()])
        track_id = confirmed[0].track_id

        mgr.update([])  # one miss, below DROP_MISSES
        confirmed = mgr.update([_det()])
        assert len(confirmed) == 1
        assert confirmed[0].track_id == track_id

    def test_noise_spike_does_not_immediately_confirm(self):
        mgr = self._mgr()
        confirmed = mgr.update([_det()])
        assert confirmed == []
        confirmed = mgr.update([])
        assert confirmed == []

    def test_existence_prob_scales_with_hit_streak(self):
        mgr = self._mgr()
        confirmed: list = []
        for i in range(CONFIRM_HITS + 2):
            confirmed = mgr.update([_det()])
        # ABG steps to a flat 100; the Kalman Bayes update converges to 99.9.
        assert confirmed[0].existence_prob >= 99.0

    def test_existence_prob_decays_on_misses(self):
        mgr = self._mgr()
        for _ in range(CONFIRM_HITS):
            mgr.update([_det()])
        # One miss should not kill a confirmed track, but should decay existence.
        confirmed = mgr.update([])
        assert len(confirmed) == 1
        assert confirmed[0].existence_prob < 100.0
        assert confirmed[0].existence_prob > 0.0

    def test_dyn_prop_classifies_moving_and_stopped(self):
        mgr = self._mgr()
        for _ in range(CONFIRM_HITS):
            confirmed = mgr.update([_det(vel_mps=-2.0)])
        assert confirmed[0].dyn_prop == "moving"

        # After the track drops below threshold it reports STOPPED, not STATIONARY.
        # The Kalman tracker publishes filtered velocity, so allow its estimate
        # a few frames to settle below the threshold (the ABG tracker, which
        # publishes raw Doppler, settles immediately and stays stopped).
        for _ in range(8):
            confirmed = mgr.update([_det(vel_mps=0.0)])
        assert confirmed[0].dyn_prop == "stopped"

    def test_far_detection_does_not_associate_to_existing_track(self):
        mgr = self._mgr()
        for _ in range(CONFIRM_HITS):
            mgr.update([_det(range_m=5.0, vel_mps=0.0)])
        # A detection far away (beyond any class gate) should spawn a new
        # tentative track, not steal the confirmed track's identity.
        confirmed = mgr.update([_det(range_m=50.0, vel_mps=0.0)])
        assert len(confirmed) == 1
        assert abs(confirmed[0].range_m - 5.0) < 0.5

    def test_static_label_preserved_in_track(self):
        mgr = self._mgr()
        for _ in range(CONFIRM_HITS):
            confirmed = mgr.update([_det(is_static=True)])
        assert confirmed[0].is_static is True

        # A later dynamic match flips the label.
        confirmed = mgr.update([_det(is_static=False)])
        assert confirmed[0].is_static is False

    def test_cartesian_state_tracks_forward_position(self):
        mgr = self._mgr()
        for _ in range(CONFIRM_HITS):
            confirmed = mgr.update([_det(range_m=10.0, azimuth_deg=0.0)])
        t = confirmed[0]
        assert abs(t.x - 10.0) < 0.1
        assert abs(t.y) < 0.1

    def test_cartesian_velocity_smoothes_across_frames(self):
        """A target moving toward ego at ~-5 m/s should build a longitudinal
        velocity estimate instead of relying on raw Doppler only.  The sequence
        is made physically consistent: range decreases by ~0.25 m each frame."""
        mgr = self._mgr()
        r = 15.0
        for _ in range(CONFIRM_HITS + 10):
            confirmed = mgr.update([_det(range_m=r, vel_mps=-5.0)])
            r -= 0.25
        t = confirmed[0]
        # Kalman / alpha-beta + Doppler fusion should converge toward -5 m/s.
        assert t.vx < -3.0
        assert t.aRel == t.ax  # alias property works

    def test_acceleration_estimated_for_braking_target(self):
        """A target that rapidly closes distance should produce a negative
        longitudinal acceleration estimate during the velocity transition —
        useful for smooth braking.  After the filter converges to the new
        constant velocity, ax naturally decays back toward zero."""
        mgr = self._mgr()
        # Confirm the track first: closing at -2 m/s from 15 m.
        r = 15.0
        for _ in range(CONFIRM_HITS):
            mgr.update([_det(range_m=r, vel_mps=-2.0)])
            r -= 0.10
        # Now lead brakes: closes at -8 m/s.
        min_ax = 0.0
        for _ in range(10):
            confirmed = mgr.update([_det(range_m=r, vel_mps=-8.0)])
            r -= 0.40
            min_ax = min(min_ax, confirmed[0].ax)
        # Acceleration should become strongly negative as the tracker reacts
        # to the step change in closing speed.
        assert min_ax < -1.0

    def test_lateral_separation_prevents_id_switch(self):
        """Two detections at the same range but different azimuth should keep
        separate tracks in dense traffic."""
        mgr = self._mgr()
        for _ in range(CONFIRM_HITS):
            confirmed = mgr.update([
                _det(range_m=8.0, azimuth_deg=-10.0),
                _det(range_m=8.0, azimuth_deg=+10.0),
            ])
        assert len(confirmed) == 2
        assert confirmed[0].y * confirmed[1].y < 0  # one left, one right


class TestABGTrackManager(_TrackerSuite):
    """Regression tests for the alpha-beta-gamma tracker."""
    MANAGER = ABGTrackManager

    def test_track_dropped_after_miss_streak(self):
        mgr = self._mgr()
        for _ in range(CONFIRM_HITS):
            mgr.update([_det()])
        assert len(mgr._tracks) == 1

        for _ in range(DROP_MISSES):
            mgr.update([])  # no detections this frame
        assert len(mgr._tracks) == 0


class TestKalmanTrackManager(_TrackerSuite):
    """Tests for the EKF tracker with occlusion handling."""
    MANAGER = KalmanTrackManager

    def test_confirmed_track_coasts_through_occlusion(self):
        """A confirmed track should coast up to EKF_COAST_MAX_S without detection."""
        mgr = self._mgr()
        for _ in range(CONFIRM_HITS):
            mgr.update([_det()])
        assert len(mgr._tracks) == 1

        # Coast for the full occlusion window (wall-clock, not frame count).
        coast_frames = int(EKF_COAST_MAX_S / mgr.dt_s)
        for _ in range(coast_frames):
            mgr.update([])
        assert len(mgr._tracks) == 1

        # One more miss exceeds the limit and drops the track.
        mgr.update([])
        assert len(mgr._tracks) == 0

    def test_existence_bayes_update_on_hit(self):
        """A hit must apply the Autoware Bayesian existence update:
        p' = p*TPR / (p*TPR + (1-p)*FPR) in normalized 0-1, stored 0-100."""
        mgr = self._mgr()
        mgr.update([_det()])  # spawn at EXISTENCE_SPAWN
        tr = list(mgr._tracks.values())[0]
        q0 = tr.existence_prob / 100.0
        mgr.update([_det()])
        expected = 100.0 * q0 * EXISTENCE_TPR / (q0 * EXISTENCE_TPR + (1.0 - q0) * EXISTENCE_FPR)
        assert abs(tr.existence_prob - expected) < 1e-6

    def test_existence_hit_converges_not_saturates(self):
        """Repeated hits converge toward 99.9 with diminishing steps."""
        mgr = self._mgr()
        for _ in range(CONFIRM_HITS + 6):
            confirmed = mgr.update([_det()])
        assert 99.0 < confirmed[0].existence_prob <= 99.9

    def test_existence_miss_decays_with_half_life(self):
        """A miss of exactly one half-life should halve existence probability."""
        mgr = self._mgr()
        for _ in range(CONFIRM_HITS):
            mgr.update([_det()])
        tr = list(mgr._tracks.values())[0]
        p0 = tr.existence_prob
        mgr.update([], dt_s=EXISTENCE_HALF_LIFE_S)
        assert abs(tr.existence_prob - p0 * 0.5) < 1e-6

    def test_low_existence_confirmed_track_expires(self):
        """A confirmed track whose existence collapses expires before the
        wall-clock coast limit (Autoware: remove at p < 0.015)."""
        mgr = self._mgr()
        for _ in range(CONFIRM_HITS):
            mgr.update([_det()])
        tr = list(mgr._tracks.values())[0]
        tr.existence_prob = EXISTENCE_EXPIRE + 0.1
        mgr.update([])  # half-life decay pushes it below the expire threshold
        assert len(mgr._tracks) == 0

    def test_occluded_track_reacquires_with_wider_gate(self):
        """After a few misses, a slightly displaced reappearance should still
        associate to the original track instead of spawning a new one."""
        mgr = self._mgr()
        for _ in range(CONFIRM_HITS):
            confirmed = mgr.update([_det(range_m=10.0, azimuth_deg=0.0)])
        track_id = confirmed[0].track_id

        # Miss a few frames (e.g. occlusion by a large vehicle).
        for _ in range(3):
            mgr.update([])

        # Reappear slightly ahead — should re-associate, not spawn.
        confirmed = mgr.update([_det(range_m=10.5, azimuth_deg=1.0)])
        assert len(confirmed) == 1
        assert confirmed[0].track_id == track_id

    def test_tentative_track_drops_fast(self):
        """Tentative tracks do not get the occlusion coast window."""
        mgr = self._mgr()
        mgr.update([_det()])  # 1 hit, still tentative
        assert len(mgr._tracks) == 1
        for _ in range(DROP_MISSES):
            mgr.update([])
        assert len(mgr._tracks) == 0

    def test_shape_metadata_smoothed_across_frames(self):
        """Per-frame cluster dimensions are EMA-smoothed, not replaced:
        one bad frame with a doubled length barely moves the estimate."""
        mgr = self._mgr()
        meta = {"length_m": 4.0, "width_m": 2.0, "height_m": 1.5,
                "yaw_rad": 0.1, "point_count": 5}
        for _ in range(CONFIRM_HITS):
            mgr.update([_det()], [meta])
        tr = list(mgr._tracks.values())[0]
        assert abs(tr.metadata["length_m"] - 4.0) < 0.1
        mgr.update([_det()], [dict(meta, length_m=8.0)])
        assert tr.metadata["length_m"] < 5.0  # jump gain, not wholesale replace

    def test_shape_yaw_pi_flip_guard(self):
        """A 180-degree-flipped L-shape yaw must not swing the tracked yaw."""
        mgr = self._mgr()
        meta = {"length_m": 4.0, "width_m": 2.0, "height_m": 1.5,
                "yaw_rad": 0.2, "point_count": 5}
        for _ in range(CONFIRM_HITS):
            mgr.update([_det()], [meta])
        tr = list(mgr._tracks.values())[0]
        yaw_before = tr.metadata["yaw_rad"]
        mgr.update([_det()], [dict(meta, yaw_rad=0.2 + math.pi)])
        assert abs(tr.metadata["yaw_rad"] - yaw_before) < 0.1

    def test_published_vrel_is_filtered_not_raw(self):
        """Published vel_mps should come from the EKF state, smoothing raw
        Doppler quantization noise.  Feed a physically consistent closing
        target (range shrinks at the mean Doppler rate) whose raw Doppler
        alternates +/-1 m/s; the published vRel swing must stay well below
        the raw 2 m/s swing."""
        mgr = self._mgr()
        r = 15.0
        published = []
        for i in range(14):
            v_raw = -4.0 if i % 2 == 0 else -2.0   # mean -3 m/s, raw swing 2 m/s
            confirmed = mgr.update([_det(range_m=r, vel_mps=v_raw)])
            r -= 0.15                              # consistent with -3 m/s at 20 Hz
            if confirmed:
                published.append(confirmed[0].vel_mps)
        tail = published[-6:]
        assert len(tail) == 6
        assert max(tail) - min(tail) < 1.0        # filtered swing << raw 2 m/s swing
        assert all(v < 0.0 for v in tail)         # sign convention preserved

    def test_dt_override_scales_prediction(self):
        """A measured frame period must scale the prediction step: with a
        dropped frame (dt_s = 0.2 s), a coasting closing target advances
        roughly 4x further than with the nominal 0.05 s."""
        def _confirmed_mgr():
            mgr = self._mgr()
            r = 15.0
            for _ in range(CONFIRM_HITS + 2):
                mgr.update([_det(range_m=r, vel_mps=-5.0)])
                r -= 0.25
            return mgr

        mgr_nominal = _confirmed_mgr()
        x_before = list(mgr_nominal._tracks.values())[0].x
        x_nominal = mgr_nominal.update([])[0].x

        mgr_slow = _confirmed_mgr()
        x_slow = mgr_slow.update([], dt_s=0.2)[0].x

        assert x_before - x_slow > 3.0 * (x_before - x_nominal)

    def test_elevation_smoothed_across_frames(self):
        """The z/elevation complementary filter should keep published
        elevation steadier than the raw alternating AoA input."""
        mgr = self._mgr()
        published = []
        for i in range(10):
            el = 10.0 if i % 2 == 0 else 5.0
            confirmed = mgr.update([_det(range_m=8.0, elevation_deg=el, vel_mps=0.0)])
            if confirmed:
                published.append(confirmed[0].elevation_deg)
        tail = published[-4:]
        assert max(tail) - min(tail) < 5.0        # filtered swing < raw 5 deg swing

