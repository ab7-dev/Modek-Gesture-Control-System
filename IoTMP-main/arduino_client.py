"""
Arduino client — abstracts serial and WiFi communication with the Arduino.

Reads configuration from .env:
    ARDUINO_MODE = none | serial | wifi
    ARDUINO_PORT = COM3          (serial only)
    ARDUINO_BAUD = 9600          (serial only)
    ARDUINO_HOST = 192.168.1.x   (wifi only)
    ARDUINO_HTTP_PORT = 80       (wifi only)

If the configured hardware is unreachable at startup, the client
automatically falls back to NullClient so the system keeps running.
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("arduino")

# ── Command strings sent to Arduino ──────────────────────────────────────────
# Each toggleable command has an ON and OFF variant.
# Alarm is a one-shot trigger — Arduino handles the 3s auto-off.
CMD_LIGHT_ON = "LIGHT_ON"
CMD_LIGHT_OFF = "LIGHT_OFF"
CMD_FAN_ON = "FAN_ON"
CMD_FAN_OFF = "FAN_OFF"
CMD_ALARM = "ALARM"
CMD_AC_ON = "AC_ON"
CMD_AC_OFF = "AC_OFF"
CMD_TV_ON = "TV_ON"
CMD_TV_OFF = "TV_OFF"

# Commands that support ON/OFF toggle state.
TOGGLEABLE = {"Light", "Fan", "AC", "TV"}


def _build_display_label(cmd: str, state: bool) -> str:
    """Build icon + label + ON/OFF string for UI display."""
    icons = {"Light": "💡", "Fan": "🌀", "Alarm": "🔔", "AC": "❄️", "TV": "📺"}
    icon = icons.get(cmd, "")
    if cmd in TOGGLEABLE:
        return f"{icon} {cmd} {'ON' if state else 'OFF'}"
    return f"{icon} {cmd}"


# ── Base client ───────────────────────────────────────────────────────────────
class ArduinoClient:
    """Base class — tracks toggle state and resolves command strings.

    Subclasses implement _send() to deliver the command to hardware.
    Toggle state is tracked in Python so Arduino doesn't need to
    report back its current state.
    """

    def __init__(self):
        # Tracks ON/OFF state for each toggleable command.
        self._state: dict[str, bool] = {
            "Light": False,
            "Fan": False,
            "AC": False,
            "TV": False,
        }

    def send(self, cmd: str) -> str:
        """Resolve the command string, update toggle state, and send.

        Returns the resolved command string (e.g. 'LIGHT_ON') for UI display.
        """
        resolved = self._resolve(cmd)
        if resolved:
            log.info("[ARDUINO] sending: %s", resolved)
            self._send(resolved)
        return resolved or cmd

    def _resolve(self, cmd: str) -> str | None:
        """Map a gesture command name to the Arduino command string."""
        if cmd == "Light":
            self._state["Light"] = not self._state["Light"]
            return CMD_LIGHT_ON if self._state["Light"] else CMD_LIGHT_OFF

        if cmd == "Fan":
            self._state["Fan"] = not self._state["Fan"]
            return CMD_FAN_ON if self._state["Fan"] else CMD_FAN_OFF

        if cmd == "Alarm":
            # No toggle — always triggers a 3s alarm on Arduino.
            return CMD_ALARM

        if cmd == "AC":
            self._state["AC"] = not self._state["AC"]
            return CMD_AC_ON if self._state["AC"] else CMD_AC_OFF

        if cmd == "TV":
            self._state["TV"] = not self._state["TV"]
            return CMD_TV_ON if self._state["TV"] else CMD_TV_OFF

        log.warning("[ARDUINO] unknown command: %s", cmd)
        return None

    def get_state(self, cmd: str) -> bool:
        """Return current ON/OFF state for a toggleable command."""
        return self._state.get(cmd, False)

    def _send(self, cmd: str) -> None:
        """Override in subclasses to deliver the command to hardware."""
        raise NotImplementedError

    def close(self) -> None:
        """Override in subclasses to release hardware resources."""


# ── Null client (no hardware) ─────────────────────────────────────────────────
class NullClient(ArduinoClient):
    """No-op client used when ARDUINO_MODE=none or hardware is unreachable.

    Toggle state is still tracked so the UI shows correct ON/OFF labels.
    """

    def _send(self, cmd: str) -> None:
        log.debug("[ARDUINO] NullClient — command not sent: %s", cmd)


# ── Serial client ─────────────────────────────────────────────────────────────
class SerialClient(ArduinoClient):
    """Sends commands over USB serial to Arduino.

    Raises ConnectionError on init if the port is not available.
    """

    def __init__(self, port: str, baud: int):
        super().__init__()
        import serial
        import serial.tools.list_ports

        available = [p.device for p in serial.tools.list_ports.comports()]
        if port not in available:
            raise ConnectionError(
                f"Serial port '{port}' not found. "
                f"Available ports: {available if available else 'none detected'}"
            )
        try:
            self._serial = serial.Serial(port, baud, timeout=1)
            log.info("[ARDUINO] Serial connected on %s at %d baud", port, baud)
        except serial.SerialException as e:
            raise ConnectionError(f"Failed to open serial port '{port}': {e}") from e

    def _send(self, cmd: str) -> None:
        import serial

        try:
            self._serial.write(f"{cmd}\n".encode())
            self._serial.flush()
        except serial.SerialException as e:
            log.error("[ARDUINO] Serial send failed: %s", e)

    def close(self) -> None:
        if self._serial and self._serial.is_open:
            self._serial.close()
            log.info("[ARDUINO] Serial port closed")


# ── WiFi client ───────────────────────────────────────────────────────────────
class WiFiClient(ArduinoClient):
    """Sends commands via HTTP POST to an ESP32/WiFi Arduino.

    Raises ConnectionError on init if the host is unreachable.
    """

    def __init__(self, host: str, port: int):
        super().__init__()
        import httpx

        self._url = f"http://{host}:{port}/command"
        # Probe the host to confirm it is reachable before proceeding.
        try:
            resp = httpx.get(f"http://{host}:{port}/ping", timeout=3.0)
            if resp.status_code != 200:
                raise ConnectionError(
                    f"Arduino WiFi host {host}:{port} responded with {resp.status_code}"
                )
            log.info("[ARDUINO] WiFi connected to %s:%d", host, port)
        except httpx.ConnectError as e:
            raise ConnectionError(f"Cannot reach Arduino WiFi host {host}:{port} — {e}") from e
        except httpx.TimeoutException as e:
            raise ConnectionError(f"Arduino WiFi host {host}:{port} timed out during probe") from e

    def _send(self, cmd: str) -> None:
        import httpx

        try:
            httpx.post(self._url, json={"cmd": cmd}, timeout=2.0)
        except httpx.TimeoutException:
            log.error("[ARDUINO] WiFi send timed out for cmd: %s", cmd)
        except httpx.ConnectError as e:
            log.error("[ARDUINO] WiFi send failed: %s", e)


# ── Factory ───────────────────────────────────────────────────────────────────
def create_arduino_client() -> ArduinoClient:
    """Read .env and return the appropriate ArduinoClient.

    Falls back to NullClient if:
    - ARDUINO_MODE=none
    - Configured hardware is unreachable
    - Required env vars are missing
    """
    mode = os.getenv("ARDUINO_MODE", "none").strip().lower()
    log.info("[ARDUINO] Mode: %s", mode)

    if mode == "serial":
        port = os.getenv("ARDUINO_PORT", "COM3")
        baud = int(os.getenv("ARDUINO_BAUD", "9600"))
        try:
            return SerialClient(port, baud)
        except ConnectionError as e:
            log.warning(
                "[ARDUINO] Serial unavailable — falling back to UI-only mode. Reason: %s", e
            )
            return NullClient()

    if mode == "wifi":
        host = os.getenv("ARDUINO_HOST", "")
        port = int(os.getenv("ARDUINO_HTTP_PORT", "80"))
        if not host:
            log.warning("[ARDUINO] ARDUINO_HOST not set — falling back to UI-only mode")
            return NullClient()
        try:
            return WiFiClient(host, port)
        except ConnectionError as e:
            log.warning("[ARDUINO] WiFi unavailable — falling back to UI-only mode. Reason: %s", e)
            return NullClient()

    # mode == "none" or unrecognised
    log.info("[ARDUINO] UI-only mode — no hardware commands will be sent")
    return NullClient()
