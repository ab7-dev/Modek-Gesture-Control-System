# Architecture

## Overview

Modek is a three-layer system:
1. **Service 1** — hardware interaction (webcam, MediaPipe)
2. **Service 2** — analysis, UI, and command dispatch
3. **Arduino** — physical hardware output (LEDs, buzzer)

The WebSocket boundary is placed **after MediaPipe landmark extraction** — Service 1 sends 21 normalised landmark coordinates per frame (~1KB), not raw video frames (~300KB).

The Arduino boundary is placed **after command resolution** — Service 2 sends a short string (`LIGHT_ON\n`) over USB serial or HTTP POST, not raw gesture data.

---

## Full System Diagram

```
Service 1 (Laptop 1)                    Service 2 (Laptop 2)
─────────────────────                   ──────────────────────────────────────

  cv2.VideoCapture                        WebSocket Server
       │                                       │
       ▼                                       ▼
  MediaPipe Hands                        _handle_control()
  (landmark extraction)                  ├── calib_hold  → Processor.calib_step=1
       │                                 ├── calib_flick → Processor.calib_step=2
       ▼                                 └── calib_done  → compute thresholds
  CaptureThread                               │
  ├── _preview_phase()                        ▼
  ├── _countdown_phase()  ──────────▶   Processor.process()
  └── _stream_phase()     landmarks     ├── _update_pinch_layer()
                                        ├── _compute_unit_state()
  PyQt6 UI                              ├── _run_wta()
  ├── Live webcam feed                  ├── _record_calibration()
  ├── Countdown timer                   ├── _compute_progress()
  └── Status labels                     └── _check_fire()
                                              │
                                              ▼
                                        AnalysisThread signals
                                        ├── progress_update  → progress bars
                                        ├── trigger_activation → activate_action()
                                        ├── layer_active_update → layer indicator
                                        └── connection_status → status bar
                                              │
                                              ▼
                                        ArduinoClient (arduino_client.py)
                                        ├── NullClient   → UI only (ARDUINO_MODE=none)
                                        ├── SerialClient → USB serial → Arduino Uno
                                        └── WiFiClient   → HTTP POST → Arduino R4 WiFi
                                              │
                                              ▼
                                        Arduino Board
                                        ├── Pin 2 Green LED  → Light
                                        ├── Pin 3 Blue LED   → Fan
                                        ├── Pin 4 Red LED    → Alarm
                                        ├── Pin 5 Buzzer     → Alarm sound
                                        ├── Pin 6 Green LED  → AC
                                        └── Pin 7 Blue LED   → TV
```

---

## Service 1 — Capture

**File:** `service1_capture.py`

**Responsibilities:**
- Open webcam via OpenCV
- Run MediaPipe Hands on every frame
- Draw landmark overlays on the live feed
- Guide the user through calibration phases with countdown UI
- Stream landmark JSON to Service 2 at ~30fps
- Send control messages at phase transitions

**Key classes:**

| Class | Role |
|-------|------|
| `CaptureThread` | Asyncio event loop in a QThread — handles WebSocket + frame capture |
| `MainWindow` | PyQt6 window — video feed, status label, countdown, start button |

**State machine (phases):**

```
READY → CONNECTING → CALIB_HOLD (3s) → CALIB_FLICK (3s) → STREAMING
```

---

## Service 2 — Analysis

**File:** `service2_analysis.py`

**Responsibilities:**
- Host WebSocket server
- Receive landmark JSON from Service 1
- Run all signal processing and gesture recognition
- Dispatch commands to ArduinoClient
- Drive the PyQt6 demo UI

**Key classes:**

| Class | Role |
|-------|------|
| `OneEuroFilter` | Adaptive low-pass filter for position and velocity smoothing |
| `FingerUnit` | Per-finger state machine (IDLE → RISING → DWELL → FIRE) |
| `Processor` | Core analysis logic — no Qt dependency, fully unit-testable |
| `AnalysisThread` | QThread wrapping asyncio WebSocket server, delegates to Processor |
| `MainWindow` | PyQt6 demo UI — command display, timeline feed, progress bars, stats |

---

## Arduino Client

**File:** `arduino_client.py`

**Responsibilities:**
- Abstract serial and WiFi communication behind a common interface
- Track ON/OFF toggle state for Light, Fan, AC, TV
- Resolve gesture command names to Arduino command strings
- Fall back to NullClient if hardware is unreachable

**Key classes:**

| Class | Mode | Transport |
|-------|------|-----------|
| `NullClient` | `none` | No-op — UI only |
| `SerialClient` | `serial` | USB serial via `pyserial` |
| `WiFiClient` | `wifi` | HTTP POST via `httpx` |

**Toggle state tracking:**

```
Light:  OFF → flick → ON  → flick → OFF  (tracked in Python)
Fan:    OFF → flick → ON  → flick → OFF
AC:     OFF → flick → ON  → flick → OFF
TV:     OFF → flick → ON  → flick → OFF
Alarm:  always triggers 3s on Arduino, no toggle
```

**Command resolution:**

| Gesture cmd | State | Arduino string |
|-------------|-------|---------------|
| Light | → ON | `LIGHT_ON` |
| Light | → OFF | `LIGHT_OFF` |
| Fan | → ON | `FAN_ON` |
| Fan | → OFF | `FAN_OFF` |
| Alarm | — | `ALARM` |
| AC | → ON | `AC_ON` |
| AC | → OFF | `AC_OFF` |
| TV | → ON | `TV_ON` |
| TV | → OFF | `TV_OFF` |

---

## Arduino Sketches

| File | Board | Transport |
|------|-------|-----------|
| `arduino/sketch.ino` | Arduino Uno | USB serial (9600 baud) |
| `arduino/sketch_wifi.ino` | Arduino Uno R4 WiFi | HTTP POST on port 80 |

Both sketches use identical pin mapping and command strings. `sketch_wifi.ino` additionally displays the last command on the built-in 12×8 LED matrix.

---

## Data Flow (per frame)

```
Service 1                              Service 2
─────────                              ─────────
1. Capture frame (cv2)
2. Extract 21 landmarks (MediaPipe)
3. Serialize to JSON
4. Send over WebSocket ──────────────▶ 5. Deserialize JSON
                                       6. _update_pinch_layer()
                                       7. _compute_unit_state()
                                       8. _run_wta()
                                       9. _record_calibration()
                                      10. _compute_progress()
                                      11. _check_fire()
                                            │ if fired
                                            ▼
                                      12. ArduinoClient.send(cmd)
                                            ├── resolve toggle state
                                            ├── build command string
                                            └── send to hardware
                                      13. Update UI (flash, timeline, bars)
```

---

## Threading Model

### Service 1
- **Main thread:** PyQt6 event loop
- **CaptureThread (QThread):** asyncio event loop — webcam capture + WebSocket send

### Service 2
- **Main thread:** PyQt6 event loop — UI updates via Qt signals
- **AnalysisThread (QThread):** asyncio event loop — WebSocket server + Processor calls
- **Cooldown thread (daemon):** sleeps 2s then resets `Processor.cooldown`

**Thread safety:** `Processor` state is only written from `AnalysisThread`. `ArduinoClient` is only called from the Qt main thread via `activate_action()`. Qt signals handle all cross-thread UI updates.

---

## Shutdown Sequence

```
User closes window / Ctrl+C
       │
       ▼
signal.SIGINT → w.close()
       │
       ▼
closeEvent()
       ├── arduino.close()     ← release serial port / HTTP session
       └── thread.stop()
               ├── self.running = False
               ├── loop.call_soon_threadsafe(loop.stop)
               ├── Cancel all pending asyncio tasks
               ├── loop.close()
               └── QThread.terminate() if wait(3000) times out
```

---

## Configuration

All Arduino settings are read from `.env` at startup via `python-dotenv`:

```
ARDUINO_MODE=none       # none | serial | wifi
ARDUINO_PORT=COM3       # serial only
ARDUINO_BAUD=9600       # serial only
ARDUINO_HOST=192.168.x.x  # wifi only
ARDUINO_HTTP_PORT=80    # wifi only
```

If `serial` or `wifi` mode is configured but hardware is unreachable, `create_arduino_client()` automatically falls back to `NullClient` and logs a warning. The system continues running with UI-only output.
