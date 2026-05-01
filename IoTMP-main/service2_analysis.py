"""
Service 2 — Analysis & UI (Laptop 2)
Hosts a WebSocket server, receives landmark JSON from Service 1,
runs FingerUnit FSMs + WTA logic, and drives the PyQt6 UI.

Usage:
    uv run service2_analysis.py --port 8765
"""

import argparse
import asyncio
import json
import logging
import math
import signal
import sys
import threading
import time
from collections import deque

import numpy as np
import websockets
from PyQt6.QtCore import QRect, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from arduino_client import ArduinoClient, _build_display_label, create_arduino_client

logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("websockets.client").setLevel(logging.WARNING)
logging.getLogger("websockets.server").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("svc2")

# ── Constants ────────────────────────────────────────────────────────────────
# WTA crosstalk gate: a finger is only "active" if its displacement is
# >= 40% of the dominant finger. Prevents sympathetic finger movement
# from stealing the trigger.
ISOLATION_INDEX: float = 0.4

# Post-trigger lockout. Prevents the same gesture firing repeatedly
# within a 2-second window (accidental double-trigger protection).
COOLDOWN_PERIOD: float = 2.0

# RISING→DWELL transition: velocity must drop to <=50% of its peak
# before dwell confirmation begins. Ensures the flick has genuinely
# peaked and is decelerating.
VEL_PEAK_DROP_RATIO: float = 0.50

# Minimum time the finger must hold near its peak position before
# the trigger fires. Filters out tremor spikes that pass the velocity
# threshold but don't sustain a stable peak.
DWELL_CONFIRM_MS: int = 200

# MediaPipe estimates depth from a single RGB camera, causing
# sub-pixel jitter (~3mm). Any displacement below this floor is
# clamped to the resting anchor to suppress hallucination noise.
NOISE_FLOOR_PX: float = 12.5

# Default peak position tolerance before calibration is run.
# Set to 40% of the noise floor as a conservative starting point.
DEFAULT_PEAK_POS_TOL_PX: float = NOISE_FLOOR_PX * 0.4

# Maximum allowed frame-to-frame position jump during DWELL.
# Larger jumps indicate landmark instability, not a real flick peak.
JUMP_TOL_PX: float = 10.0

# Number of consecutive stable frames required to confirm a flick
# or a pinch layer switch. Guards against single-frame noise spikes.
FLICK_CONFIRM_FRAMES: int = 5

# Pinch detection: thumb-index distance threshold in mm, normalised
# by hand size (wrist→middle MCP ~90mm) to be scale-invariant across
# different hand sizes and camera distances.
PINCH_THRESHOLD_MM: float = 20.0
HAND_SIZE_MM_REFERENCE: float = 90.0
PINCH_THRESHOLD_RATIO: float = PINCH_THRESHOLD_MM / HAND_SIZE_MM_REFERENCE

# MediaPipe landmark indices for each finger tip.
FINGER_TIP_IDS: dict[str, int] = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}

# Command maps per layer. Advanced layer activates on thumb-index pinch.
# Index is disabled in Advanced layer (mechanically occupied by pinch).
# Scene renamed to Fan to match physical hardware demo.
COMMAND_MAP_NORMAL: dict[str, str] = {"index": "Light", "middle": "Fan", "ring_pinky": "Alarm"}
COMMAND_MAP_ADVANCED: dict[str, str] = {"middle": "AC", "ring_pinky": "TV"}

DEFAULT_PORT = 8765


# ── One Euro Filter ───────────────────────────────────────────────────────────
class OneEuroFilter:
    """Adaptive low-pass filter (Géry et al., 2012).

    Automatically adjusts its cutoff frequency based on signal speed:
    - Slow movement  → low cutoff  → heavy smoothing (suppresses tremor)
    - Fast movement  → high cutoff → light smoothing (preserves flick sharpness)

    Two instances are used per FingerUnit:
    - Position filter: min_cutoff=0.1, beta=0.01  (tight, tremor-resistant)
    - Velocity filter: min_cutoff=2.0, beta=0.0   (looser, fast response)
    """

    def __init__(self, freq=30.0, min_cutoff=0.5, beta=0.05, d_cutoff=1.0):
        self.freq, self.min_cutoff, self.beta, self.d_cutoff = freq, min_cutoff, beta, d_cutoff
        self.x_prev = self.dx_prev = None

    def _alpha(self, cutoff):
        # Convert cutoff frequency (Hz) to a smoothing coefficient alpha.
        # alpha close to 1 = light smoothing; alpha close to 0 = heavy smoothing.
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / (1.0 / self.freq))

    def __call__(self, x):
        if self.x_prev is None:
            # First sample: initialise with no smoothing applied.
            self.x_prev, self.dx_prev = x, 0.0
            return x
        # Estimate instantaneous derivative (speed of change).
        dx = (x - self.x_prev) * self.freq
        # Smooth the derivative with a fixed cutoff to reduce noise.
        dx_hat = self._alpha(self.d_cutoff) * dx + (1 - self._alpha(self.d_cutoff)) * self.dx_prev
        # Adapt the cutoff: faster movement → higher cutoff → less lag.
        a = self._alpha(self.min_cutoff + self.beta * abs(dx_hat))
        # Apply the adaptive low-pass filter.
        x_hat = a * x + (1 - a) * self.x_prev
        self.x_prev, self.dx_prev = x_hat, dx_hat
        return x_hat


# ── FingerUnit (identical to final.py) ───────────────────────────────────────
class FingerUnit:
    _IDLE, _RISING, _DWELL = 0, 1, 2

    def __init__(self, name: str, freq: float = 30.0):
        self.name, self.freq = name, freq
        self._pos_filt = OneEuroFilter(freq=freq, min_cutoff=0.1, beta=0.01)
        self._vel_filt = OneEuroFilter(freq=freq, min_cutoff=2.0, beta=0.0)
        self._prev_pos: float | None = None
        self.velocity: float = 0.0
        self.position: float = 0.0
        self._fsm_state = self._IDLE
        self._local_peak_vel: float = 0.0
        self._peak_pos: float = 0.0
        self.peak_pos_tol_px: float = DEFAULT_PEAK_POS_TOL_PX
        self._dwell_start_t: float | None = None
        self._prev_dwell_pos: float = 0.0
        self._dwell_pos_buf: deque[float] = deque(maxlen=FLICK_CONFIRM_FRAMES)
        self.calib_peak_vel: float = 0.0

    def reset(self):
        """Hard-reset all state — called between calibration phases."""
        self._prev_pos = None
        self.velocity = self.position = 0.0
        self._fsm_state = self._IDLE
        self._local_peak_vel = self._peak_pos = 0.0
        self._dwell_start_t = None
        self._prev_dwell_pos = 0.0
        self._dwell_pos_buf.clear()
        self.calib_peak_vel = 0.0

    def set_peak_pos_tol_px(self, tol_px: float):
        """Set the DWELL position stability window (px). Derived from flick range at calibration."""
        self.peak_pos_tol_px = max(0.0, float(tol_px))

    @property
    def is_dwelling(self):
        """True while the FSM is in the DWELL confirmation window."""
        return self._fsm_state == self._DWELL

    @property
    def fsm_state_name(self):
        return {self._IDLE: "IDLE", self._RISING: "RISING", self._DWELL: "DWELL"}.get(
            self._fsm_state, "UNKNOWN"
        )

    def update(self, raw_dist: float, resting_anchor: float | None = None):
        """Feed one frame sample. Returns (filtered_position, filtered_velocity)."""
        # Clamp micro-jitter to resting anchor: MediaPipe depth estimation
        # produces sub-pixel noise at rest that would generate false velocity.
        if resting_anchor is not None and raw_dist <= NOISE_FLOOR_PX:
            raw_dist = resting_anchor
        fd = self._pos_filt(raw_dist)
        self.position = fd
        if self._prev_pos is None:
            # First frame: no velocity can be computed yet.
            self._prev_pos = fd
            return fd, 0.0
        # Velocity = change in filtered position per second.
        fv = self._vel_filt((fd - self._prev_pos) * self.freq)
        self._prev_pos = fd
        self.velocity = fv
        return fd, fv

    def is_velocity_peak(self, vel_threshold: float) -> bool:
        """Advance the FSM. Returns True exactly once per confirmed flick.

        Must be called once per frame after update().
        """
        v = self.velocity
        if self._fsm_state == self._IDLE:
            # Wait for velocity to cross the personalised threshold.
            if v >= vel_threshold:
                self._fsm_state = self._RISING
                self._local_peak_vel = v
                self._peak_pos = self.position
        elif self._fsm_state == self._RISING:
            # Track the rising velocity peak and its position.
            if v > self._local_peak_vel:
                self._local_peak_vel = v
                self._peak_pos = self.position
            # Velocity has fallen back to <=50% of peak → flick is decelerating.
            elif v <= self._local_peak_vel * VEL_PEAK_DROP_RATIO:
                self._fsm_state = self._DWELL
                self._dwell_start_t = time.perf_counter()
                self._dwell_pos_buf.clear()
                self._prev_dwell_pos = self.position
        elif self._fsm_state == self._DWELL:
            # Abort if landmark jumps too far between frames (tracking instability).
            if abs(self.position - self._prev_dwell_pos) > JUMP_TOL_PX:
                self._fsm_state = self._IDLE
                self._local_peak_vel = self._peak_pos = 0.0
                self._dwell_start_t = None
                self._dwell_pos_buf.clear()
                return False
            self._prev_dwell_pos = self.position
            self._dwell_pos_buf.append(self.position)
            # If position keeps climbing, update the latched peak and restart dwell.
            # This handles slow flicks that haven't fully peaked yet.
            if self.position > self._peak_pos + self.peak_pos_tol_px:
                self._peak_pos = self.position
                self._dwell_start_t = time.perf_counter()
                self._dwell_pos_buf.clear()
                self._prev_dwell_pos = self.position
                return False
            if abs(self.position - self._peak_pos) <= self.peak_pos_tol_px:
                elapsed_ms = (time.perf_counter() - self._dwell_start_t) * 1000.0
                # Fire only when: time held >= 200ms AND 5 stable frames collected.
                if (
                    elapsed_ms >= DWELL_CONFIRM_MS
                    and len(self._dwell_pos_buf) >= FLICK_CONFIRM_FRAMES
                ):
                    # Final check: position spread must be tight.
                    # Rejects tremor that satisfies time/frame count but isn't stable.
                    spread = max(self._dwell_pos_buf) - min(self._dwell_pos_buf)
                    if spread <= JUMP_TOL_PX:
                        self._fsm_state = self._IDLE
                        self._local_peak_vel = self._peak_pos = 0.0
                        self._dwell_start_t = None
                        self._dwell_pos_buf.clear()
                        return True
        return False


# ── Core Processor (no Qt dependency — fully unit-testable) ──────────────────
class Processor:
    """All landmark analysis logic isolated from Qt."""

    def __init__(self):
        self.cooldown = False
        self.calibrating = False
        self.calib_step = 0
        self.calib_results: list[float] = []
        self.calib_vel_results: list[float] = []
        self.resting_anchor: float | None = None
        self.max_flick: float | None = None
        self.vel_threshold: float | None = None
        self._layer_is_advanced = False
        self._pinch_true_streak = 0
        self._pinch_false_streak = 0
        self._locked_winner: str | None = None
        self._pos_dwell_start: dict[str, float | None] = {
            "index": None,
            "middle": None,
            "ring_pinky": None,
        }
        self.finger_units: dict[str, FingerUnit] = {
            "index": FingerUnit("index"),
            "middle": FingerUnit("middle"),
            "ring_pinky": FingerUnit("ring_pinky"),
        }
        self._ring_unit = FingerUnit("ring")
        self._pinky_unit = FingerUnit("pinky")
        # Callbacks — replaced by Qt signals in AnalysisThread
        self.on_progress: callable = lambda p: None
        self.on_trigger: callable = lambda cmd: None
        self.on_layer: callable = lambda active: None

    def _update_pinch_layer(self, lm_fn, wx: float, wy: float, w: int, h: int):
        """Detect thumb-index pinch and update the layer state with hysteresis.

        Pinch distance is normalised by hand size (wrist→middle MCP) to be
        scale-invariant across different hand sizes and camera distances.
        5 consecutive frames required to switch layers in either direction.
        """
        tx_thumb, ty_thumb = lm_fn(4)
        tx_index, ty_index = lm_fn(8)
        pinch_dist = float(np.linalg.norm([tx_thumb - tx_index, ty_thumb - ty_index]))
        tx_mcp, ty_mcp = lm_fn(9)
        # Hand size = wrist to middle MCP distance — scale reference for pinch ratio.
        hand_size = float(np.linalg.norm([tx_mcp - wx, ty_mcp - wy]))
        is_pinched = (pinch_dist / max(1e-6, hand_size)) < PINCH_THRESHOLD_RATIO
        if is_pinched:
            self._pinch_true_streak += 1
            self._pinch_false_streak = 0
        else:
            self._pinch_false_streak += 1
            self._pinch_true_streak = 0
        if not self._layer_is_advanced and self._pinch_true_streak >= FLICK_CONFIRM_FRAMES:
            self._layer_is_advanced = True
            self.finger_units["index"].reset()
            self.on_layer(True)
        elif self._layer_is_advanced and self._pinch_false_streak >= FLICK_CONFIRM_FRAMES:
            self._layer_is_advanced = False
            self.finger_units["index"].reset()
            self.on_layer(False)

    def _compute_unit_state(self, raw_dists: dict) -> dict:
        """Apply One Euro filtering and compute velocity for each finger unit.

        ring_pinky uses the max velocity of ring and pinky individually
        because either finger can initiate the gesture (flexion synergy).
        """
        unit_state: dict[str, tuple[float, float]] = {}
        for name, fu in self.finger_units.items():
            if name == "ring_pinky":
                fd_rp, _ = fu.update(raw_dists["ring_pinky"], resting_anchor=self.resting_anchor)
                _, vel_ring = self._ring_unit.update(
                    raw_dists["ring"], resting_anchor=self.resting_anchor
                )
                _, vel_pinky = self._pinky_unit.update(
                    raw_dists["pinky"], resting_anchor=self.resting_anchor
                )
                # Use max velocity so either ring or pinky can trigger the unit.
                vel_rp = max(vel_ring, vel_pinky)
                fu.velocity = vel_rp
                unit_state[name] = (fd_rp, vel_rp)
            else:
                unit_state[name] = fu.update(raw_dists[name], resting_anchor=self.resting_anchor)
        return unit_state

    def _run_wta(self, unit_state: dict) -> tuple[dict, str]:
        """Winner-Takes-All: suppress fingers whose displacement is < 40% of dominant.

        Returns (active_units, winner) where active_units only contains fingers
        that passed the isolation gate. Index is excluded in Advanced layer.
        """
        names = (
            ("middle", "ring_pinky")
            if self._layer_is_advanced
            else ("index", "middle", "ring_pinky")
        )
        max_disp = max(unit_state[n][0] for n in names)
        active = {n: unit_state[n] for n in names if unit_state[n][0] >= ISOLATION_INDEX * max_disp}
        winner = max(active or {n: unit_state[n] for n in names}, key=lambda n: unit_state[n][0])
        return active, winner

    def _record_calibration(self, active_units: dict, winner: str):
        if not self.calibrating or winner not in active_units:
            return
        w_dist, w_vel = active_units[winner]
        if self.calib_step == 1:
            self.calib_results.append(w_dist)
        elif self.calib_step == 2:
            self.calib_results.append(w_dist)
            self.calib_vel_results.append(abs(w_vel))
            self.finger_units[winner].calib_peak_vel = max(
                self.finger_units[winner].calib_peak_vel, abs(w_vel)
            )

    def _compute_progress(self, unit_state: dict) -> dict:
        progress = {"index": 0.0, "middle": 0.0, "ring_pinky": 0.0}
        if self.resting_anchor is not None and self.max_flick is not None:
            total_range = max(1e-6, self.max_flick - self.resting_anchor)
            for key in ("index", "middle", "ring_pinky"):
                progress[key] = min(
                    1.0, max(0.0, unit_state[key][0] - self.resting_anchor) / total_range
                )
        return progress

    def _check_fire(self, unit_state: dict, active_units: dict, layer_map: dict):
        """Run FSMs, maintain locked winner, and emit trigger if a flick is confirmed.

        The locked winner prevents WTA from stealing the winner slot during the
        200ms DWELL confirmation window — once a unit enters DWELL it is locked
        until it either fires or aborts.
        """
        fired_units: list[str] = []
        if self.vel_threshold is not None:
            for name, fu in self.finger_units.items():
                if self._layer_is_advanced and name == "index":
                    continue
                if fu.is_velocity_peak(self.vel_threshold):
                    fired_units.append(name)
        eligible = [
            n
            for n, fu in self.finger_units.items()
            if fu.is_dwelling and not (self._layer_is_advanced and n == "index")
        ]
        # Lock the highest-displacement dwelling unit so WTA can't steal it.
        if self._locked_winner is None and eligible:
            self._locked_winner = max(eligible, key=lambda n: unit_state[n][0])
        elif self._locked_winner and not self.finger_units[self._locked_winner].is_dwelling:
            # Unit left DWELL (fired or aborted) — release the lock.
            self._locked_winner = None
        if not self.cooldown and fired_units:
            if self._locked_winner and self._locked_winner in fired_units:
                fire_unit = self._locked_winner
            else:
                isolated = [u for u in fired_units if u in active_units]
                fire_unit = max(isolated or fired_units, key=lambda n: unit_state[n][0])
            self._locked_winner = None
            cmd = layer_map.get(fire_unit, "Unknown")
            self.cooldown = True
            log.info(
                "[TRIGGER] cmd=%-6s  unit=%-10s  layer=%s",
                cmd,
                fire_unit,
                "advanced" if self._layer_is_advanced else "normal",
            )
            self.on_trigger(cmd)
            threading.Thread(target=self._cooldown_fn, daemon=True).start()

    def process(self, landmarks: list, w: int, h: int):
        if not landmarks:
            if self._layer_is_advanced:
                self._layer_is_advanced = False
                self.on_layer(False)
            self.on_progress({"index": 0.0, "middle": 0.0, "ring_pinky": 0.0})
            return

        def lm(idx):
            return landmarks[idx]["x"] * w, landmarks[idx]["y"] * h

        wx, wy = lm(0)
        self._update_pinch_layer(lm, wx, wy, w, h)
        layer_map = COMMAND_MAP_ADVANCED if self._layer_is_advanced else COMMAND_MAP_NORMAL

        tip_px = {name: lm(tid) for name, tid in FINGER_TIP_IDS.items()}
        raw_dists = {
            name: float(np.linalg.norm([tx - wx, ty - wy])) for name, (tx, ty) in tip_px.items()
        }
        raw_dists["ring_pinky"] = max(raw_dists["ring"], raw_dists["pinky"])

        unit_state = self._compute_unit_state(raw_dists)
        active_units, winner = self._run_wta(unit_state)
        self._record_calibration(active_units, winner)
        progress = self._compute_progress(unit_state)
        self._check_fire(unit_state, active_units, layer_map)
        self.on_progress(progress)

    def _cooldown_fn(self):
        time.sleep(COOLDOWN_PERIOD)
        self.cooldown = False


# ── Analysis Thread ───────────────────────────────────────────────────────────
class AnalysisThread(QThread):
    progress_update = pyqtSignal(object)
    trigger_activation = pyqtSignal(str)
    layer_active_update = pyqtSignal(bool)
    connection_status = pyqtSignal(str)
    stats_update = pyqtSignal(str)

    def __init__(self, port: int):
        super().__init__()
        self.port = port
        self.running = True
        self._loop: asyncio.AbstractEventLoop | None = None
        self.proc = Processor()
        self.proc.on_progress = self.progress_update.emit
        self.proc.on_trigger = self.trigger_activation.emit
        self.proc.on_layer = self.layer_active_update.emit

    # Expose proc attributes used by calibration worker in MainWindow
    @property
    def finger_units(self):
        return self.proc.finger_units

    @property
    def _ring_unit(self):
        return self.proc._ring_unit

    @property
    def _pinky_unit(self):
        return self.proc._pinky_unit

    @property
    def resting_anchor(self):
        return self.proc.resting_anchor

    @resting_anchor.setter
    def resting_anchor(self, v):
        self.proc.resting_anchor = v

    @property
    def max_flick(self):
        return self.proc.max_flick

    @max_flick.setter
    def max_flick(self, v):
        self.proc.max_flick = v

    @property
    def vel_threshold(self):
        return self.proc.vel_threshold

    @vel_threshold.setter
    def vel_threshold(self, v):
        self.proc.vel_threshold = v

    @property
    def calibrating(self):
        return self.proc.calibrating

    @calibrating.setter
    def calibrating(self, v):
        self.proc.calibrating = v

    @property
    def calib_step(self):
        return self.proc.calib_step

    @calib_step.setter
    def calib_step(self, v):
        self.proc.calib_step = v

    @property
    def calib_results(self):
        return self.proc.calib_results

    @calib_results.setter
    def calib_results(self, v):
        self.proc.calib_results = v

    @property
    def calib_vel_results(self):
        return self.proc.calib_vel_results

    @calib_vel_results.setter
    def calib_vel_results(self, v):
        self.proc.calib_vel_results = v

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except RuntimeError as e:
            log.debug("[SVC2] Event loop stopped: %s", e)
        finally:
            try:
                pending = asyncio.all_tasks(self._loop)
                for t in pending:
                    t.cancel()
                if pending:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except RuntimeError as e:
                log.warning("[SVC2] Task cleanup error: %s", e)
            self._loop.close()
            log.info("[SVC2] Event loop closed")

    async def _serve(self):
        self.connection_status.emit("Waiting for Service 1...")
        async with websockets.serve(self._handle, "0.0.0.0", self.port):
            log.info("WebSocket server listening on port %d", self.port)
            while self.running:
                await asyncio.sleep(0.1)
        log.info("WebSocket server stopped")

    async def _handle(self, ws):
        self.connection_status.emit("Service 1 connected ✓  — click Start Capture")
        log.info("Service 1 connected")
        async for message in ws:
            if not self.running:
                break
            data = json.loads(message)
            if data.get("type") == "control":
                self._handle_control(data["cmd"])
            else:
                lms = data.get("landmarks", [])
                self.proc.process(lms, data["frame_size"]["w"], data["frame_size"]["h"])
        log.info("Service 1 disconnected")
        self.connection_status.emit("Service 1 disconnected")

    def _handle_control(self, cmd: str):
        p = self.proc
        if cmd == "calib_hold":
            log.info("[CALIB] Phase 1 — hold still")
            self.connection_status.emit("Calibrating: Hold still... (3s)")
            for fu in p.finger_units.values():
                fu.reset()
            p._ring_unit.reset()
            p._pinky_unit.reset()
            p.calib_results = []
            p.calib_vel_results = []
            p.calibrating = True
            p.calib_step = 1
        elif cmd == "calib_flick":
            log.info(
                "[CALIB] Phase 2 — flick  (resting=%.1f px)",
                float(np.mean(p.calib_results)) if p.calib_results else 0.0,
            )
            self.connection_status.emit("Calibrating: Flick hard! (3s)")
            p.resting_anchor = float(np.mean(p.calib_results)) if p.calib_results else 0.0
            p.calib_results = []
            p.calib_vel_results = []
            p.calib_step = 2
        elif cmd == "calib_done":
            rest = p.resting_anchor or 0.0
            max_disp = float(np.max(p.calib_results)) if p.calib_results else rest + 60.0
            peak_vel = float(np.max(p.calib_vel_results)) if p.calib_vel_results else 0.0
            p.vel_threshold = (
                0.30 * peak_vel if peak_vel > 1.0 else max(max_disp - rest, 35.0) / 0.15 * 0.30
            )
            p.max_flick = max(max_disp, rest + 35.0)
            tol = 0.15 * max(1e-6, p.max_flick - rest)
            for fu in p.finger_units.values():
                fu.set_peak_pos_tol_px(tol)
            p._ring_unit.set_peak_pos_tol_px(tol)
            p._pinky_unit.set_peak_pos_tol_px(tol)
            p.calibrating = False
            p.calib_step = 0
            log.info(
                "[CALIB] Done — vel_thr=%.1f  rest=%.1fpx  max_flick=%.1fpx  tol=%.1fpx",
                p.vel_threshold,
                p.resting_anchor,
                p.max_flick,
                tol,
            )
            self.connection_status.emit(
                f"Ready ✓  vel_thr={p.vel_threshold:.1f}  rest={p.resting_anchor:.1f}px"
            )
            self.stats_update.emit(
                f"vel_threshold: {p.vel_threshold:.1f}  |  resting: {p.resting_anchor:.1f}px  |  max_flick: {p.max_flick:.1f}px"
            )

    def stop(self):
        """Signal the asyncio loop to stop and wait for the thread to exit.

        call_soon_threadsafe is required because the loop runs in a different
        thread — calling loop.stop() directly from the Qt thread is not safe.
        """
        log.info("Stop requested")
        self.running = False
        if self._loop and not self._loop.is_closed():
            # Schedule loop.stop() safely from the Qt main thread.
            self._loop.call_soon_threadsafe(self._loop.stop)
        if not self.wait(3000):
            # Thread didn't exit cleanly — force terminate to avoid hung port.
            log.warning("Force terminating AnalysisThread")
            self.terminate()
            self.wait(1000)


# ── Progress Bar Widgets (identical to final.py) ─────────────────────────────
class UnitProgressBarWidget(QWidget):
    def __init__(self, label: str, fill_color: QColor):
        super().__init__()
        self.label, self.fill_color = label, fill_color
        self.disabled = False
        self.disabled_fill_color = QColor(90, 90, 95)
        self.value: float = 0.0
        self.setMinimumWidth(70)

    def set_disabled(self, disabled: bool):
        self.disabled = bool(disabled)
        if self.disabled:
            self.value = 0.0
        self.update()

    def set_value(self, val: float):
        if self.disabled:
            self.value = 0.0
            return
        self.value = max(0.0, min(1.0, float(val)))
        self.update()

    def paintEvent(self, event):
        qp = QPainter(self)
        w, h = self.width(), self.height()
        fill_h = int(self.value * h)
        qp.setBrush(QColor(40, 40, 45))
        qp.setPen(Qt.PenStyle.NoPen)
        qp.drawRect(0, 0, w, h)
        qp.setBrush(self.disabled_fill_color if self.disabled else self.fill_color)
        qp.drawRect(4, h - fill_h, w - 8, fill_h)
        qp.setPen(QColor(255, 255, 255))
        qp.setBrush(Qt.BrushStyle.NoBrush)
        qp.drawRect(4, 2, w - 8, h - 4)
        qp.setPen(QColor(230, 230, 230))
        qp.setFont(QFont("Arial", 9))
        qp.drawText(
            QRect(0, 2, w, 18),
            Qt.AlignmentFlag.AlignCenter,
            "DISABLED" if self.disabled else self.label,
        )
        qp.setPen(QColor(233, 233, 233))
        qp.drawText(
            QRect(0, h // 2 - 10, w, 20), Qt.AlignmentFlag.AlignCenter, f"{int(self.value * 100)}%"
        )


class MultiUnitProgressWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.index_bar = UnitProgressBarWidget("Index", QColor(66, 153, 255))
        self.middle_bar = UnitProgressBarWidget("Middle", QColor(255, 82, 82))
        self.ring_bar = UnitProgressBarWidget("Ring/Pinky", QColor(255, 191, 0))
        root = QHBoxLayout()
        root.setContentsMargins(6, 0, 6, 0)
        root.setSpacing(8)
        for bar in (self.index_bar, self.middle_bar, self.ring_bar):
            root.addWidget(bar)
        self.setLayout(root)

    def set_value(self, val: dict | float):
        if isinstance(val, dict):
            self.index_bar.set_value(val.get("index", 0.0))
            self.middle_bar.set_value(val.get("middle", 0.0))
            self.ring_bar.set_value(val.get("ring_pinky", 0.0))
        else:
            self.index_bar.set_value(float(val))
        self.update()

    def set_index_disabled(self, disabled: bool):
        self.index_bar.set_disabled(disabled)


# ── Command colours ──────────────────────────────────────────────────────────
COMMAND_COLORS: dict[str, str] = {
    "Light": "#f0a500",
    "Fan": "#66ccff",
    "Alarm": "#ff4444",
    "AC": "#66ff99",
    "TV": "#cc88ff",
}


class CommandDisplayWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(130)
        self._label = QLabel("—")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setFont(QFont("Arial", 42, QFont.Weight.Bold))
        self._label.setStyleSheet("color: #444444;")
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)
        self.setLayout(layout)

    def flash(self, cmd: str):
        color = COMMAND_COLORS.get(cmd, "#ffffff")
        self._label.setText(f"⚡ {cmd}")
        self._label.setStyleSheet(f"color: {color}; font-size: 42px; font-weight: bold;")
        threading.Timer(1.5, self._fade).start()

    def _fade(self):
        self._label.setStyleSheet("color: #555555; font-size: 42px; font-weight: bold;")


class HistoryStripWidget(QWidget):
    """Vertical timeline feed showing last 5 commands with timestamp, icon and state.

    Latest entry is fully bright, older entries fade progressively.
    """

    def __init__(self):
        super().__init__()
        self.setFixedHeight(160)
        self._layout = QVBoxLayout()
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(3)
        self._layout.addStretch(1)
        self.setLayout(self._layout)
        self._history: list[str] = []

    def push(self, display_label: str):
        """Add a new entry to the top of the feed."""
        from datetime import datetime

        timestamp = datetime.now().strftime("%H:%M:%S")
        self._history.append(f"{timestamp}  {display_label}")
        if len(self._history) > 5:
            self._history.pop(0)
        # Rebuild all entries with fading opacity.
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._layout.addStretch(1)
        total = len(self._history)
        for i, entry in enumerate(reversed(self._history)):
            # Extract command name to get its color.
            parts = entry.split("  ", 1)
            label_text = parts[1] if len(parts) > 1 else entry
            # Find matching command color.
            color = "#ffffff"
            for cmd, clr in COMMAND_COLORS.items():
                if cmd.lower() in label_text.lower():
                    color = clr
                    break
            # Fade older entries: latest = full opacity, oldest = 35%.
            opacity = 1.0 - (i / max(1, total - 1)) * 0.65 if total > 1 else 1.0
            alpha = int(opacity * 255)
            row = QLabel(label_text if i == 0 else entry)
            row.setStyleSheet(
                f"color: rgba({int(color[1:3], 16)}, "
                f"{int(color[3:5], 16)}, "
                f"{int(color[5:7], 16)}, {alpha}); "
                f"font-size: {'13' if i == 0 else '11'}px; "
                f"font-weight: {'bold' if i == 0 else 'normal'}; "
                f"padding: 1px 4px;"
            )
            self._layout.addWidget(row)


# ── Command display helpers ──────────────────────────────────────────────────
COMMAND_ICONS: dict[str, str] = {
    "Light": "💡",
    "Fan": "🌀",
    "Alarm": "🔔",
    "AC": "❄️",
    "TV": "📺",
}
TOGGLEABLE_COMMANDS = {"Light", "Fan", "AC", "TV"}


# ── Main Window ───────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self, port: int):
        super().__init__()
        self.setWindowTitle("Finger Flick — Service 2 (Analysis)")
        self.setFixedSize(540, 700)
        self.setStyleSheet("background-color: #1a1a2e;")

        # ── Connection status bar
        self.status_label = QLabel("Waiting for Service 1...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setFixedHeight(36)
        self.status_label.setStyleSheet(
            "background:#0d0d1a; color:#f0a500; font-size:13px; font-weight:bold; padding:4px;"
        )

        # ── Layer indicator
        self.layer_indicator = QLabel("LAYER: NORMAL")
        self.layer_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layer_indicator.setFixedHeight(36)
        self.layer_indicator.setStyleSheet(
            "background:#222; color:#aaa; font-size:13px; font-weight:bold;"
        )

        # ── Big command display
        self.cmd_display = CommandDisplayWidget()

        # ── History strip
        self.history_strip = HistoryStripWidget()

        # ── Progress bars
        self.progress_bars = MultiUnitProgressWidget()
        self.progress_bars.setFixedHeight(200)

        # ── Stats bar
        self.stats_label = QLabel("vel_threshold: not calibrated")
        self.stats_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stats_label.setStyleSheet("color:#888; font-size:11px; padding:2px;")

        # ── Calibrate button
        self.calib_button = QPushButton("Calibrate")
        self.calib_button.setFixedHeight(40)
        self.calib_button.setStyleSheet(
            "QPushButton { background:#2255aa; color:#fff; font-size:13px; font-weight:bold; border-radius:6px; }"
            "QPushButton:disabled { background:#444; color:#888; }"
        )
        self.calib_button.clicked.connect(self.start_calibration)

        layout = QVBoxLayout()
        layout.setSpacing(6)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(self.status_label)
        layout.addWidget(self.layer_indicator)
        layout.addWidget(self.cmd_display)
        layout.addWidget(self.history_strip)
        layout.addWidget(self.progress_bars)
        layout.addWidget(self.stats_label)
        layout.addWidget(self.calib_button)

        holder = QWidget()
        holder.setLayout(layout)
        self.setCentralWidget(holder)

        self.thread = AnalysisThread(port)
        self.thread.progress_update.connect(self.progress_bars.set_value)
        self.thread.trigger_activation.connect(self.activate_action)
        self.thread.layer_active_update.connect(self.set_layer_active)
        self.thread.connection_status.connect(self.status_label.setText)
        self.thread.stats_update.connect(self.stats_label.setText)
        self.thread.start()
        self._calibrating = False
        # Initialise Arduino client based on .env configuration.
        # Falls back to NullClient automatically if hardware is unreachable.
        self._arduino: ArduinoClient = create_arduino_client()

    def start_calibration(self):
        if self._calibrating:
            return
        self._calibrating = True

        def _worker():
            t = self.thread
            self.calib_button.setText("Hold hand still…")
            time.sleep(0.4)
            for fu in t.finger_units.values():
                fu.reset()
            t._ring_unit.reset()
            t._pinky_unit.reset()
            t.calib_results = []
            t.calib_vel_results = []
            t.calibrating = True
            t.calib_step = 1
            time.sleep(3.0)

            resting_val = float(np.mean(t.calib_results)) if t.calib_results else 0.0
            t.resting_anchor = resting_val
            t.calib_results = []
            t.calib_vel_results = []
            t.calib_step = 2
            self.calib_button.setText("Flick hard & fast!")
            time.sleep(2.5)

            max_disp_val = float(np.max(t.calib_results)) if t.calib_results else resting_val + 60.0
            peak_vel = float(np.max(t.calib_vel_results)) if t.calib_vel_results else 0.0
            t.vel_threshold = (
                0.30 * peak_vel
                if peak_vel > 1.0
                else max(max_disp_val - resting_val, 35.0) / 0.15 * 0.30
            )
            t.max_flick = max(max_disp_val, resting_val + 35.0)
            flick_range = max(1e-6, float(t.max_flick - t.resting_anchor))
            peak_pos_tol = 0.15 * flick_range
            for fu in t.finger_units.values():
                fu.set_peak_pos_tol_px(peak_pos_tol)
            t._ring_unit.set_peak_pos_tol_px(peak_pos_tol)
            t._pinky_unit.set_peak_pos_tol_px(peak_pos_tol)
            t.calibrating = False
            t.calib_step = 0
            self.calib_button.setText("Calibrate")
            self.calib_button.setEnabled(True)
            self.stats_label.setText(
                f"vel_threshold: {t.vel_threshold:.1f}  |  "
                f"resting: {t.resting_anchor:.1f}px  |  "
                f"max_flick: {t.max_flick:.1f}px"
            )
            self._calibrating = False

        threading.Thread(target=_worker, daemon=True).start()

    @pyqtSlot(str)
    def activate_action(self, cmd: str):
        # Send to Arduino and get the resolved command string (e.g. LIGHT_ON).
        resolved = self._arduino.send(cmd)
        log.info("ACTION → %s", resolved)
        # Build display label with ON/OFF state for toggleable commands.
        state = self._arduino.get_state(cmd)
        display = _build_display_label(cmd, state)
        self.cmd_display.flash(display)
        self.history_strip.push(display)

    @pyqtSlot(bool)
    def set_layer_active(self, active: bool):
        if active:
            self.layer_indicator.setText("LAYER: ADVANCED (Pinch ON)")
            self.layer_indicator.setStyleSheet(
                "background:#145a2a; color:#b9ffcf; font-size:13px; font-weight:bold;"
            )
            self.progress_bars.set_index_disabled(True)
        else:
            self.layer_indicator.setText("LAYER: NORMAL")
            self.layer_indicator.setStyleSheet(
                "background:#222; color:#aaa; font-size:13px; font-weight:bold;"
            )
            self.progress_bars.set_index_disabled(False)

    def closeEvent(self, event):
        log.info("Shutting down Service 2...")
        self._arduino.close()
        self.thread.stop()
        log.info("Service 2 stopped")
        event.accept()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    app = QApplication(sys.argv)

    w = MainWindow(args.port)
    w.show()

    signal.signal(signal.SIGINT, lambda *_: w.close())
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(200)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
