# Operations Guide

## Prerequisites

| Requirement | Details |
|-------------|---------|
| Python | 3.11 |
| uv | [Install guide](https://docs.astral.sh/uv/getting-started/installation/) |
| Webcam | Built-in or USB, accessible as device index 0 |
| Network | Both laptops on the same WiFi hotspot (for two-machine setup) |
| Arduino (optional) | Uno (serial) or Uno R4 WiFi (wifi) |

---

## Installation

```bash
cd modek
uv sync
```

This creates `.venv` and installs all packages from `uv.lock`.

---

## Arduino Setup (Optional)

Skip this section if running in UI-only mode (`ARDUINO_MODE=none`).

### Option A — Arduino Uno (USB Serial)

**Hardware needed:**
- Arduino Uno
- Breadboard
- 3× Green LED, 2× Blue LED, 1× Red LED
- 6× 220Ω resistors
- 1× Buzzer
- Jumper wires

**Wiring:**
```
Pin 2 → 220Ω → Green LED (+) → GND   (Light)
Pin 3 → 220Ω → Blue LED  (+) → GND   (Fan)
Pin 4 → 220Ω → Red LED   (+) → GND   (Alarm LED)
Pin 5 → Buzzer (+) → GND              (Alarm sound)
Pin 6 → 220Ω → Green LED (+) → GND   (AC)
Pin 7 → 220Ω → Blue LED  (+) → GND   (TV)
```

**Upload sketch:**
1. Open Arduino IDE
2. Open `arduino/sketch.ino`
3. Select **Tools → Board → Arduino Uno**
4. Select **Tools → Port → COM3** (or whichever port appears)
5. Click **Upload**

**Configure `.env`:**
```
ARDUINO_MODE=serial
ARDUINO_PORT=COM3
ARDUINO_BAUD=9600
```

---

### Option B — Arduino Uno R4 WiFi

**Hardware needed:** Same as Option A (same pin wiring).

**Additional:** Built-in 12×8 LED matrix displays the last command automatically.

**Before uploading:**
1. Open `arduino/sketch_wifi.ino`
2. Set your hotspot credentials:
   ```cpp
   const char WIFI_SSID[] = "YOUR_HOTSPOT_SSID";
   const char WIFI_PASS[] = "YOUR_HOTSPOT_PASSWORD";
   ```
3. Select **Tools → Board → Arduino Uno R4 WiFi**
4. Install required libraries via **Tools → Manage Libraries**:
   - `ArduinoGraphics`
   - `Arduino_LED_Matrix` (usually pre-installed)
5. Click **Upload**
6. Open **Serial Monitor** at 115200 baud
7. Copy the IP address shown (e.g. `192.168.43.55`)

**Configure `.env`:**
```
ARDUINO_MODE=wifi
ARDUINO_HOST=192.168.43.55
ARDUINO_HTTP_PORT=80
```

---

## Running the System

### Order of startup (always Service 2 first)

```
1. uv run service2_analysis.py --port 8765   ← start first
2. uv run service1_capture.py --host <IP> --port 8765
3. Click ▶ Start Capture on Service 1 window
```

---

### Option A — Same Machine (localhost)

```bash
# Terminal 1
uv run service2_analysis.py --port 8765

# Terminal 2
uv run service1_capture.py --host 127.0.0.1 --port 8765
```

---

### Option B — Two Laptops (Mobile Hotspot)

1. Enable mobile hotspot on phone
2. Both laptops connect to hotspot
3. Find Laptop 2's IP: `ipconfig` → WiFi adapter IP

```bash
# Laptop 2
uv run service2_analysis.py --port 8765

# Laptop 1
uv run service1_capture.py --host 192.168.43.x --port 8765
```

---

## Demo Workflow

### Step 1 — Position hand
- Service 1 shows live webcam feed
- Position hand so all fingers are visible
- Status: `"Position your hand in frame, then click Start."`

### Step 2 — Click ▶ Start Capture
- Connects to Service 2
- Service 2 status: `"Service 1 connected ✓"`

### Step 3 — Hold still (3 seconds)
- Status: `"Hold your hand completely still..."`
- Countdown: `3... 2... 1...`

### Step 4 — Flick hard (3 seconds)
- Status: `"Flick your fingers hard & fast!"`
- Countdown: `3... 2... 1...`

### Step 5 — Streaming
- Service 2 status: `"Ready ✓ vel_thr=245.1 rest=142.3px"`
- Flick fingers to trigger commands:

| Gesture | Normal Layer | Advanced Layer (pinch ON) |
|---------|-------------|--------------------------|
| Index flick | 💡 Light ON/OFF | — |
| Middle flick | 🌀 Fan ON/OFF | ❄️ AC ON/OFF |
| Ring/Pinky flick | 🔔 Alarm (3s) | 📺 TV ON/OFF |
| Thumb+Index pinch | → Advanced | → Normal |

---

## Stopping the Services

```bash
# Graceful — close window or:
Ctrl+C

# Force kill if hung
netstat -ano | findstr :8765
taskkill /PID <PID> /F
```

---

## Command Line Arguments

### service2_analysis.py

| Argument | Default | Description |
|----------|---------|-------------|
| `--port` | 8765 | WebSocket server port |

### service1_capture.py

| Argument | Default | Description |
|----------|---------|-------------|
| `--host` | required | IP address of Service 2 |
| `--port` | 8765 | WebSocket server port |

---

## Running Tests

```bash
uv run pytest tests/ -v
```

Expected: **58 passed**

```bash
# Run specific test file
uv run pytest tests/test_analysis.py -v        # signal processing (38 tests)
uv run pytest tests/test_arduino_client.py -v  # Arduino client (20 tests)
```

---

## Code Quality

```bash
uv run ruff check service1_capture.py service2_analysis.py arduino_client.py tests/
uv run ruff format service1_capture.py service2_analysis.py arduino_client.py tests/
```

Expected: `All checks passed!`

---

## Log Reference

### Service 1 logs

| Log | Meaning |
|-----|---------|
| `Connecting to ws://...` | Attempting WebSocket connection |
| `Connected to Service 2` | Connection established |
| `[PHASE] calib_hold start` | Entering hold-still phase |
| `[CTRL] sent: calib_hold` | Control message sent |
| `[PHASE] calib_flick start` | Entering flick phase |
| `[PHASE] streaming start` | Entering live stream |
| `Stream send failed: ...` | WebSocket send error |

### Service 2 logs

| Log | Meaning |
|-----|---------|
| `WebSocket server listening on port 8765` | Server ready |
| `Service 1 connected` | Client connected |
| `[CALIB] Phase 1 — hold still` | Recording resting samples |
| `[CALIB] Phase 2 — flick (resting=142.3px)` | Recording flick samples |
| `[CALIB] Done — vel_thr=245.1 ...` | Calibration complete |
| `[TRIGGER] cmd=Light unit=index layer=normal` | Command fired |
| `[ARDUINO] Mode: serial` | Arduino mode loaded from .env |
| `[ARDUINO] Serial connected on COM3 at 9600 baud` | Serial connected |
| `[ARDUINO] WiFi connected to 192.168.x.x:80` | WiFi connected |
| `[ARDUINO] sending: LIGHT_ON` | Command sent to Arduino |
| `[ARDUINO] Serial unavailable — falling back to UI-only mode` | Hardware not found |
| `Stop requested` | Shutdown initiated |
| `[SVC2] Event loop closed` | Clean shutdown complete |

---

## Troubleshooting

### Service 2 shows nothing / no updates
**Cause:** Calibration not completed.
**Fix:** Click **▶ Start Capture** on Service 1 and complete both phases. Check logs for `[CALIB] Done`.

---

### "Connection failed. Check IP and retry."
**Fix:**
1. Confirm Service 2 is running
2. Confirm both machines are on the same network
3. Check IP is correct (`ipconfig` on Laptop 2)
4. Allow port 8765 through firewall:
   ```
   netsh advfirewall firewall add rule name="Modek" dir=in action=allow protocol=TCP localport=8765
   ```

---

### Port 8765 already in use
```bash
netstat -ano | findstr :8765
taskkill /PID <PID> /F
```

---

### Arduino serial port not found
**Log:** `Serial port 'COM3' not found. Available ports: [COM1, COM4]`
**Fix:** Update `ARDUINO_PORT` in `.env` to the correct port. System falls back to UI-only automatically.

---

### Arduino WiFi not reachable
**Log:** `WiFi unavailable — falling back to UI-only mode`
**Fix:**
1. Confirm Arduino R4 WiFi is powered and connected to hotspot
2. Check `ARDUINO_HOST` IP in `.env` matches Serial Monitor output
3. Confirm both Laptop 2 and Arduino are on the same hotspot

---

### Webcam not opening
**Fix:** Change `cv2.VideoCapture(0)` to `cv2.VideoCapture(1)` in `service1_capture.py`.

---

### Commands firing too easily / not firing
**Fix:** Re-calibrate — hold stiller in Phase 1, flick harder in Phase 2.

---

### MediaPipe warnings on startup
```
WARNING: All log messages before absl::InitializeLog()...
W0000 inference_feedback_manager.cc:114
```
Normal — not errors. System works correctly.

---

## File Reference

| File | Purpose |
|------|---------|
| `service1_capture.py` | Laptop 1 — webcam + streaming |
| `service2_analysis.py` | Laptop 2 — analysis + UI + Arduino dispatch |
| `arduino_client.py` | Arduino serial/wifi/none abstraction |
| `final.py` | Original single-machine version (reference) |
| `.env` | Arduino mode configuration |
| `.env.example` | Configuration template |
| `arduino/sketch.ino` | Arduino Uno sketch (USB serial) |
| `arduino/sketch_wifi.ino` | Arduino Uno R4 WiFi sketch (HTTP) |
| `tests/test_analysis.py` | Signal processing tests (38) |
| `tests/test_arduino_client.py` | Arduino client tests (20) |
| `pyproject.toml` | Dependencies + ruff config |
| `uv.lock` | Locked dependency versions |
