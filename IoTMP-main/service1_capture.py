"""
Service 1 — Guided Capture & Stream (Laptop 1)
Guided calibration flow → live landmark streaming to Service 2.

Usage:
    uv run service1_capture.py --host <Laptop2_IP> --port 8765
"""

import argparse
import asyncio
import json
import logging
import signal
import sys
import threading
import time

import cv2
import mediapipe as mp
import numpy as np
import websockets
from PyQt6.QtCore import QRect, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("websockets.client").setLevel(logging.WARNING)
logging.getLogger("websockets.server").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("svc1")

DEFAULT_PORT = 8765

FINGER_TIP_IDS = {"index": 8, "middle": 12, "ring": 16, "pinky": 20}
UNIT_COLORS_BGR = {
    "index": (244, 133, 66),
    "middle": (53, 67, 234),
    "ring_pinky": (0, 167, 255),
}

# ── Capture States ────────────────────────────────────────────────────────────
STATE_READY = "ready"
STATE_CALIB_HOLD = "calib_hold"
STATE_CALIB_FLICK = "calib_flick"
STATE_STREAMING = "streaming"
STATE_CONNECTING = "connecting"
STATE_ERROR = "error"

STATE_LABELS = {
    STATE_READY: ("Position your hand in frame, then click Start.", "#f0a500"),
    STATE_CONNECTING: ("Connecting to Laptop 2...", "#aaaaaa"),
    STATE_CALIB_HOLD: ("Hold your hand completely still...", "#66ccff"),
    STATE_CALIB_FLICK: ("Flick your fingers hard & fast!", "#ff6666"),
    STATE_STREAMING: ("Streaming to Laptop 2...", "#66ff99"),
    STATE_ERROR: ("Connection failed. Check IP and retry.", "#ff4444"),
}


# ── Capture + Stream Thread ───────────────────────────────────────────────────
class CaptureThread(QThread):
    """Runs the asyncio event loop in a QThread.

    Manages the full capture lifecycle:
    READY → CONNECTING → CALIB_HOLD → CALIB_FLICK → STREAMING

    Each phase transition sends a control message to Service 2 so it
    can auto-drive its own calibration without manual intervention.
    """

    frame_ready = pyqtSignal(np.ndarray)
    state_changed = pyqtSignal(str)
    countdown_tick = pyqtSignal(int)  # remaining seconds
    error_occurred = pyqtSignal(str)

    def __init__(self, host: str, port: int):
        super().__init__()
        self.host = host
        self.port = port
        self.running = True
        self._state = STATE_READY
        self._trigger_start = threading.Event()
        self._ws = None
        self._loop: asyncio.AbstractEventLoop | None = None

        self._mp_hands = mp.solutions.hands
        self.hands = self._mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=1,
            model_complexity=1,
            min_detection_confidence=0.8,
            min_tracking_confidence=0.5,
        )
        self.cap = cv2.VideoCapture(0)

    # ── Public control ────────────────────────────────────────────────────────
    def trigger_start(self):
        self._trigger_start.set()

    def stop(self):
        self.running = False
        self._trigger_start.set()
        self.wait()

    # ── Main loop ─────────────────────────────────────────────────────────────
    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        finally:
            self.cap.release()
            self._loop.close()
            log.info("[SVC1] Event loop closed")

    async def _send_control(self, cmd: str):
        if self._ws:
            try:
                await self._ws.send(json.dumps({"type": "control", "cmd": cmd}))
                log.info("[CTRL] sent: %s", cmd)
            except Exception as e:
                log.warning("[CTRL] failed to send %s: %s", cmd, e)

    async def _main(self):
        """Orchestrate the full capture flow: preview → connect → calib → stream.

        Control messages are sent at each phase boundary so Service 2
        automatically starts/advances its calibration in sync.
        """
        await self._preview_phase()
        if not self.running:
            return
        self._set_state(STATE_CONNECTING)
        try:
            uri = f"ws://{self.host}:{self.port}"
            log.info("Connecting to %s", uri)
            async with websockets.connect(uri, open_timeout=10) as ws:
                self._ws = ws
                log.info("Connected to Service 2")

                log.info("[PHASE] calib_hold start")
                await self._send_control("calib_hold")
                await self._countdown_phase(STATE_CALIB_HOLD, 3)
                if not self.running:
                    return

                log.info("[PHASE] calib_flick start")
                await self._send_control("calib_flick")
                await self._countdown_phase(STATE_CALIB_FLICK, 3)
                if not self.running:
                    return

                log.info("[PHASE] streaming start")
                await self._send_control("calib_done")
                self._set_state(STATE_STREAMING)
                await self._stream_phase()

        except Exception as e:
            log.error("Connection error: %s", e)
            self._set_state(STATE_ERROR)
            self.error_occurred.emit(str(e))

    async def _preview_phase(self):
        """Show live feed, wait for user to click Start."""
        self._set_state(STATE_READY)
        while self.running and not self._trigger_start.is_set():
            frame, _ = self._grab_frame()
            if frame is not None:
                self.frame_ready.emit(frame)
            await asyncio.sleep(1 / 30)

    async def _countdown_phase(self, state: str, duration: int):
        """Stream landmarks for `duration` seconds while showing a countdown.

        Landmarks are streamed during calibration phases so Service 2 can
        record resting/flick samples in real time as the user performs them.
        """
        self._set_state(state)
        end_t = time.perf_counter() + duration
        while self.running:
            remaining = int(end_t - time.perf_counter())
            if remaining < 0:
                break
            self.countdown_tick.emit(remaining)
            frame, payload = self._grab_frame()
            if frame is not None:
                self.frame_ready.emit(frame)
            if payload and self._ws:
                try:
                    await self._ws.send(json.dumps(payload))
                except (websockets.ConnectionClosed, OSError) as e:
                    log.warning("[CTRL] send failed during %s: %s", state, e)
            await asyncio.sleep(1 / 30)

    async def _stream_phase(self):
        """Continuously stream landmark frames to Service 2 until stopped."""
        log.debug("Streaming landmarks...")
        while self.running:
            frame, payload = self._grab_frame()
            if frame is not None:
                self.frame_ready.emit(frame)
            if payload and self._ws:
                try:
                    await self._ws.send(json.dumps(payload))
                except Exception as e:
                    log.error("Stream send failed: %s", e)
                    self._set_state(STATE_ERROR)
                    break
            await asyncio.sleep(1 / 30)

    # ── Frame grab + landmark extraction ─────────────────────────────────────
    def _grab_frame(self) -> tuple[np.ndarray | None, dict | None]:
        """Capture one frame, extract landmarks, draw overlay.

        Returns (overlay_frame, payload) where payload is None if no hand
        is detected — callers must check before sending over WebSocket.
        """
        ret, frame = self.cap.read()
        if not ret:
            return None, None

        h, w, _ = frame.shape
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.hands.process(frame_rgb)
        overlay = frame_rgb.copy()

        landmarks = []
        if results.multi_hand_landmarks:
            ldmk = results.multi_hand_landmarks[0]
            wrist = ldmk.landmark[0]
            wx, wy = wrist.x * w, wrist.y * h

            # Draw wrist
            cv2.circle(overlay, (int(wx), int(wy)), 9, (20, 215, 0), 2)

            for name, tip_id in FINGER_TIP_IDS.items():
                tip = ldmk.landmark[tip_id]
                tx, ty = tip.x * w, tip.y * h
                color = UNIT_COLORS_BGR.get(name, UNIT_COLORS_BGR["ring_pinky"])
                cv2.circle(overlay, (int(tx), int(ty)), 8, color, 2)
                cv2.line(overlay, (int(wx), int(wy)), (int(tx), int(ty)), color, 1)

            # Thumb
            thumb = ldmk.landmark[4]
            cv2.circle(overlay, (int(thumb.x * w), int(thumb.y * h)), 6, (255, 255, 255), -1)

            landmarks = [{"x": lm.x, "y": lm.y, "z": lm.z} for lm in ldmk.landmark]

        # State label overlay
        label, _ = STATE_LABELS.get(self._state, ("", "#ffffff"))
        cv2.putText(
            overlay, label, (10, h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1
        )

        # Only send a payload when a hand is detected.
        # Service 2 handles empty landmark arrays gracefully but
        # skipping the send reduces unnecessary network traffic.
        payload = (
            {
                "timestamp": time.time(),
                "frame_size": {"w": w, "h": h},
                "landmarks": landmarks,
            }
            if landmarks
            else None
        )

        return overlay, payload

    def _set_state(self, state: str):
        self._state = state
        self.state_changed.emit(state)


# ── Progress Bar (reused from final.py) ──────────────────────────────────────
class UnitProgressBarWidget(QWidget):
    def __init__(self, label: str, fill_color: QColor):
        super().__init__()
        self.label, self.fill_color = label, fill_color
        self.value: float = 0.0
        self.setMinimumWidth(70)

    def set_value(self, val: float):
        self.value = max(0.0, min(1.0, float(val)))
        self.update()

    def paintEvent(self, event):
        qp = QPainter(self)
        w, h = self.width(), self.height()
        fill_h = int(self.value * h)
        qp.setBrush(QColor(40, 40, 45))
        qp.setPen(Qt.PenStyle.NoPen)
        qp.drawRect(0, 0, w, h)
        qp.setBrush(self.fill_color)
        qp.drawRect(4, h - fill_h, w - 8, fill_h)
        qp.setPen(QColor(255, 255, 255))
        qp.setBrush(Qt.BrushStyle.NoBrush)
        qp.drawRect(4, 2, w - 8, h - 4)
        qp.setPen(QColor(230, 230, 230))
        qp.setFont(QFont("Arial", 9))
        qp.drawText(QRect(0, 2, w, 18), Qt.AlignmentFlag.AlignCenter, self.label)
        qp.drawText(
            QRect(0, h // 2 - 10, w, 20), Qt.AlignmentFlag.AlignCenter, f"{int(self.value * 100)}%"
        )


# ── Countdown Widget ──────────────────────────────────────────────────────────
class CountdownWidget(QLabel):
    def __init__(self):
        super().__init__("")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(60)
        self.setStyleSheet(
            "font-size: 36px; font-weight: bold; color: #ffffff; background: transparent;"
        )

    def set_count(self, n: int):
        self.setText(str(n) if n > 0 else "")


# ── Main Window ───────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self, host: str, port: int):
        super().__init__()
        self.setWindowTitle("Finger Flick — Service 1 (Capture)")
        self.setFixedSize(880, 620)
        self.setStyleSheet("background-color: #1a1a2e;")

        # Video
        self.video_label = QLabel()
        self.video_label.setFixedSize(640, 480)
        self.video_label.setStyleSheet("background-color: #222;")

        # Status
        self.status_label = QLabel("Position your hand in frame, then click Start.")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            "color: #f0a500; font-size: 13px; font-weight: bold; padding: 4px;"
        )

        # Countdown
        self.countdown = CountdownWidget()

        # Start button
        self.start_btn = QPushButton("▶  Start Capture")
        self.start_btn.setFixedHeight(44)
        self.start_btn.setStyleSheet(
            "QPushButton { background:#2ecc71; color:#000; font-size:14px; font-weight:bold; border-radius:6px; }"
            "QPushButton:disabled { background:#555; color:#999; }"
        )
        self.start_btn.clicked.connect(self._on_start)

        # Finger indicators (right panel)
        self.index_bar = UnitProgressBarWidget("Index", QColor(244, 133, 66))
        self.middle_bar = UnitProgressBarWidget("Middle", QColor(53, 67, 234))
        self.ring_bar = UnitProgressBarWidget("Ring/Pinky", QColor(0, 167, 255))

        right_col = QVBoxLayout()
        right_col.addWidget(QLabel("  Finger Activity"))
        bar_row = QHBoxLayout()
        for bar in (self.index_bar, self.middle_bar, self.ring_bar):
            bar_row.addWidget(bar)
        bar_widget = QWidget()
        bar_widget.setLayout(bar_row)
        bar_widget.setFixedSize(220, 300)
        right_col.addWidget(bar_widget)
        right_col.addWidget(self.countdown)
        right_col.addStretch(1)

        left_col = QVBoxLayout()
        left_col.addWidget(self.video_label)
        left_col.addWidget(self.status_label)
        left_col.addWidget(self.start_btn)

        root = QHBoxLayout()
        root.addLayout(left_col)
        root.addLayout(right_col)

        holder = QWidget()
        holder.setLayout(root)
        self.setCentralWidget(holder)

        self.thread = CaptureThread(host, port)
        self.thread.frame_ready.connect(self._update_frame)
        self.thread.state_changed.connect(self._on_state_change)
        self.thread.countdown_tick.connect(self.countdown.set_count)
        self.thread.error_occurred.connect(self._on_error)
        self.thread.start()

    @pyqtSlot(np.ndarray)
    def _update_frame(self, frame: np.ndarray):
        h, w, ch = frame.shape
        qt_img = QImage(frame.data, w, h, ch * w, QImage.Format.Format_RGB888)
        self.video_label.setPixmap(QPixmap.fromImage(qt_img))

    @pyqtSlot(str)
    def _on_state_change(self, state: str):
        label, color = STATE_LABELS.get(state, ("", "#ffffff"))
        self.status_label.setText(label)
        self.status_label.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: bold; padding: 4px;"
        )
        # Disable start button once flow begins
        if state != STATE_READY:
            self.start_btn.setEnabled(False)
            self.start_btn.setText("Capturing..." if state != STATE_ERROR else "▶  Start Capture")
        if state == STATE_ERROR:
            self.start_btn.setEnabled(True)
            self.start_btn.setText("▶  Retry")
        if state != STATE_CALIB_HOLD and state != STATE_CALIB_FLICK:
            self.countdown.set_count(0)

    @pyqtSlot(str)
    def _on_error(self, msg: str):
        self.status_label.setText(f"Error: {msg}")

    def _on_start(self):
        self.thread.trigger_start()

    def closeEvent(self, event):
        self.thread.stop()
        event.accept()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, help="IP address of Laptop 2")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    w = MainWindow(args.host, args.port)
    w.show()

    signal.signal(signal.SIGINT, lambda *_: w.close())
    # QTimer forces the Qt event loop to yield to Python every 200ms
    # so SIGINT can be processed. Without this, Qt blocks signal handling.
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(200)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
