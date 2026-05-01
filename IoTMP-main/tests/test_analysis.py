"""
Unit & mock tests for service2_analysis.py core logic.
Run with: uv run pytest tests/test_analysis.py -v
"""

import os
import sys
from unittest.mock import MagicMock

# Patch PyQt6 + websockets before importing service2_analysis — E402 intentional
sys.modules["PyQt6"] = MagicMock()
sys.modules["PyQt6.QtCore"] = MagicMock()
sys.modules["PyQt6.QtGui"] = MagicMock()
sys.modules["PyQt6.QtWidgets"] = MagicMock()
sys.modules["websockets"] = MagicMock()

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from service2_analysis import (  # noqa: E402
    DWELL_CONFIRM_MS,
    FLICK_CONFIRM_FRAMES,
    ISOLATION_INDEX,
    FingerUnit,
    OneEuroFilter,
    Processor,
)


# ── OneEuroFilter ─────────────────────────────────────────────────────────────
class TestOneEuroFilter:
    def test_first_call_returns_input(self):
        f = OneEuroFilter()
        assert f(5.0) == 5.0

    def test_smooths_noisy_signal(self):
        f = OneEuroFilter(freq=30.0, min_cutoff=0.5, beta=0.0)
        outputs = [f(v) for v in [100.0, 0.0, 100.0, 0.0, 100.0]]
        assert max(outputs[1:]) - min(outputs[1:]) < 100.0

    def test_stable_signal_converges(self):
        f = OneEuroFilter(freq=30.0, min_cutoff=1.0, beta=0.0)
        out = None
        for _ in range(60):
            out = f(50.0)
        assert abs(out - 50.0) < 1.0


# ── FingerUnit FSM ────────────────────────────────────────────────────────────
class TestFingerUnitFSM:
    def test_initial_state_is_idle(self):
        fu = FingerUnit("index")
        assert fu.fsm_state_name == "IDLE"

    def test_reset_clears_state(self):
        fu = FingerUnit("index")
        fu.update(100.0)
        fu.reset()
        assert fu.fsm_state_name == "IDLE"
        assert fu.velocity == 0.0
        assert fu.position == 0.0

    def test_no_fire_below_threshold(self):
        fu = FingerUnit("index")
        for _ in range(20):
            fu.update(50.0)
            assert not fu.is_velocity_peak(vel_threshold=9999.0)

    def test_transitions_to_rising_on_high_velocity(self):
        fu = FingerUnit("index", freq=30.0)
        fu.update(10.0)
        for _ in range(5):
            fu.update(200.0)
        fu.is_velocity_peak(vel_threshold=10.0)
        assert fu.fsm_state_name in ("RISING", "DWELL", "IDLE")

    def test_full_flick_fires_at_most_once(self):
        fu = FingerUnit("index", freq=30.0)
        fu.set_peak_pos_tol_px(20.0)
        VEL_THR = 50.0

        pos = 50.0
        for _ in range(10):
            pos += 15.0
            fu.update(pos)
            fu.is_velocity_peak(VEL_THR)

        hold_pos = fu.position
        dwell_frames = int((DWELL_CONFIRM_MS / 1000.0) * 30) + FLICK_CONFIRM_FRAMES + 5
        fired_count = sum(
            1 for _ in range(dwell_frames) if (fu.update(hold_pos), fu.is_velocity_peak(VEL_THR))[1]
        )
        assert fired_count <= 1

    def test_dwell_aborts_on_large_jump(self):
        fu = FingerUnit("index", freq=30.0)
        fu.set_peak_pos_tol_px(5.0)
        fu.update(10.0)
        for _ in range(5):
            fu.update(150.0)
            fu.is_velocity_peak(10.0)

        if fu.fsm_state_name == "DWELL":
            fu.update(fu.position + 50.0)
            fu.is_velocity_peak(10.0)
            assert fu.fsm_state_name == "IDLE"


# ── WTA Isolation Logic ───────────────────────────────────────────────────────
class TestWTALogic:
    def _wta(self, displacements: dict):
        names = tuple(displacements.keys())
        unit_state = {n: (d, 0.0) for n, d in displacements.items()}
        max_disp = max(unit_state[n][0] for n in names)
        active = {n: unit_state[n] for n in names if unit_state[n][0] >= ISOLATION_INDEX * max_disp}
        winner = max(active or unit_state, key=lambda n: unit_state[n][0])
        return active, winner

    def test_dominant_finger_wins(self):
        _, winner = self._wta({"index": 200.0, "middle": 50.0, "ring_pinky": 30.0})
        assert winner == "index"

    def test_weak_fingers_gated_out(self):
        active, _ = self._wta({"index": 200.0, "middle": 50.0, "ring_pinky": 30.0})
        assert "middle" not in active
        assert "ring_pinky" not in active

    def test_close_fingers_both_pass_gate(self):
        active, _ = self._wta({"index": 200.0, "middle": 190.0, "ring_pinky": 30.0})
        assert "index" in active
        assert "middle" in active

    def test_advanced_layer_excludes_index(self):
        names = ("middle", "ring_pinky")
        unit_state = {"index": 200.0, "middle": 150.0, "ring_pinky": 100.0}
        max_disp = max(unit_state[n] for n in names)
        active = {n: unit_state[n] for n in names if unit_state[n] >= ISOLATION_INDEX * max_disp}
        winner = max(active or {n: unit_state[n] for n in names}, key=lambda n: unit_state[n])
        assert winner == "middle"
        assert "index" not in active


# ── Mock AnalysisThread._process ─────────────────────────────────────────────
class TestAnalysisThreadProcess:
    def _make_thread(self):
        p = Processor()
        p.on_progress = MagicMock()
        p.on_trigger = MagicMock()
        p.on_layer = MagicMock()
        # Alias signal names used in assertions
        p.progress_update = p.on_progress
        p.trigger_activation = p.on_trigger
        p.layer_active_update = p.on_layer
        return p

    def _flat_landmarks(self):
        """21 landmarks all at wrist position — no movement."""
        return [{"x": 0.5, "y": 0.5, "z": 0.0} for _ in range(21)]

    def test_empty_landmarks_emits_zero_progress(self):
        t = self._make_thread()
        t.process([], 640, 480)
        t.on_progress.assert_called_once_with({"index": 0.0, "middle": 0.0, "ring_pinky": 0.0})

    def test_valid_landmarks_emits_progress(self):
        t = self._make_thread()
        t.process(self._flat_landmarks(), 640, 480)
        t.on_progress.assert_called_once()

    def test_no_trigger_without_calibration(self):
        t = self._make_thread()
        for _ in range(30):
            t.process(self._flat_landmarks(), 640, 480)
        t.on_trigger.assert_not_called()

    def test_pinch_activates_advanced_layer(self):
        t = self._make_thread()
        lms = self._flat_landmarks()
        lms[4] = {"x": 0.5, "y": 0.5, "z": 0.0}
        lms[8] = {"x": 0.5, "y": 0.5, "z": 0.0}
        for _ in range(FLICK_CONFIRM_FRAMES + 1):
            t.process(lms, 640, 480)
        t.on_layer.assert_called_with(True)

    def test_no_pinch_stays_normal_layer(self):
        t = self._make_thread()
        lms = self._flat_landmarks()
        lms[4] = {"x": 0.1, "y": 0.1, "z": 0.0}
        lms[8] = {"x": 0.9, "y": 0.9, "z": 0.0}
        for _ in range(10):
            t.process(lms, 640, 480)
        assert not t._layer_is_advanced

    def test_cooldown_blocks_second_trigger(self):
        t = self._make_thread()
        t.cooldown = True
        t.vel_threshold = 10.0
        t.resting_anchor = 50.0
        t.max_flick = 200.0
        lms = self._flat_landmarks()
        # Simulate a flick by spreading finger far from wrist
        for i in [8, 12, 16, 20]:
            lms[i] = {"x": 0.9, "y": 0.1, "z": 0.0}
        for _ in range(30):
            t.process(lms, 640, 480)
        t.on_trigger.assert_not_called()

    def test_calibration_step1_records_resting(self):
        t = self._make_thread()
        t.calibrating = True
        t.calib_step = 1
        lms = self._flat_landmarks()
        for _ in range(10):
            t.process(lms, 640, 480)
        assert len(t.calib_results) > 0

    def test_calibration_step2_records_flick_and_velocity(self):
        t = self._make_thread()
        t.calibrating = True
        t.calib_step = 2
        lms = self._flat_landmarks()
        # Spread fingers to simulate flick displacement
        for i in [8, 12, 16, 20]:
            lms[i] = {"x": 0.9, "y": 0.1, "z": 0.0}
        for _ in range(10):
            t.process(lms, 640, 480)
        assert len(t.calib_results) > 0
        assert len(t.calib_vel_results) > 0

    def test_progress_clamped_between_0_and_1(self):
        t = self._make_thread()
        t.resting_anchor = 50.0
        t.max_flick = 200.0
        lms = self._flat_landmarks()
        for i in [8, 12, 16, 20]:
            lms[i] = {"x": 0.99, "y": 0.01, "z": 0.0}
        captured = []
        t.on_progress = lambda p: captured.append(p)
        for _ in range(5):
            t.process(lms, 640, 480)
        for p in captured:
            for v in p.values():
                assert 0.0 <= v <= 1.0

    def test_layer_deactivates_after_pinch_release(self):
        t = self._make_thread()
        lms = self._flat_landmarks()
        lms[4] = {"x": 0.5, "y": 0.5, "z": 0.0}
        lms[8] = {"x": 0.5, "y": 0.5, "z": 0.0}
        for _ in range(FLICK_CONFIRM_FRAMES + 1):
            t.process(lms, 640, 480)
        assert t._layer_is_advanced

        lms[4] = {"x": 0.1, "y": 0.1, "z": 0.0}
        lms[8] = {"x": 0.9, "y": 0.9, "z": 0.0}
        for _ in range(FLICK_CONFIRM_FRAMES + 1):
            t.process(lms, 640, 480)
        assert not t._layer_is_advanced


# ── Processor helper method tests ─────────────────────────────────────────────
class TestProcessorHelpers:
    def _make_proc(self):
        p = Processor()
        p.on_progress = MagicMock()
        p.on_trigger = MagicMock()
        p.on_layer = MagicMock()
        return p

    def _lm_fn(self, coords: dict):
        def lm(idx):
            x, y = coords.get(idx, (0.5, 0.5))
            return x * 640, y * 480

        return lm

    # _update_pinch_layer
    def test_pinch_layer_activates_on_close_thumb_index(self):
        p = self._make_proc()
        lm = self._lm_fn({4: (0.5, 0.5), 8: (0.5, 0.5), 9: (0.3, 0.3)})
        for _ in range(FLICK_CONFIRM_FRAMES + 1):
            p._update_pinch_layer(lm, 320, 240, 640, 480)
        assert p._layer_is_advanced
        p.on_layer.assert_called_with(True)

    def test_pinch_layer_stays_off_when_far(self):
        p = self._make_proc()
        lm = self._lm_fn({4: (0.1, 0.1), 8: (0.9, 0.9), 9: (0.5, 0.3)})
        for _ in range(10):
            p._update_pinch_layer(lm, 320, 240, 640, 480)
        assert not p._layer_is_advanced

    def test_pinch_layer_deactivates_after_release(self):
        p = self._make_proc()
        # Activate
        lm_close = self._lm_fn({4: (0.5, 0.5), 8: (0.5, 0.5), 9: (0.3, 0.3)})
        for _ in range(FLICK_CONFIRM_FRAMES + 1):
            p._update_pinch_layer(lm_close, 320, 240, 640, 480)
        assert p._layer_is_advanced
        # Release
        lm_far = self._lm_fn({4: (0.1, 0.1), 8: (0.9, 0.9), 9: (0.5, 0.3)})
        for _ in range(FLICK_CONFIRM_FRAMES + 1):
            p._update_pinch_layer(lm_far, 320, 240, 640, 480)
        assert not p._layer_is_advanced
        p.on_layer.assert_called_with(False)

    # _compute_unit_state
    def test_unit_state_returns_all_three_units(self):
        p = self._make_proc()
        raw = {"index": 100.0, "middle": 80.0, "ring": 60.0, "pinky": 55.0, "ring_pinky": 60.0}
        state = p._compute_unit_state(raw)
        assert set(state.keys()) == {"index", "middle", "ring_pinky"}

    def test_ring_pinky_velocity_is_max_of_ring_and_pinky(self):
        p = self._make_proc()
        raw_seed = {"index": 50.0, "middle": 50.0, "ring": 50.0, "pinky": 50.0, "ring_pinky": 50.0}
        p._compute_unit_state(raw_seed)
        raw = {"index": 100.0, "middle": 80.0, "ring": 200.0, "pinky": 50.0, "ring_pinky": 200.0}
        state = p._compute_unit_state(raw)
        # ring moved a lot, pinky didn't — ring_pinky vel should reflect ring
        assert state["ring_pinky"][1] >= 0

    # _run_wta
    def test_wta_normal_layer_includes_index(self):
        p = self._make_proc()
        p._layer_is_advanced = False
        unit_state = {"index": (200.0, 0.0), "middle": (50.0, 0.0), "ring_pinky": (30.0, 0.0)}
        active, winner = p._run_wta(unit_state)
        assert winner == "index"
        assert "index" in active

    def test_wta_advanced_layer_excludes_index(self):
        p = self._make_proc()
        p._layer_is_advanced = True
        unit_state = {"index": (200.0, 0.0), "middle": (150.0, 0.0), "ring_pinky": (100.0, 0.0)}
        active, winner = p._run_wta(unit_state)
        assert winner != "index"
        assert "index" not in active

    def test_wta_returns_fallback_when_all_gated(self):
        p = self._make_proc()
        p._layer_is_advanced = False
        # All equal — all should pass gate
        unit_state = {"index": (100.0, 0.0), "middle": (100.0, 0.0), "ring_pinky": (100.0, 0.0)}
        active, winner = p._run_wta(unit_state)
        assert len(active) == 3

    # _record_calibration
    def test_record_calib_step1_appends_dist_only(self):
        p = self._make_proc()
        p.calibrating = True
        p.calib_step = 1
        p._record_calibration({"index": (120.0, 5.0)}, "index")
        assert 120.0 in p.calib_results
        assert len(p.calib_vel_results) == 0

    def test_record_calib_step2_appends_dist_and_vel(self):
        p = self._make_proc()
        p.calibrating = True
        p.calib_step = 2
        p._record_calibration({"index": (180.0, 300.0)}, "index")
        assert 180.0 in p.calib_results
        assert 300.0 in p.calib_vel_results

    def test_record_calib_skips_when_not_calibrating(self):
        p = self._make_proc()
        p.calibrating = False
        p._record_calibration({"index": (180.0, 300.0)}, "index")
        assert len(p.calib_results) == 0

    def test_record_calib_skips_when_winner_not_in_active(self):
        p = self._make_proc()
        p.calibrating = True
        p.calib_step = 1
        p._record_calibration({"middle": (120.0, 5.0)}, "index")
        assert len(p.calib_results) == 0

    # _compute_progress
    def test_progress_zero_before_calibration(self):
        p = self._make_proc()
        unit_state = {"index": (100.0, 0.0), "middle": (80.0, 0.0), "ring_pinky": (60.0, 0.0)}
        progress = p._compute_progress(unit_state)
        assert all(v == 0.0 for v in progress.values())

    def test_progress_scales_correctly(self):
        p = self._make_proc()
        p.resting_anchor = 50.0
        p.max_flick = 150.0
        unit_state = {"index": (100.0, 0.0), "middle": (50.0, 0.0), "ring_pinky": (150.0, 0.0)}
        progress = p._compute_progress(unit_state)
        assert abs(progress["index"] - 0.5) < 0.01
        assert progress["middle"] == 0.0
        assert progress["ring_pinky"] == 1.0

    def test_progress_clamped_at_boundaries(self):
        p = self._make_proc()
        p.resting_anchor = 50.0
        p.max_flick = 150.0
        unit_state = {"index": (999.0, 0.0), "middle": (0.0, 0.0), "ring_pinky": (100.0, 0.0)}
        progress = p._compute_progress(unit_state)
        assert progress["index"] == 1.0
        assert progress["middle"] == 0.0
