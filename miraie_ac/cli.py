"""Command-line interface (CLI) for controlling Panasonic MirAIe Air Conditioners."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import sys
import time
from typing import Any, Optional

from miraie_ac.broker import MirAIeBroker
from miraie_ac.enums import (
    ConvertiMode,
    DisplayMode,
    FanMode,
    HVACMode,
    PowerMode,
    PresetMode,
    SwingMode,
)
from miraie_ac.hub import MirAIeHub
from miraie_ac.transport.udp import LocalUdpTransportManager

LOGGER = logging.getLogger("miraie_ac.cli")


def _get_input(prompt: str, secret: bool = False) -> str:
    if secret:
        return getpass.getpass(prompt)
    print(prompt, end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().strip()


async def cmd_auth(args: argparse.Namespace) -> int:
    username = args.user or os.getenv("MIRAIE_USER") or _get_input("Username (Mobile/Email): ")
    password = args.password or os.getenv("MIRAIE_PASSWORD") or _get_input("Password: ", secret=True)

    hub = MirAIeHub()
    try:
        await hub._authenticate(username, password)
        user = hub.user
        data = {
            "status": "success",
            "userId": getattr(user, "user_id", ""),
            "accessToken": getattr(user, "access_token", ""),
            "refreshToken": getattr(user, "refresh_token", ""),
            "expiresIn": getattr(user, "expires_in", 0),
        }
        print(json.dumps(data, indent=2))
        return 0
    except Exception as err:
        print(json.dumps({"status": "error", "message": str(err)}, indent=2), file=sys.stderr)
        return 1
    finally:
        await hub.close()


async def cmd_devices(args: argparse.Namespace) -> int:
    username = args.user or os.getenv("MIRAIE_USER") or _get_input("Username (Mobile/Email): ")
    password = args.password or os.getenv("MIRAIE_PASSWORD") or _get_input("Password: ", secret=True)

    hub = MirAIeHub()
    try:
        await hub.init(username, password, broker=None)
        devices = []
        for dev in getattr(getattr(hub, "home", None), "devices", []):
            dt = getattr(dev, "details", None)
            devices.append({
                "id": dev.id,
                "friendlyName": dev.friendly_name,
                "modelNumber": getattr(dt, "model_number", ""),
                "macAddress": getattr(dt, "mac_address", ""),
                "firmwareVersion": getattr(dt, "firmware_version", ""),
                "supportsLocalUdp": dev.supports_local_udp,
                "tempStep": dev.target_temperature_step,
                "supportsHorizontalSwing": dev.supports_horizontal_swing,
                "supportsNightDisplay": dev.supports_night_display,
                "convertibleProfile": dev.convertible_profile_name,
                "supportsNanoe": dev.supports_nanoe,
                "supportsBuzzer": dev.supports_buzzer,
            })
        print(json.dumps(devices, indent=2))
        return 0
    except Exception as err:
        print(json.dumps({"status": "error", "message": str(err)}, indent=2), file=sys.stderr)
        return 1
    finally:
        await hub.close()


async def cmd_dump_keys(args: argparse.Namespace) -> int:
    username = args.user or os.getenv("MIRAIE_USER") or _get_input("Username (Mobile/Email): ")
    password = args.password or os.getenv("MIRAIE_PASSWORD") or _get_input("Password: ", secret=True)

    hub = MirAIeHub()
    try:
        await hub.init(username, password, broker=None)
        keys_list = []
        for dev in getattr(getattr(hub, "home", None), "devices", []):
            dt = getattr(dev, "details", None)
            keys_list.append({
                "friendlyName": dev.friendly_name,
                "deviceId": dev.id,
                "modelNumber": getattr(dt, "model_number", ""),
                "macAddress": getattr(dt, "mac_address", ""),
                "udpControlKey": getattr(dt, "udp_control_key", None),
                "udpControlIv": getattr(dt, "udp_control_iv", None),
                "multicastIp": getattr(dt, "multicast_ip", None),
                "supportsLocalUdp": dev.supports_local_udp,
            })
        print(json.dumps(keys_list, indent=2))
        return 0
    except Exception as err:
        print(json.dumps({"status": "error", "message": str(err)}, indent=2), file=sys.stderr)
        return 1
    finally:
        await hub.close()


async def cmd_discover(args: argparse.Namespace) -> int:
    timeout = getattr(args, "timeout", 3.0) or 3.0
    transport = LocalUdpTransportManager()
    await transport.start()
    print(f"Discovering Panasonic ACs on local subnet (timeout: {timeout}s)...", file=sys.stderr)

    try:
        results = await transport.discover_subnet(timeout=timeout)
        print(json.dumps(results, indent=2))
        return 0
    finally:
        await transport.stop()


async def cmd_ping(args: argparse.Namespace) -> int:
    ip = args.ip or os.getenv("MIRAIE_IP")
    device_id = args.device_id or os.getenv("MIRAIE_DEVICE_ID")
    key = args.key or os.getenv("MIRAIE_KEY")
    iv = args.iv or os.getenv("MIRAIE_IV")
    count = getattr(args, "count", 3) or 3

    if not all([ip, device_id, key, iv]):
        print("Error: --ip, --device-id, --key, and --iv are all required for local UDP ping (or set via MIRAIE_IP, MIRAIE_DEVICE_ID, MIRAIE_KEY, MIRAIE_IV env vars).", file=sys.stderr)
        return 1

    transport = LocalUdpTransportManager()
    await transport.start()

    latencies = []
    try:
        transport.register_device(
            device_id=device_id,
            mac="00:00:00:00:00:00",
            key=key,
            iv=iv,
            ip_address=ip,
        )

        print(f"Pinging {device_id} at {ip}:5001 ({count} packets)...", file=sys.stderr)
        for seq in range(1, count + 1):
            t_start = time.perf_counter()
            try:
                res = await transport.send_command(device_id, {"ps": "query"}, timeout=2.0)
                elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
                latencies.append(elapsed_ms)
                print(f"Reply from {ip}: seq={seq} time={elapsed_ms}ms")
            except Exception as err:
                print(f"Request timeout from {ip}: seq={seq} ({err})")
            if seq < count:
                await asyncio.sleep(0.5)

        if latencies:
            summary = {
                "status": "success",
                "device_id": device_id,
                "ip": ip,
                "packets_sent": count,
                "packets_received": len(latencies),
                "min_ms": min(latencies),
                "avg_ms": round(sum(latencies) / len(latencies), 2),
                "max_ms": max(latencies),
            }
            print(json.dumps(summary, indent=2))
            return 0
        else:
            print(json.dumps({"status": "failed", "device_id": device_id, "ip": ip}, indent=2), file=sys.stderr)
            return 1
    finally:
        await transport.stop()


async def cmd_control(args: argparse.Namespace) -> int:
    transport_type = args.transport.lower()

    cmd_dict: dict[str, Any] = {}
    if args.power:
        cmd_dict["ps"] = "on" if args.power.lower() == "on" else "off"
    if args.temp is not None:
        t_val = int(args.temp) if float(args.temp).is_integer() else args.temp
        cmd_dict["actmp"] = str(t_val)
    if args.mode:
        cmd_dict["acmd"] = args.mode.lower()
    if args.fan:
        cmd_dict["acfs"] = args.fan.lower()
    if args.swing_v is not None:
        cmd_dict["acvs"] = 0 if args.swing_v.lower() == "auto" else int(args.swing_v)
    if args.swing_h is not None:
        cmd_dict["achs"] = 0 if args.swing_h.lower() == "auto" else int(args.swing_h)
    if args.preset:
        preset = args.preset.lower()
        if preset == "eco":
            cmd_dict.update({"acpm": "off", "acem": "on", "acec": "off"})
        elif preset == "boost":
            cmd_dict.update({"acpm": "on", "acem": "off", "acec": "off"})
        elif preset == "clean":
            cmd_dict.update({"acpm": "off", "acem": "off", "acec": "on"})
        elif preset == "none":
            cmd_dict.update({"acpm": "off", "acem": "off", "acec": "off"})
    if args.converti is not None:
        cmd_dict["cnv"] = int(args.converti)
        cmd_dict.update({"acem": "off", "acpm": "off"})
    if args.nanoe:
        cmd_dict["acng"] = "on" if args.nanoe.lower() == "on" else "off"
    if args.buzzer:
        cmd_dict["bzr"] = "on" if args.buzzer.lower() == "on" else "off"
    if args.display:
        cmd_dict["acdc"] = args.display.lower()

    if not cmd_dict:
        print("Error: No control attributes specified to send.", file=sys.stderr)
        return 1

    if transport_type == "udp":
        ip = args.ip or os.getenv("MIRAIE_IP")
        device_id = args.device_id or os.getenv("MIRAIE_DEVICE_ID")
        key = args.key or os.getenv("MIRAIE_KEY")
        iv = args.iv or os.getenv("MIRAIE_IV")

        if not all([ip, device_id, key, iv]):
            print("Error: Local UDP requires --ip, --device-id, --key, and --iv.", file=sys.stderr)
            return 1

        transport = LocalUdpTransportManager()
        await transport.start()
        try:
            transport.register_device(
                device_id=device_id,
                mac="00:00:00:00:00:00",
                key=key,
                iv=iv,
                ip_address=ip,
            )
            t_start = time.perf_counter()
            res = await transport.send_command(device_id, cmd_dict)
            elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
            print(json.dumps({"status": "success", "transport": "udp", "latency_ms": elapsed_ms, "result": res}, indent=2))
            return 0
        except Exception as err:
            print(json.dumps({"status": "error", "transport": "udp", "message": str(err)}, indent=2), file=sys.stderr)
            return 1
        finally:
            await transport.stop()

    elif transport_type == "cloud":
        username = args.user or os.getenv("MIRAIE_USER") or _get_input("Username: ")
        password = args.password or os.getenv("MIRAIE_PASSWORD") or _get_input("Password: ", secret=True)
        device_id = args.device_id or os.getenv("MIRAIE_DEVICE_ID")

        if not device_id:
            print("Error: Cloud control requires --device-id.", file=sys.stderr)
            return 1

        hub = MirAIeHub()
        broker = MirAIeBroker()
        try:
            await hub.init(username, password, broker)
            target = next((d for d in getattr(getattr(hub, "home", None), "devices", []) if d.id == device_id), None)
            if not target:
                print(f"Error: Device {device_id} not found in MirAIe account.", file=sys.stderr)
                return 1

            t_start = time.perf_counter()
            # Send through broker
            if "ps" in cmd_dict:
                await target.turn_on(transport="cloud") if cmd_dict["ps"] == "on" else await target.turn_off(transport="cloud")
            if "actmp" in cmd_dict:
                await target.set_temperature(float(cmd_dict["actmp"]), transport="cloud")
            if "acmd" in cmd_dict:
                await target.set_hvac_mode(HVACMode(cmd_dict["acmd"]), transport="cloud")
            if "acfs" in cmd_dict:
                await target.set_fan_mode(FanMode(cmd_dict["acfs"]), transport="cloud")
            if "acvs" in cmd_dict:
                await target.set_v_swing_mode(SwingMode(cmd_dict["acvs"]), transport="cloud")
            if "achs" in cmd_dict:
                await target.set_h_swing_mode(SwingMode(cmd_dict["achs"]), transport="cloud")
            if "acdc" in cmd_dict:
                await target.set_display_mode(DisplayMode(cmd_dict["acdc"]), transport="cloud")
            if "cnv" in cmd_dict:
                await target.set_converti_mode(ConvertiMode(cmd_dict["cnv"]), transport="cloud")
            if "bzr" in cmd_dict:
                await target.set_buzzer(cmd_dict["bzr"] == "on", transport="cloud")
            if "acng" in cmd_dict:
                await target.set_nanoe(cmd_dict["acng"] == "on", transport="cloud")
            if args.preset:
                preset_val = PresetMode(args.preset.lower())
                await target.set_preset_mode(preset_val, transport="cloud")

            elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
            print(json.dumps({"status": "success", "transport": "cloud", "latency_ms": elapsed_ms}, indent=2))
            return 0
        except Exception as err:
            print(json.dumps({"status": "error", "transport": "cloud", "message": str(err)}, indent=2), file=sys.stderr)
            return 1
        finally:
            await hub.close()
    else:
        print(f"Error: Unsupported transport '{transport_type}'. Use 'udp' or 'cloud'.", file=sys.stderr)
        return 1


async def cmd_listen(args: argparse.Namespace) -> int:
    transport_type = args.transport.lower()

    if transport_type == "udp":
        device_id = args.device_id or os.getenv("MIRAIE_DEVICE_ID")
        key = args.key or os.getenv("MIRAIE_KEY")
        iv = args.iv or os.getenv("MIRAIE_IV")

        if not all([device_id, key, iv]):
            print("Error: UDP listener requires --device-id, --key, and --iv to decrypt telemetry frames.", file=sys.stderr)
            return 1

        transport = LocalUdpTransportManager()
        await transport.start()
        print(f"Listening for incoming UDP frames for {device_id} on 0.0.0.0:5002 (Ctrl+C to stop)...", file=sys.stderr)

        def _packet_listener(payload: dict):
            print(json.dumps({"transport": "udp", "timestamp": time.time(), "payload": payload}), flush=True)

        try:
            transport.register_device(
                device_id=device_id,
                mac="00:00:00:00:00:00",
                key=key,
                iv=iv,
                callback=_packet_listener,
            )
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\nStopping UDP listener.", file=sys.stderr)
        finally:
            await transport.stop()
        return 0

    elif transport_type == "cloud":
        username = args.user or os.getenv("MIRAIE_USER") or _get_input("Username: ")
        password = args.password or os.getenv("MIRAIE_PASSWORD") or _get_input("Password: ", secret=True)

        hub = MirAIeHub()
        broker = MirAIeBroker()
        try:
            await hub.init(username, password, broker)
            print("Listening to MirAIe Cloud MQTT topics (Ctrl+C to stop)...", file=sys.stderr)

            for dev in getattr(getattr(hub, "home", None), "devices", []):
                def _make_cb(device):
                    return lambda: print(json.dumps({
                        "transport": "cloud",
                        "deviceId": device.id,
                        "friendlyName": device.friendly_name,
                        "timestamp": time.time(),
                        "power": getattr(device.status, "power_mode", None).value if getattr(device.status, "power_mode", None) else None,
                        "temp": getattr(device.status, "temperature", None),
                        "roomTemp": getattr(device.status, "room_temperature", None),
                        "mode": getattr(device.status, "hvac_mode", None).value if getattr(device.status, "hvac_mode", None) else None,
                    }), flush=True)
                dev.register_callback(_make_cb(dev))

            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\nStopping MQTT listener.", file=sys.stderr)
        finally:
            await hub.close()
        return 0
    else:
        print(f"Error: Unsupported transport '{transport_type}'. Use 'udp' or 'cloud'.", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="miraie-ac",
        description="CLI client for Panasonic MirAIe Air Conditioners (Local UDP & Cloud MQTT)",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # auth
    p_auth = subparsers.add_parser("auth", help="Authenticate with MirAIe cloud account")
    p_auth.add_argument("--user", help="Mobile number (+91XXXXXXXXXX) or email")
    p_auth.add_argument("--password", help="Account password")

    # devices
    p_devs = subparsers.add_parser("devices", help="List registered ACs and their capabilities")
    p_devs.add_argument("--user", help="Mobile number or email")
    p_devs.add_argument("--password", help="Account password")

    # dump-keys
    p_keys = subparsers.add_parser("dump-keys", help="Export local UDP encryption keys and MACs in JSON")
    p_keys.add_argument("--user", help="Mobile number or email")
    p_keys.add_argument("--password", help="Account password")

    # discover
    p_disc = subparsers.add_parser("discover", help="Discover Panasonic ACs on the local network via UDP")
    p_disc.add_argument("--timeout", type=float, default=3.0, help="Discovery timeout in seconds")

    # ping
    p_ping = subparsers.add_parser("ping", help="Ping a local AC on UDP port 5001 and measure latency")
    p_ping.add_argument("--ip", help="AC LAN IP address (or MIRAIE_IP)")
    p_ping.add_argument("--device-id", help="AC Device ID (or MIRAIE_DEVICE_ID)")
    p_ping.add_argument("--key", help="UDP AES-256 Key (or MIRAIE_KEY)")
    p_ping.add_argument("--iv", help="UDP AES-256 IV (or MIRAIE_IV)")
    p_ping.add_argument("--count", type=int, default=3, help="Number of packets to send (default: 3)")

    # control
    p_ctrl = subparsers.add_parser("control", help="Send command to AC via Local UDP or Cloud MQTT")
    p_ctrl.add_argument("--transport", default="udp", choices=["udp", "cloud"], help="Transport mode (default: udp)")
    p_ctrl.add_argument("--device-id", help="Device ID")
    p_ctrl.add_argument("--ip", help="AC LAN IP address (UDP)")
    p_ctrl.add_argument("--key", help="UDP AES Key (UDP)")
    p_ctrl.add_argument("--iv", help="UDP AES IV (UDP)")
    p_ctrl.add_argument("--user", help="Cloud user (Cloud)")
    p_ctrl.add_argument("--password", help="Cloud password (Cloud)")
    # Action options
    p_ctrl.add_argument("--power", choices=["on", "off"], help="Power mode")
    p_ctrl.add_argument("--temp", type=float, help="Target temperature (°C)")
    p_ctrl.add_argument("--mode", choices=["cool", "fan", "dry", "auto", "heat"], help="HVAC mode")
    p_ctrl.add_argument("--fan", choices=["auto", "low", "medium", "high", "quiet"], help="Fan speed")
    p_ctrl.add_argument("--swing-v", choices=["auto", "0", "1", "2", "3", "4", "5"], help="Vertical swing (auto or 0-5)")
    p_ctrl.add_argument("--swing-h", choices=["auto", "0", "1", "2", "3", "4", "5"], help="Horizontal swing (auto or 0-5)")
    p_ctrl.add_argument("--preset", choices=["none", "eco", "boost", "clean"], help="Preset mode")
    p_ctrl.add_argument("--converti", type=int, choices=[0, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 100, 105, 110], help="Convertible capacity (0, 40-110)")
    p_ctrl.add_argument("--nanoe", choices=["on", "off"], help="Nanoe purification")
    p_ctrl.add_argument("--buzzer", choices=["on", "off"], help="Buzzer sound")
    p_ctrl.add_argument("--display", choices=["on", "off", "night"], help="Display mode")

    # listen
    p_list = subparsers.add_parser("listen", help="Stream live telemetry from UDP socket or Cloud MQTT")
    p_list.add_argument("--transport", default="udp", choices=["udp", "cloud"], help="Transport mode (default: udp)")
    p_list.add_argument("--device-id", help="Device ID")
    p_list.add_argument("--key", help="UDP AES Key")
    p_list.add_argument("--iv", help="UDP AES IV")
    p_list.add_argument("--user", help="Cloud user")
    p_list.add_argument("--password", help="Cloud password")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "auth": cmd_auth,
        "devices": cmd_devices,
        "dump-keys": cmd_dump_keys,
        "discover": cmd_discover,
        "ping": cmd_ping,
        "control": cmd_control,
        "listen": cmd_listen,
    }
    handler = handlers.get(args.subcommand)
    if not handler:
        sys.exit(1)

    try:
        exit_code = asyncio.run(handler(args))
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()

