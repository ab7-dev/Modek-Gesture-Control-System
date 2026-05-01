"""
Unit tests for arduino_client.py
Run with: uv run pytest tests/test_arduino_client.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from arduino_client import (
    CMD_AC_OFF,
    CMD_AC_ON,
    CMD_ALARM,
    CMD_FAN_OFF,
    CMD_FAN_ON,
    CMD_LIGHT_OFF,
    CMD_LIGHT_ON,
    CMD_TV_OFF,
    CMD_TV_ON,
    NullClient,
    _build_display_label,
    create_arduino_client,
)


# ── NullClient ────────────────────────────────────────────────────────────────
class TestNullClient:
    def test_send_does_not_raise(self):
        c = NullClient()
        c.send("Light")  # should not raise

    def test_close_does_not_raise(self):
        c = NullClient()
        c.close()


# ── Toggle logic ──────────────────────────────────────────────────────────────
class TestToggleLogic:
    def _client(self):
        c = NullClient()
        return c

    def test_light_toggles_on_then_off(self):
        c = self._client()
        assert c.send("Light") == CMD_LIGHT_ON
        assert c.get_state("Light") is True
        assert c.send("Light") == CMD_LIGHT_OFF
        assert c.get_state("Light") is False

    def test_fan_toggles_on_then_off(self):
        c = self._client()
        assert c.send("Fan") == CMD_FAN_ON
        assert c.send("Fan") == CMD_FAN_OFF

    def test_ac_toggles_on_then_off(self):
        c = self._client()
        assert c.send("AC") == CMD_AC_ON
        assert c.send("AC") == CMD_AC_OFF

    def test_tv_toggles_on_then_off(self):
        c = self._client()
        assert c.send("TV") == CMD_TV_ON
        assert c.send("TV") == CMD_TV_OFF

    def test_alarm_always_returns_alarm(self):
        c = self._client()
        assert c.send("Alarm") == CMD_ALARM
        assert c.send("Alarm") == CMD_ALARM  # no toggle

    def test_alarm_has_no_state(self):
        c = self._client()
        c.send("Alarm")
        assert c.get_state("Alarm") is False  # not in toggle map

    def test_multiple_toggles_independent(self):
        c = self._client()
        c.send("Light")  # Light ON
        c.send("Fan")  # Fan ON
        assert c.get_state("Light") is True
        assert c.get_state("Fan") is True
        c.send("Light")  # Light OFF
        assert c.get_state("Light") is False
        assert c.get_state("Fan") is True  # Fan unchanged

    def test_unknown_command_returns_cmd(self):
        c = self._client()
        result = c.send("Unknown")
        assert result == "Unknown"


# ── Display label helper ──────────────────────────────────────────────────────
class TestBuildDisplayLabel:
    def test_light_on_label(self):
        assert "Light" in _build_display_label("Light", True)
        assert "ON" in _build_display_label("Light", True)

    def test_light_off_label(self):
        assert "OFF" in _build_display_label("Light", False)

    def test_alarm_has_no_on_off(self):
        label = _build_display_label("Alarm", False)
        assert "ON" not in label
        assert "OFF" not in label
        assert "Alarm" in label

    def test_fan_on_label(self):
        assert "ON" in _build_display_label("Fan", True)

    def test_tv_off_label(self):
        assert "OFF" in _build_display_label("TV", False)


# ── Factory fallback ──────────────────────────────────────────────────────────
class TestCreateArduinoClient:
    def test_none_mode_returns_null_client(self):
        with patch.dict(os.environ, {"ARDUINO_MODE": "none"}):
            client = create_arduino_client()
        assert isinstance(client, NullClient)

    def test_unknown_mode_returns_null_client(self):
        with patch.dict(os.environ, {"ARDUINO_MODE": "bluetooth"}):
            client = create_arduino_client()
        assert isinstance(client, NullClient)

    def test_serial_mode_falls_back_on_missing_port(self):
        with patch.dict(os.environ, {"ARDUINO_MODE": "serial", "ARDUINO_PORT": "COM99"}):
            client = create_arduino_client()
        assert isinstance(client, NullClient)

    def test_wifi_mode_falls_back_on_missing_host(self):
        with patch.dict(os.environ, {"ARDUINO_MODE": "wifi", "ARDUINO_HOST": ""}):
            client = create_arduino_client()
        assert isinstance(client, NullClient)

    def test_wifi_mode_falls_back_on_unreachable_host(self):
        with patch.dict(
            os.environ,
            {
                "ARDUINO_MODE": "wifi",
                "ARDUINO_HOST": "192.168.99.99",
                "ARDUINO_HTTP_PORT": "80",
            },
        ):
            client = create_arduino_client()
        assert isinstance(client, NullClient)


# ── SerialClient ──────────────────────────────────────────────────────────────
class TestSerialClient:
    def _mock_serial_module(self, port="COM3", available=True):
        """Returns a mock serial module with configurable port availability."""
        import serial as _serial

        mock_port = type("Port", (), {"device": port})()
        mock_comports = [mock_port] if available else []

        mock_serial_instance = MagicMock()
        mock_serial_instance.is_open = True

        mock_serial_module = MagicMock()
        mock_serial_module.tools.list_ports.comports.return_value = mock_comports
        mock_serial_module.Serial.return_value = mock_serial_instance
        mock_serial_module.SerialException = _serial.SerialException
        return mock_serial_module, mock_serial_instance

    def test_connects_successfully(self):
        from arduino_client import SerialClient

        mock_mod, _ = self._mock_serial_module("COM3", available=True)
        with patch.dict(
            "sys.modules",
            {"serial": mock_mod, "serial.tools.list_ports": mock_mod.tools.list_ports},
        ):
            client = SerialClient("COM3", 9600)
        assert client is not None

    def test_raises_on_port_not_found(self):
        from arduino_client import SerialClient

        mock_mod, _ = self._mock_serial_module("COM3", available=False)
        with patch.dict(
            "sys.modules",
            {"serial": mock_mod, "serial.tools.list_ports": mock_mod.tools.list_ports},
        ):
            try:
                SerialClient("COM3", 9600)
                raise AssertionError("Should have raised ConnectionError")
            except ConnectionError as e:
                assert "COM3" in str(e)

    def test_raises_on_serial_exception_at_open(self):
        import serial as _serial

        from arduino_client import SerialClient

        mock_mod, _ = self._mock_serial_module("COM3", available=True)
        mock_mod.Serial.side_effect = _serial.SerialException("Access denied")
        with patch.dict(
            "sys.modules",
            {"serial": mock_mod, "serial.tools.list_ports": mock_mod.tools.list_ports},
        ):
            try:
                SerialClient("COM3", 9600)
                raise AssertionError("Should have raised ConnectionError")
            except ConnectionError as e:
                assert "Access denied" in str(e)

    def test_send_writes_command_with_newline(self):
        from arduino_client import SerialClient

        mock_mod, mock_instance = self._mock_serial_module("COM3", available=True)
        with patch.dict(
            "sys.modules",
            {"serial": mock_mod, "serial.tools.list_ports": mock_mod.tools.list_ports},
        ):
            client = SerialClient("COM3", 9600)
            client.send("Light")  # first send → LIGHT_ON
        mock_instance.write.assert_called_once_with(b"LIGHT_ON\n")

    def test_send_toggle_sends_correct_sequence(self):
        from arduino_client import SerialClient

        mock_mod, mock_instance = self._mock_serial_module("COM3", available=True)
        with patch.dict(
            "sys.modules",
            {"serial": mock_mod, "serial.tools.list_ports": mock_mod.tools.list_ports},
        ):
            client = SerialClient("COM3", 9600)
            client.send("Light")  # ON
            client.send("Light")  # OFF
        calls = [c.args[0] for c in mock_instance.write.call_args_list]
        assert b"LIGHT_ON\n" in calls
        assert b"LIGHT_OFF\n" in calls

    def test_send_failure_logs_error_does_not_raise(self):
        import serial as _serial

        from arduino_client import SerialClient

        mock_mod, mock_instance = self._mock_serial_module("COM3", available=True)
        mock_instance.write.side_effect = _serial.SerialException("Broken pipe")
        with patch.dict(
            "sys.modules",
            {"serial": mock_mod, "serial.tools.list_ports": mock_mod.tools.list_ports},
        ):
            client = SerialClient("COM3", 9600)
            client.send("Fan")  # should not raise

    def test_close_closes_serial_port(self):
        from arduino_client import SerialClient

        mock_mod, mock_instance = self._mock_serial_module("COM3", available=True)
        with patch.dict(
            "sys.modules",
            {"serial": mock_mod, "serial.tools.list_ports": mock_mod.tools.list_ports},
        ):
            client = SerialClient("COM3", 9600)
            client.close()
        mock_instance.close.assert_called_once()

    def test_all_commands_sent_correctly(self):
        from arduino_client import SerialClient

        mock_mod, mock_instance = self._mock_serial_module("COM3", available=True)
        with patch.dict(
            "sys.modules",
            {"serial": mock_mod, "serial.tools.list_ports": mock_mod.tools.list_ports},
        ):
            client = SerialClient("COM3", 9600)
            commands = ["Light", "Fan", "Alarm", "AC", "TV"]
            for cmd in commands:
                client.send(cmd)
        assert mock_instance.write.call_count == len(commands)


# ── WiFiClient ────────────────────────────────────────────────────────────────
class TestWiFiClient:
    def _mock_httpx(self, ping_status=200, ping_raises=None, post_raises=None):
        """Returns a mock httpx module."""
        import httpx as _httpx

        mock_httpx = MagicMock()
        mock_httpx.ConnectError = _httpx.ConnectError
        mock_httpx.TimeoutException = _httpx.TimeoutException

        if ping_raises:
            mock_httpx.get.side_effect = ping_raises
        else:
            mock_response = MagicMock()
            mock_response.status_code = ping_status
            mock_httpx.get.return_value = mock_response

        if post_raises:
            mock_httpx.post.side_effect = post_raises

        return mock_httpx

    def test_connects_successfully(self):
        from arduino_client import WiFiClient

        mock_httpx = self._mock_httpx(ping_status=200)
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            client = WiFiClient("192.168.1.100", 80)
        assert client is not None

    def test_raises_on_connect_error(self):
        import httpx as _httpx

        from arduino_client import WiFiClient

        mock_httpx = self._mock_httpx(ping_raises=_httpx.ConnectError("Connection refused"))
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            try:
                WiFiClient("192.168.1.100", 80)
                raise AssertionError("Should have raised ConnectionError")
            except ConnectionError as e:
                assert "192.168.1.100" in str(e)

    def test_raises_on_probe_timeout(self):
        import httpx as _httpx

        from arduino_client import WiFiClient

        mock_httpx = self._mock_httpx(ping_raises=_httpx.TimeoutException("Timed out"))
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            try:
                WiFiClient("192.168.1.100", 80)
                raise AssertionError("Should have raised ConnectionError")
            except ConnectionError as e:
                assert "timed out" in str(e).lower()

    def test_raises_on_non_200_probe(self):
        from arduino_client import WiFiClient

        mock_httpx = self._mock_httpx(ping_status=404)
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            try:
                WiFiClient("192.168.1.100", 80)
                raise AssertionError("Should have raised ConnectionError")
            except ConnectionError as e:
                assert "404" in str(e)

    def test_send_posts_correct_command(self):
        from arduino_client import WiFiClient

        mock_httpx = self._mock_httpx(ping_status=200)
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            client = WiFiClient("192.168.1.100", 80)
            client.send("Light")  # first → LIGHT_ON
        mock_httpx.post.assert_called_once_with(
            "http://192.168.1.100:80/command",
            json={"cmd": "LIGHT_ON"},
            timeout=2.0,
        )

    def test_send_toggle_posts_correct_sequence(self):
        from arduino_client import WiFiClient

        mock_httpx = self._mock_httpx(ping_status=200)
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            client = WiFiClient("192.168.1.100", 80)
            client.send("Fan")  # ON
            client.send("Fan")  # OFF
        calls = [c.kwargs["json"]["cmd"] for c in mock_httpx.post.call_args_list]
        assert "FAN_ON" in calls
        assert "FAN_OFF" in calls

    def test_send_timeout_does_not_raise(self):
        import httpx as _httpx

        from arduino_client import WiFiClient

        mock_httpx = self._mock_httpx(
            ping_status=200,
            post_raises=_httpx.TimeoutException("Timed out"),
        )
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            client = WiFiClient("192.168.1.100", 80)
            client.send("AC")  # should not raise

    def test_send_connect_error_does_not_raise(self):
        import httpx as _httpx

        from arduino_client import WiFiClient

        mock_httpx = self._mock_httpx(
            ping_status=200,
            post_raises=_httpx.ConnectError("Lost connection"),
        )
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            client = WiFiClient("192.168.1.100", 80)
            client.send("TV")  # should not raise

    def test_all_commands_posted_correctly(self):
        from arduino_client import WiFiClient

        mock_httpx = self._mock_httpx(ping_status=200)
        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            client = WiFiClient("192.168.1.100", 80)
            for cmd in ["Light", "Fan", "Alarm", "AC", "TV"]:
                client.send(cmd)
        assert mock_httpx.post.call_count == 5


# ── Factory — all modes ───────────────────────────────────────────────────────
class TestCreateArduinoClientAllModes:
    def test_serial_mode_returns_serial_client(self):
        import serial as _serial

        from arduino_client import SerialClient

        mock_port = type("Port", (), {"device": "COM3"})()
        mock_mod = MagicMock()
        mock_mod.tools.list_ports.comports.return_value = [mock_port]
        mock_mod.Serial.return_value = MagicMock(is_open=True)
        mock_mod.SerialException = _serial.SerialException

        with (
            patch.dict(
                "sys.modules",
                {"serial": mock_mod, "serial.tools.list_ports": mock_mod.tools.list_ports},
            ),
            patch.dict(
                os.environ,
                {"ARDUINO_MODE": "serial", "ARDUINO_PORT": "COM3", "ARDUINO_BAUD": "9600"},
            ),
        ):
            client = create_arduino_client()
        assert isinstance(client, SerialClient)

    def test_wifi_mode_returns_wifi_client(self):
        from arduino_client import WiFiClient

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_httpx = MagicMock()
        import httpx as _httpx

        mock_httpx.ConnectError = _httpx.ConnectError
        mock_httpx.TimeoutException = _httpx.TimeoutException
        mock_httpx.get.return_value = mock_response

        with (
            patch.dict("sys.modules", {"httpx": mock_httpx}),
            patch.dict(
                os.environ,
                {
                    "ARDUINO_MODE": "wifi",
                    "ARDUINO_HOST": "192.168.1.100",
                    "ARDUINO_HTTP_PORT": "80",
                },
            ),
        ):
            client = create_arduino_client()
        assert isinstance(client, WiFiClient)
