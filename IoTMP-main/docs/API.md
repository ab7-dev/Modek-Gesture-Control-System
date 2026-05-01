# API Reference

---

## Part 1 — WebSocket API (Service 1 → Service 2)

### Connection

| Property | Value |
|----------|-------|
| Protocol | WebSocket (ws://) |
| Default port | 8765 |
| Direction | Service 1 → Service 2 (unidirectional) |
| Frame type | Text (JSON) |
| Frequency | ~30fps (landmark frames) + 3 control messages per session |

---

### Message Types

All messages are JSON objects with a `type` field.

---

### 1. Landmark Frame

Sent every frame (~30fps) during calibration and streaming phases.

```json
{
  "type": "landmarks",
  "timestamp": 1720000000.123,
  "frame_size": {
    "w": 640,
    "h": 480
  },
  "landmarks": [
    {"x": 0.45, "y": 0.72, "z": -0.02},
    {"x": 0.46, "y": 0.68, "z": -0.03},
    ...
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Always `"landmarks"` |
| `timestamp` | float | Unix timestamp from `time.time()` |
| `frame_size.w` | int | Frame width in pixels |
| `frame_size.h` | int | Frame height in pixels |
| `landmarks` | array[21] | MediaPipe hand landmarks, normalised 0..1 |
| `landmarks[i].x` | float | Normalised x (0=left, 1=right) |
| `landmarks[i].y` | float | Normalised y (0=top, 1=bottom) |
| `landmarks[i].z` | float | Normalised depth (relative to wrist) |

**Key landmark indices:**

| Index | Landmark |
|-------|----------|
| 0 | Wrist |
| 4 | Thumb tip |
| 8 | Index tip |
| 9 | Middle MCP (hand size reference) |
| 12 | Middle tip |
| 16 | Ring tip |
| 20 | Pinky tip |

**Payload size:** ~1.5–1.8KB per frame

**Note:** If no hand is detected, no landmark message is sent for that frame.

---

### 2. Control Messages

Sent at phase transitions to synchronise calibration.

```json
{"type": "control", "cmd": "<command>"}
```

| Command | Sent when | Service 2 action |
|---------|-----------|-----------------|
| `calib_hold` | Hold-still phase starts | Reset FingerUnits, `calib_step=1`, record resting |
| `calib_flick` | Flick phase starts | Compute `resting_anchor`, `calib_step=2`, record flick |
| `calib_done` | Streaming starts | Compute `vel_threshold`, `max_flick`, `peak_pos_tol_px` |

---

### WebSocket Connection Lifecycle

```
Service 2 starts → listening on 0.0.0.0:8765

Service 1 connects
       ↓
calib_hold  → landmarks × ~90 frames
       ↓
calib_flick → landmarks × ~90 frames
       ↓
calib_done  → landmarks × continuous

Service 1 disconnects → server keeps listening for reconnect
```

---

### WebSocket Error Handling

| Scenario | Service 1 | Service 2 |
|----------|-----------|-----------|
| Service 2 not running | Connection error → STATE_ERROR → Retry | N/A |
| Send failure during stream | Log error → STATE_ERROR | Connection closes |
| Send failure during countdown | Log warning → continue | Misses some calib samples |
| Invalid JSON | N/A | Logged, frame skipped |
| No hand in frame | No message sent | `proc.process([], w, h)` → zero progress |

---

## Part 2 — Arduino HTTP API (Service 2 → Arduino R4 WiFi)

Used when `ARDUINO_MODE=wifi`. Service 2 sends HTTP POST requests to the Arduino Uno R4 WiFi's built-in web server.

### Endpoints

#### POST /command

Sends a command to control hardware.

**Request:**
```http
POST /command HTTP/1.1
Content-Type: application/json

{"cmd": "LIGHT_ON"}
```

**Response:**
```
HTTP/1.1 200 OK
Content-Type: text/plain

ACK:LIGHT_ON
```

**Error response:**
```
ERR:UNKNOWN:BADCMD
ERR:PARSE
ERR:METHOD
```

---

#### GET /ping

Used by `WiFiClient` at startup to probe connectivity before accepting the connection.

**Request:**
```http
GET /ping HTTP/1.1
```

**Response:**
```
HTTP/1.1 200 OK

OK
```

If this returns non-200 or times out, `WiFiClient` raises `ConnectionError` and falls back to `NullClient`.

---

### Arduino Command Strings

| Command | Hardware action |
|---------|----------------|
| `LIGHT_ON` | Pin 2 HIGH (Green LED on) |
| `LIGHT_OFF` | Pin 2 LOW (Green LED off) |
| `FAN_ON` | Pin 3 HIGH (Blue LED on) |
| `FAN_OFF` | Pin 3 LOW (Blue LED off) |
| `ALARM` | Pin 4 + Pin 5 beep pattern for 3s, then auto-off |
| `AC_ON` | Pin 6 HIGH (Green LED on) |
| `AC_OFF` | Pin 6 LOW (Green LED off) |
| `TV_ON` | Pin 7 HIGH (Blue LED on) |
| `TV_OFF` | Pin 7 LOW (Blue LED off) |

---

## Part 3 — Arduino Serial API (Service 2 → Arduino Uno)

Used when `ARDUINO_MODE=serial`. Service 2 writes newline-terminated strings over USB serial.

### Protocol

```
Baud rate: 9600
Line ending: \n
Encoding: ASCII
```

**Send:**
```
LIGHT_ON\n
```

**Arduino responds:**
```
ACK:LIGHT_ON\n
```

**Error:**
```
ERR:UNKNOWN:BADCMD\n
```

Same command strings as HTTP API above.

---

## Part 4 — ArduinoClient Internal API

The `ArduinoClient` base class in `arduino_client.py` provides a unified interface regardless of transport.

```python
client = create_arduino_client()   # reads .env, returns correct subclass

resolved = client.send("Light")    # returns "LIGHT_ON" or "LIGHT_OFF"
state = client.get_state("Light")  # returns True (ON) or False (OFF)
client.close()                     # release serial port / HTTP session
```

**Toggle state is tracked in Python** — Arduino does not need to report back its state. Each call to `send()` flips the state for toggleable commands (Light, Fan, AC, TV). Alarm has no toggle — it always sends `ALARM`.
