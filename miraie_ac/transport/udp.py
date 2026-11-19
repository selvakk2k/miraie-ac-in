"""Local UDP transport and protocol for Panasonic MirAIe ACs.

Reverse engineered and ported with canonical attribution to:
- DTechTerminal (https://github.com/dtechterminal/ha-panasonic-miraie-local) for port binding & TLV baseline.
- Panasonic MirAIe Android APK (com.panasonic.in.miraie) for UDPService specs.

Licensed under MIT.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from typing import Any, Callable, Coroutine

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_LOGGER = logging.getLogger(__name__)

# Protocol Constants
DISCOVERY_PORT: int = 5001
RECEIVE_PORT: int = 5002
BROADCAST_ADDRESS: str = "255.255.255.255"

DISCOVERY_REQ_PREFIX: str = "DISC_REQ_"
DISCOVERY_RSP_PREFIX: str = "DISC_RSP_"

TAG_DEVICE_ID: int = 0
TAG_COMMAND_CIPHERTEXT: int = 1
TAG_RESPONSE_CIPHERTEXT: int = 2

# MirAIe Controller Identifier: "ha" (Home Assistant baseline, customizable per command)
CONTROLLER_ID: str = "ha"


def build_tlv(tag: int, value: bytes) -> bytes:
    """Build a single TLV field: Tag (1 byte) + Length (2 bytes, big-endian) + Value."""
    return bytes([tag]) + len(value).to_bytes(2, "big") + value


def parse_tlv(wire: bytes) -> dict[int, bytes]:
    """Parse a TLV-encoded byte stream into a dictionary of {tag: value}."""
    fields: dict[int, bytes] = {}
    offset = 0
    wire_len = len(wire)
    while offset < wire_len:
        if offset + 3 > wire_len:
            raise ValueError(f"Truncated TLV header at offset {offset}")
        tag = wire[offset]
        length = int.from_bytes(wire[offset + 1 : offset + 3], "big")
        offset += 3
        if offset + length > wire_len:
            raise ValueError(
                f"Truncated TLV payload for tag {tag}: expected {length} bytes, have {wire_len - offset}"
            )
        fields[tag] = wire[offset : offset + length]
        offset += length
    return fields


def encrypt_aes_cbc_zero_padded(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    """Encrypt plaintext using AES-256-CBC with zero-byte padding to 16-byte boundary."""
    pad_len = -len(plaintext) % 16
    padded = plaintext + (b"\x00" * pad_len if pad_len != 0 else b"")
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def decrypt_aes_cbc_zero_padded(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    """Decrypt AES-256-CBC ciphertext and strip trailing null padding."""
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(ciphertext) + decryptor.finalize()
    return decrypted.rstrip(b"\x00")


class LocalUdpDeviceProtocol:
    """Encapsulates device-specific encryption, framing, and serialization."""

    def __init__(
        self,
        device_id: str,
        mac: str,
        key: str | bytes,
        iv: str | bytes,
        ip_address: str | None = None,
        multicast_ip: str | None = None,
    ) -> None:
        """Initialize the device protocol handler."""
        self.device_id = device_id
        self.mac = mac.replace(":", "").upper()
        self.key = bytes.fromhex(key) if isinstance(key, str) else key
        self.iv = bytes.fromhex(iv) if isinstance(iv, str) else iv
        self.ip_address = ip_address
        self.multicast_ip = multicast_ip
        self._dev_id_bytes = device_id.encode("ascii")
        self.last_seen: float = 0.0

    def encode_command(
        self,
        command_dict: dict[str, Any],
        sid: str = "1",
        controller: str = CONTROLLER_ID,
    ) -> bytes:
        """Encode a command dictionary into an encrypted TLV wire packet."""
        payload = {"ki": 1, "cnt": controller, "sid": sid, **command_dict}
        raw_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ciphertext = encrypt_aes_cbc_zero_padded(self.key, self.iv, raw_json)
        return (
            build_tlv(TAG_DEVICE_ID, self._dev_id_bytes)
            + build_tlv(TAG_COMMAND_CIPHERTEXT, ciphertext)
        )

    def decode_packet(self, wire: bytes) -> dict[str, Any]:
        """Decode and decrypt a TLV packet received from the AC."""
        fields = parse_tlv(wire)

        dev_id = fields.get(TAG_DEVICE_ID)
        if dev_id != self._dev_id_bytes:
            raise ValueError(
                f"Device ID mismatch in packet: expected {self.device_id}, got {dev_id!r}"
            )

        ciphertext = fields.get(TAG_RESPONSE_CIPHERTEXT) or fields.get(
            TAG_COMMAND_CIPHERTEXT
        )
        if not ciphertext:
            raise ValueError("No ciphertext found in TLV payload (tags 1 and 2 missing)")

        plaintext = decrypt_aes_cbc_zero_padded(self.key, self.iv, ciphertext)
        return json.loads(plaintext.decode("utf-8"))


class _AsyncUdpSocketProtocol(asyncio.DatagramProtocol):
    """Low-level asyncio datagram protocol for receiving UDP packets on port 5002."""

    def __init__(self, manager: LocalUdpTransportManager) -> None:
        self.manager = manager
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        _LOGGER.debug("UDP listener successfully bound on port %s", RECEIVE_PORT)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.manager.handle_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.warning("UDP listener encountered socket error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        _LOGGER.debug("UDP listener connection closed (error: %s)", exc)


class LocalUdpTransportManager:
    """Central socket manager running on port 5002 dispatching to multiple AC instances."""

    _instance: LocalUdpTransportManager | None = None

    def __init__(self) -> None:
        """Initialize the transport manager."""
        self._devices: dict[str, LocalUdpDeviceProtocol] = {}
        self._callbacks: dict[str, Callable[[dict[str, Any]], Coroutine[Any, Any, None] | None]] = {}
        self._pending_commands: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._device_locks: dict[str, asyncio.Lock] = {}
        self._pending_discoveries: dict[str, asyncio.Future[str]] = {}
        self._subnet_discovery_cb: Callable[[str, str], None] | None = None
        self._transport: asyncio.DatagramTransport | None = None
        self._lock = asyncio.Lock()
        self._is_running = False
        self._generation: int = 0
        self._cmd_seq: int = 0
        self._seen_device_ips: dict[str, str] = {}

    @classmethod
    def get_instance(cls) -> LocalUdpTransportManager:
        """Retrieve or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def is_running(self) -> bool:
        """Return True if the listener socket is open."""
        return self._is_running

    def get_seen_ip(self, device_id: str) -> str | None:
        """Return the most recently observed IP address for a device_id, if any."""
        return self._seen_device_ips.get(device_id)

    @property
    def seen_device_ips(self) -> dict[str, str]:
        """Return copy of all seen device IDs and their IP addresses."""
        return dict(self._seen_device_ips)

    async def start(self) -> None:
        """Start the central UDP receiver socket on port 5002."""
        async with self._lock:
            if self._is_running:
                return

            loop = asyncio.get_running_loop()

            # Create and configure the socket with SO_REUSEADDR and SO_BROADCAST
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            # Support SO_REUSEPORT on POSIX/Linux systems where available
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass

            sock.bind(("0.0.0.0", RECEIVE_PORT))
            sock.setblocking(False)

            transport, _ = await loop.create_datagram_endpoint(
                lambda: _AsyncUdpSocketProtocol(self),
                sock=sock,
            )
            self._transport = transport
            self._is_running = True
            self._generation += 1
            _LOGGER.info("MirAIe Local UDP Transport Manager started on port %s", RECEIVE_PORT)

    async def stop(self) -> None:
        """Close the central UDP socket and cancel pending futures."""
        async with self._lock:
            if not self._is_running:
                return

            if self._transport and not self._transport.is_closing():
                self._transport.close()

            self._transport = None
            self._is_running = False
            self._generation += 1

            # Cancel any pending command futures
            for future in self._pending_commands.values():
                if not future.done():
                    future.cancel()
            self._pending_commands.clear()

            # Cancel any pending discovery futures
            for future in self._pending_discoveries.values():
                if not future.done():
                    future.cancel()
            self._pending_discoveries.clear()

            _LOGGER.info("MirAIe Local UDP Transport Manager stopped")

    def register_device(
        self,
        device_id: str,
        mac: str,
        key: str | bytes,
        iv: str | bytes,
        ip_address: str | None = None,
        multicast_ip: str | None = None,
        callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None] | None] | None = None,
    ) -> LocalUdpDeviceProtocol:
        """Register a device with the transport manager."""
        if not ip_address and device_id in self._seen_device_ips:
            ip_address = self._seen_device_ips[device_id]
        protocol = LocalUdpDeviceProtocol(device_id, mac, key, iv, ip_address, multicast_ip)
        self._devices[device_id] = protocol
        if ip_address:
            self._seen_device_ips[device_id] = ip_address
        if callback:
            self._callbacks[device_id] = callback
        _LOGGER.debug("Registered AC device %s (MAC: %s, IP: %s)", device_id, mac, ip_address)
        return protocol

    def unregister_device(self, device_id: str) -> None:
        """Unregister a device."""
        self._devices.pop(device_id, None)
        self._callbacks.pop(device_id, None)
        _LOGGER.debug("Unregistered AC device %s", device_id)

    def set_device_ip(self, device_id: str, ip_address: str) -> None:
        """Update the known IP address for a registered device."""
        self._seen_device_ips[device_id] = ip_address
        if device_id in self._devices:
            self._devices[device_id].ip_address = ip_address
            _LOGGER.debug("Updated IP for device %s -> %s", device_id, ip_address)

    async def discover(self, mac: str, timeout: float = 3.0) -> str | None:
        """Broadcast DISC_REQ_<MAC> and await DISC_RSP_<MAC> to locate device IP."""
        if not self._is_running:
            await self.start()

        clean_mac = mac.replace(":", "").upper()
        disc_req = f"{DISCOVERY_REQ_PREFIX}{clean_mac}".encode("ascii")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending_discoveries[clean_mac] = future

        try:
            if self._transport:
                # Send discovery packet to subnet broadcast address
                self._transport.sendto(disc_req, (BROADCAST_ADDRESS, DISCOVERY_PORT))
                _LOGGER.debug("Broadcasted %s to %s:%s", disc_req.decode(), BROADCAST_ADDRESS, DISCOVERY_PORT)

            resolved_ip = await asyncio.wait_for(future, timeout=timeout)
            return resolved_ip
        except asyncio.TimeoutError:
            _LOGGER.debug("UDP discovery timed out for MAC %s", clean_mac)
            return None
        finally:
            self._pending_discoveries.pop(clean_mac, None)

    async def discover_subnet(self, timeout: float = 3.0) -> list[dict[str, str]]:
        """Broadcast generic discovery probe DISC and collect responding AC IP and MACs."""
        if not self._is_running:
            await self.start()

        disc_req = b"DISC"
        discovered: dict[str, str] = {}

        # Temporarily record discovery responses
        def _on_discovered(mac: str, ip: str) -> None:
            discovered[mac] = ip

        self._subnet_discovery_cb = _on_discovered

        try:
            if self._transport:
                self._transport.sendto(disc_req, (BROADCAST_ADDRESS, DISCOVERY_PORT))
                _LOGGER.debug("Broadcasted subnet probe DISC to %s:%s", BROADCAST_ADDRESS, DISCOVERY_PORT)
            await asyncio.sleep(timeout)
        finally:
            self._subnet_discovery_cb = None

        return [{"mac": mac, "ip": ip} for mac, ip in discovered.items()]

    async def send_command(
        self,
        device_id: str,
        command_dict: dict[str, Any],
        sid: str | None = None,
        timeout: float = 3.5,
        controller: str | None = None,
    ) -> dict[str, Any]:
        """Send an encrypted command to an AC and await its matching ACK on port 5002."""
        if not self._is_running:
            await self.start()

        gen = self._generation
        lock = self._device_locks.setdefault(device_id, asyncio.Lock())
        async with lock:
            if not self._is_running or self._generation != gen:
                raise ConnectionError(f"Transport stopped while command for {device_id} was queued")

            protocol = self._devices.get(device_id)
            if not protocol:
                raise ValueError(f"Device {device_id} is not registered with UDP transport")

            if not protocol.ip_address:
                # Attempt opportunistic discovery first
                resolved_ip = await self.discover(protocol.mac, timeout=2.0)
                if resolved_ip:
                    protocol.ip_address = resolved_ip
                    self._seen_device_ips[device_id] = resolved_ip
                else:
                    raise ConnectionError(f"No IP address available for device {device_id}")

            self._cmd_seq += 1
            assigned_sid = sid if sid is not None else f"ha_{self._cmd_seq}"

            wire_bytes = protocol.encode_command(command_dict, sid=assigned_sid, controller=controller or CONTROLLER_ID)

            loop = asyncio.get_running_loop()
            cmd_key = f"{device_id}:{assigned_sid}"
            future: asyncio.Future[dict[str, Any]] = loop.create_future()
            self._pending_commands[cmd_key] = future

            try:
                if self._transport:
                    self._transport.sendto(wire_bytes, (protocol.ip_address, DISCOVERY_PORT))
                    _LOGGER.debug("Sent UDP command to %s:%s (sid=%s)", protocol.ip_address, DISCOVERY_PORT, assigned_sid)

                response = await asyncio.wait_for(future, timeout=timeout)
                return response
            finally:
                if self._pending_commands.get(cmd_key) is future:
                    self._pending_commands.pop(cmd_key, None)

    def handle_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        """Handle raw incoming UDP packets on port 5002."""
        sender_ip, sender_port = addr

        # 1. Handle Discovery Response: DISC_RSP_<MAC>
        if data.startswith(DISCOVERY_RSP_PREFIX.encode("ascii")):
            rsp_str = data.decode("ascii", errors="ignore").strip()
            mac_part = rsp_str[len(DISCOVERY_RSP_PREFIX) :]
            _LOGGER.debug("Received discovery response from %s for MAC %s", sender_ip, mac_part)

            # Update known device IP
            for dev in self._devices.values():
                if dev.mac == mac_part:
                    dev.ip_address = sender_ip
                    self._seen_device_ips[dev.device_id] = sender_ip

            # Fulfill pending discovery future
            if mac_part in self._pending_discoveries and not self._pending_discoveries[mac_part].done():
                self._pending_discoveries[mac_part].set_result(sender_ip)

            if getattr(self, "_subnet_discovery_cb", None):
                self._subnet_discovery_cb(mac_part, sender_ip)
            return

        # 2. Handle TLV Encrypted Status/ACK packet
        if not data.startswith(bytes([TAG_DEVICE_ID])):
            _LOGGER.debug("Ignoring non-MirAIe packet from %s (missing Tag 0)", sender_ip)
            return

        try:
            fields = parse_tlv(data)
            dev_id_bytes = fields.get(TAG_DEVICE_ID)
            if not dev_id_bytes:
                return

            dev_id_str = dev_id_bytes.decode("ascii", errors="ignore")
            protocol = self._devices.get(dev_id_str)
            if not protocol:
                _LOGGER.debug("Received packet for unregistered device %s from %s", dev_id_str, sender_ip)
                return

            self._seen_device_ips[dev_id_str] = sender_ip

            # Keep device IP updated
            protocol.ip_address = sender_ip

            # Decrypt payload
            status = protocol.decode_packet(data)
            _LOGGER.debug("Decrypted UDP packet from %s: %s", dev_id_str, status)

            # Record last UDP packet reception timestamp for reachability tracking
            protocol.last_seen = time.time()

            # Fulfill command matching response sid
            rsp_sid = status.get("sid")
            if rsp_sid:
                exact_key = f"{dev_id_str}:{rsp_sid}"
                fut = self._pending_commands.pop(exact_key, None)
                if fut and not fut.done():
                    fut.set_result(status)

            # Dispatch status update to registered device callback
            callback = self._callbacks.get(dev_id_str)
            if callback:
                res = callback(status)
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)

        except Exception as err:
            _LOGGER.debug("Failed to parse/decrypt packet from %s: %s", sender_ip, err)

