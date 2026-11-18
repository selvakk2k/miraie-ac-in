"""Unit tests for miraie-ac CLI command-line interface."""
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from miraie_ac.cli import (
    build_parser,
    cmd_auth,
    cmd_control,
    cmd_devices,
    cmd_discover,
    cmd_dump_keys,
    cmd_ping,
)


class TestCli(unittest.IsolatedAsyncioTestCase):
    """Test suite for CLI commands."""

    def test_parser_subcommands(self):
        """Test parser defines all required subcommands."""
        parser = build_parser()
        subcommands = ["auth", "devices", "dump-keys", "discover", "ping", "control", "listen"]
        choices = parser._subparsers._actions[1].choices
        for sub in subcommands:
            self.assertIn(sub, choices)

    async def test_cmd_auth_success(self):
        """Test cmd_auth successfully returns JSON user info."""
        parser = build_parser()
        args = parser.parse_args(["auth", "--user", "test@example.com", "--password", "secret"])

        mock_hub = AsyncMock()
        mock_hub._authenticate = AsyncMock()
        mock_hub.user = MagicMock(user_id="user_123", access_token="tok", refresh_token="ref", expires_in=3600)
        mock_hub.close = AsyncMock()

        with patch("miraie_ac.cli.MirAIeHub", return_value=mock_hub), patch("sys.stdout") as mock_stdout:
            exit_code = await cmd_auth(args)
            self.assertEqual(exit_code, 0)
            mock_hub._authenticate.assert_awaited_once_with("test@example.com", "secret")

    async def test_cmd_dump_keys_success(self):
        """Test cmd_dump_keys exports local keys in clean JSON."""
        parser = build_parser()
        args = parser.parse_args(["dump-keys", "--user", "test@example.com", "--password", "secret"])

        dev = MagicMock(
            id="dev_1",
            friendly_name="Living Room AC",
            supports_local_udp=True,
            details=MagicMock(
                model_number="CS-CU-18",
                mac_address="00:11:22:33:44:55",
                udp_control_key="aes_key",
                udp_control_iv="aes_iv",
                multicast_ip="239.255.255.250",
            ),
        )
        mock_hub = AsyncMock()
        mock_hub.init = AsyncMock()
        mock_hub.home = MagicMock(devices=[dev])
        mock_hub.close = AsyncMock()

        with patch("miraie_ac.cli.MirAIeHub", return_value=mock_hub), patch("sys.stdout") as mock_stdout:
            exit_code = await cmd_dump_keys(args)
            self.assertEqual(exit_code, 0)
            mock_hub.init.assert_awaited_once_with("test@example.com", "secret", broker=None)

    async def test_cmd_ping_success(self):
        """Test cmd_ping computes packet latency."""
        parser = build_parser()
        args = parser.parse_args([
            "ping",
            "--ip", "192.168.0.100",
            "--device-id", "dev_1",
            "--key", "key",
            "--iv", "iv",
            "--count", "1",
        ])

        mock_transport = AsyncMock()
        mock_transport.start = AsyncMock()
        mock_transport.register_device = MagicMock()
        mock_transport.send_command = AsyncMock(return_value={"ps": "on"})
        mock_transport.stop = AsyncMock()

        with patch("miraie_ac.cli.LocalUdpTransportManager", return_value=mock_transport), patch("sys.stdout"):
            exit_code = await cmd_ping(args)
            self.assertEqual(exit_code, 0)
            mock_transport.send_command.assert_awaited_once_with("dev_1", {"ps": "query"}, timeout=2.0)

    async def test_cmd_control_udp_success(self):
        """Test cmd_control sends expected command dictionary over UDP."""
        parser = build_parser()
        args = parser.parse_args([
            "control",
            "--transport", "udp",
            "--ip", "192.168.0.100",
            "--device-id", "dev_1",
            "--key", "key",
            "--iv", "iv",
            "--power", "on",
            "--temp", "24",
            "--mode", "cool",
        ])

        mock_transport = AsyncMock()
        mock_transport.start = AsyncMock()
        mock_transport.register_device = MagicMock()
        mock_transport.send_command = AsyncMock(return_value={"ps": "on", "actmp": "24", "acmd": "cool"})
        mock_transport.stop = AsyncMock()

        with patch("miraie_ac.cli.LocalUdpTransportManager", return_value=mock_transport), patch("sys.stdout"):
            exit_code = await cmd_control(args)
            self.assertEqual(exit_code, 0)
            mock_transport.send_command.assert_awaited_once_with(
                "dev_1",
                {"ps": "on", "actmp": "24", "acmd": "cool"},
            )


if __name__ == "__main__":
    unittest.main()
