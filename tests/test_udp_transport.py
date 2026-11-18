"""Unit tests for MirAIe AC Local UDP Transport and Protocol."""

from __future__ import annotations

import asyncio
import json
import pytest

from miraie_ac.device import Device, DeviceDetails, DeviceStatus
from miraie_ac.enums import (
    ConvertiMode,
    DisplayMode,
    FanMode,
    HVACMode,
    PowerMode,
    PresetMode,
    SwingMode,
)
from miraie_ac.transport.udp import (
    CONTROLLER_ID,
    DISCOVERY_REQ_PREFIX,
    DISCOVERY_RSP_PREFIX,
    TAG_COMMAND_CIPHERTEXT,
    TAG_DEVICE_ID,
    TAG_RESPONSE_CIPHERTEXT,
    LocalUdpDeviceProtocol,
    LocalUdpTransportManager,
    build_tlv,
    decrypt_aes_cbc_zero_padded,
    encrypt_aes_cbc_zero_padded,
    parse_tlv,
)

# Test vectors
TEST_KEY = b"\x01" * 32
TEST_IV = b"\x02" * 16
TEST_DEV_ID = "8c53afca6252"
TEST_MAC = "90:E5:B1:F0:9F:0D"
CLEAN_MAC = "90E5B1F09F0D"


def test_build_and_parse_tlv():
    """Verify TLV building and parsing roundtrip."""
    f0 = b"device_123"
    f1 = b"some_ciphertext_payload"
    wire = build_tlv(0, f0) + build_tlv(1, f1)

    fields = parse_tlv(wire)
    assert fields[0] == f0
    assert fields[1] == f1


def test_parse_tlv_errors():
    """Verify parsing malformed TLV raises ValueError."""
    # Truncated header
    with pytest.raises(ValueError, match="Truncated TLV header"):
        parse_tlv(b"\x00\x01")

    # Truncated payload
    with pytest.raises(ValueError, match="Truncated TLV payload"):
        parse_tlv(b"\x00\x00\x05abc")


def test_encrypt_decrypt_zero_padded():
    """Verify AES-256-CBC encryption and decryption with zero-byte padding."""
    plaintext = b'{"ps":"on","actmp":"24.0"}'
    ciphertext = encrypt_aes_cbc_zero_padded(TEST_KEY, TEST_IV, plaintext)

    # Must be multiple of 16 bytes
    assert len(ciphertext) % 16 == 0
    assert ciphertext != plaintext

    decrypted = decrypt_aes_cbc_zero_padded(TEST_KEY, TEST_IV, ciphertext)
    assert decrypted == plaintext


def test_device_protocol_encode_decode():
    """Verify LocalUdpDeviceProtocol encodes command and decodes response."""
    proto = LocalUdpDeviceProtocol(TEST_DEV_ID, TEST_MAC, TEST_KEY, TEST_IV)

    # 1. Encode command
    wire_cmd = proto.encode_command({"ps": "on", "actmp": "25.0"}, sid="test_1")
    fields = parse_tlv(wire_cmd)

    assert fields[TAG_DEVICE_ID] == TEST_DEV_ID.encode("ascii")
    assert TAG_COMMAND_CIPHERTEXT in fields

    raw_json = decrypt_aes_cbc_zero_padded(TEST_KEY, TEST_IV, fields[TAG_COMMAND_CIPHERTEXT])
    payload = json.loads(raw_json)
    assert payload["ki"] == 1
    assert payload["cnt"] == CONTROLLER_ID
    assert payload["sid"] == "test_1"
    assert payload["ps"] == "on"
    assert payload["actmp"] == "25.0"

    # 2. Decode response (AC returns Tag 0 + Tag 2)
    resp_payload = {"ps": "on", "actmp": "25.0", "sid": "test_1", "cnt": "ha"}
    resp_cipher = encrypt_aes_cbc_zero_padded(
        TEST_KEY, TEST_IV, json.dumps(resp_payload).encode("utf-8")
    )
    wire_resp = build_tlv(TAG_DEVICE_ID, TEST_DEV_ID.encode("ascii")) + build_tlv(
        TAG_RESPONSE_CIPHERTEXT, resp_cipher
    )

    decoded = proto.decode_packet(wire_resp)
    assert decoded["ps"] == "on"
    assert decoded["sid"] == "test_1"
    assert decoded["cnt"] == "ha"


def test_device_protocol_mismatch_device_id():
    """Verify decode_packet raises ValueError when Tag 0 does not match device_id."""
    proto = LocalUdpDeviceProtocol(TEST_DEV_ID, TEST_MAC, TEST_KEY, TEST_IV)
    resp_cipher = encrypt_aes_cbc_zero_padded(TEST_KEY, TEST_IV, b'{"ps":"on"}')
    wire_wrong_id = build_tlv(TAG_DEVICE_ID, b"different_id") + build_tlv(
        TAG_RESPONSE_CIPHERTEXT, resp_cipher
    )

    with pytest.raises(ValueError, match="Device ID mismatch"):
        proto.decode_packet(wire_wrong_id)


def test_device_dynamic_capabilities():
    """Verify dynamic capability evaluation for temperature step, swing, convertible, etc."""
    device = Device(
        id=TEST_DEV_ID,
        name="Test AC",
        friendly_name="Living Room AC",
        control_topic="control",
        status_topic="status",
        connection_status_topic="conn",
        broker=None,
    )

    details = DeviceDetails(
        model_name="Panasonic Smart AC",
        mac_address=TEST_MAC,
        category="AC",
        brand="PANASONIC",
        firmware_version="3.02",
        serial_number="12345",
        model_number="CS-CU-EU18CKY5XFM",
        product_serial_number="67890",
        udp_control_key=TEST_KEY.hex(),
        udp_control_iv=TEST_IV.hex(),
    )
    device.set_details(details)

    # Initial state without capabilities (safe defaults)
    assert device.target_temperature_step == 1.0
    assert device.supports_horizontal_swing is True
    assert device.supports_night_display is False
    assert device.supports_local_udp is True
    assert device.convertible_profile_name == "Converti8"

    # Set capabilities with 0.5 step, no horizontal swing, night display, Converti7
    capabilities = {
        "controlCapabilities": [
            {
                "capability": "ACTemperature",
                "attributes": [
                    {
                        "key": "actmp",
                        "range": {
                            "increment": [{"unit": "celcius", "value": 0.5}]
                        },
                    }
                ],
            },
            {
                "capability": "DisplayControl",
                "attributes": [
                    {"key": "acdc", "values": ["on", "night", "off"]}
                ],
            },
            {
                "capability": "Convertible",
                "attributes": [
                    {"key": "cnv", "intValues": [100, 90, 80, 70, 60, 50, 40, 0]}
                ],
            },
            {"capability": "NanoeX"},
            {"capability": "BuzzerControl"},
        ]
    }
    device.set_model_capabilities(capabilities)

    assert device.target_temperature_step == 0.5
    assert device.supports_horizontal_swing is False
    assert device.supports_night_display is True
    assert device.supports_nanoe is True
    assert device.supports_buzzer is True
    assert device.convertible_profile_name == "Converti7"
    assert 110 not in device.supported_convertible_modes
    assert device.supported_convertible_modes[100] == "100%"


@pytest.mark.asyncio
async def test_udp_transport_manager_datagram_handling():
    """Verify transport manager handles datagrams for discovery and status."""
    manager = LocalUdpTransportManager()
    callback_called = False
    received_status = {}

    def status_cb(status):
        nonlocal callback_called, received_status
        callback_called = True
        received_status = status

    manager.register_device(
        device_id=TEST_DEV_ID,
        mac=TEST_MAC,
        key=TEST_KEY.hex(),
        iv=TEST_IV.hex(),
        ip_address="192.168.1.100",
        callback=status_cb,
    )

    # 1. Test discovery packet handling
    loop = asyncio.get_running_loop()
    disc_future = loop.create_future()
    manager._pending_discoveries[CLEAN_MAC] = disc_future

    disc_data = f"{DISCOVERY_RSP_PREFIX}{CLEAN_MAC}".encode("ascii")
    manager.handle_datagram(disc_data, ("192.168.1.150", 5001))

    assert disc_future.done()
    assert disc_future.result() == "192.168.1.150"
    assert manager._devices[TEST_DEV_ID].ip_address == "192.168.1.150"

    # 2. Test encrypted status packet handling
    resp_payload = {"ps": "on", "actmp": "24.0", "sid": "cmd_123", "cnt": "ha"}
    cipher = encrypt_aes_cbc_zero_padded(
        TEST_KEY, TEST_IV, json.dumps(resp_payload).encode("utf-8")
    )
    wire = build_tlv(TAG_DEVICE_ID, TEST_DEV_ID.encode("ascii")) + build_tlv(
        TAG_RESPONSE_CIPHERTEXT, cipher
    )

    cmd_future = loop.create_future()
    manager._pending_commands[f"{TEST_DEV_ID}:cmd_123"] = cmd_future

    manager.handle_datagram(wire, ("192.168.1.150", 5001))

    assert cmd_future.done()
    assert cmd_future.result()["ps"] == "on"
    assert callback_called is True
    assert received_status["actmp"] == "24.0"

