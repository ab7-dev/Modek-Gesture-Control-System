# Modek — Finger Flick Accessibility Control

A real-time hand gesture recognition system that maps finger flick movements to smart home commands (Light, Fan, Alarm, AC, TV). Built with MediaPipe, PyQt6, OpenCV, WebSocket streaming, and Arduino hardware integration.

---

## What It Does

The user performs quick finger flick gestures in front of a webcam. The system detects which finger flicked, maps it to a command, fires it to the UI and optionally sends it to an Arduino to control physical hardware (LEDs, buzzer).

| Gesture | Normal Layer | Advanced Layer (Pinch ON) |
|---------|-------------|--------------------------|
| Index flick | Light (toggle ON/OFF) | — (disabled) |
| Middle flick | Fan (toggle ON/OFF) | AC (toggle ON/OFF) |
| Ring/Pinky flick | Alarm (3s trigger) | TV (toggle ON/OFF) |
| Thumb + Index pinch | Switch to Advanced | Switch to Normal |

---

## Architecture

```
┌─────────────────────────┐        WebSocket        ┌──────────────────────────┐
│   Service 1 (Laptop 1)  │ ──────────────────────▶ │   Service 2 (Laptop 2)   │
│                         │   landmark JSON @ 30fps  │                          │
│  - Webcam capture       │   control messages       │  - FingerUnit FSMs       │
│  - MediaPipe landmarks  │                          │  - WTA logic             │
│  - Guided calib UI      │                          │  - Pinch layer           │
│  - Live overlay         │                          │  - Command triggers      │
│                         │                          │  - PyQt6 demo UI         │
└─────────────────────────┘                          └──────────────────────────┘
                                                               │
                                                    USB serial / WiFi HTTP
                                                               │
                                                    ┌──────────────────────────┐
                                                    │   Arduino                │
                                                    │  - LEDs (Light/Fan/AC/TV)│
                                                    │  - Buzzer (Alarm)        │
                                                    │  - LED matrix (R4 WiFi)  │
                                                    └──────────────────────────┘
```

---

## Project Structure

```
modek/
├── service1_capture.py      # Webcam capture + landmark streaming (Laptop 1)
├── service2_analysis.py     # Analysis engine + demo UI (Laptop 2)
├── arduino_client.py        # Arduino serial/wifi/none abstraction
├── final.py                 # Original single-machine version (reference)
├── .env                     # Arduino mode configuration
├── .env.example             # Configuration template
├── arduino/
│   ├── sketch.ino           # Arduino Uno (USB serial)
│   └── sketch_wifi.ino      # Arduino Uno R4 WiFi (HTTP)
├── tests/
│   ├── test_analysis.py     # 38 unit + mock tests (signal processing)
│   └── test_arduino_client.py # 20 unit tests (Arduino client)
├── docs/
│   ├── ARCHITECTURE.md      # System design and data flow
│   ├── SIGNAL_PROCESSING.md # OneEuroFilter, FSM, WTA explained
│   ├── CALIBRATION.md       # Calibration phases and computation
│   ├── API.md               # WebSocket + Arduino HTTP API
│   └── OPERATIONS.md        # How to run, troubleshoot, demo guide
├── pyproject.toml           # uv project + ruff config
└── uv.lock
```

---

## Quick Start

### Prerequisites
- Python 3.11
- [uv](https://docs.astral.sh/uv/) package manager
- Webcam

### Install
```bash
uv sync
```

### Configure Arduino (optional)
```bash
# Edit .env — default is none (UI only, no hardware needed)
ARDUINO_MODE=none       # none | serial | wifi
ARDUINO_PORT=COM3       # serial only
ARDUINO_HOST=192.168.x.x  # wifi only
```

### Run (same machine)
```bash
# Terminal 1
uv run service2_analysis.py --port 8765

# Terminal 2
uv run service1_capture.py --host 127.0.0.1 --port 8765
```

### Run (two laptops on hotspot)
```bash
# Laptop 2
uv run service2_analysis.py --port 8765

# Laptop 1 (replace with Laptop 2's IP)
uv run service1_capture.py --host 192.168.x.x --port 8765
```

See [docs/OPERATIONS.md](docs/OPERATIONS.md) for the full operational guide.

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `mediapipe` | 0.10.14 | Hand landmark detection |
| `opencv-python` | ≥4.13 | Webcam capture + overlay rendering |
| `PyQt6` | ≥6.10 | Desktop UI |
| `numpy` | ≥2.2 | Vector math |
| `websockets` | ≥16.0 | Service-to-service communication |
| `pyserial` | ≥3.5 | Arduino USB serial communication |
| `httpx` | ≥0.28 | Arduino WiFi HTTP communication |
| `python-dotenv` | ≥1.2 | `.env` configuration loading |

---

## Testing

```bash
uv run pytest tests/ -v
```

58 tests covering: OneEuroFilter, FingerUnit FSM, WTA isolation, Processor helpers, calibration, pinch layer, progress clamping, Arduino toggle logic, Arduino fallback behaviour.

---

## Further Reading

- [Architecture](docs/ARCHITECTURE.md)
- [Signal Processing](docs/SIGNAL_PROCESSING.md)
- [Calibration](docs/CALIBRATION.md)
- [WebSocket + Arduino API](docs/API.md)
- [Operations Guide](docs/OPERATIONS.md)
