# Write a function check if the given string is a valid email address.

import re
from typing import Any


def is_valid_email(addr):
    if re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", addr):
        return True
    else:
        return False


def toFloat(value: str) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_room_temp(value: object, firmware_version: str = "") -> float | None:
    """Parse rmtmp, accounting for sub-degree high-resolution format in firmware 3.02+."""
    if value is None:
        return None

    if isinstance(value, (int, float)):
        if value <= 50.0:
            return float(value)

    val_str = str(value).strip()
    if not val_str:
        return None

    try:
        cleaned_version = "".join(c for c in firmware_version if c.isdigit() or c == '.')
        major, minor = cleaned_version.split('.')[:2]
        is_packed_firmware = (int(major), int(minor)) >= (3, 2)
    except (ValueError, AttributeError):
        is_packed_firmware = False

    if is_packed_firmware and '.' in val_str:
        parts = val_str.split('.')
        if len(parts) == 2:
            prefix_str, suffix_str = parts[0], parts[1]
            try:
                prefix_val = int(prefix_str)
                suffix_val = int(suffix_str)

                # Check for zero/uninitialized temperature like "00.0"
                if prefix_val == 0 and suffix_val == 0:
                    return None

                # Normal decimal temperature (e.g. "23.0", "24.5", "23.5", "24.0", "25.2"):
                # - Prefix is in ambient room temp range (10 <= prefix <= 50) and not zero-padded (e.g. not "00", "01")
                # - Suffix is either 1 digit (e.g. "0", "5", "2") or standard decimal "00"/"50", or not an ambient temp
                is_normal_ambient = (
                    10 <= prefix_val <= 50
                    and not (len(prefix_str) > 1 and prefix_str.startswith('0'))
                    and (len(suffix_str) == 1 or suffix_str in ("00", "50") or not (10 <= suffix_val <= 50))
                )

                if is_normal_ambient:
                    return toFloat(val_str)

                # Packed high-resolution format (e.g. "128.28", "193.29", "61.26", "01.28", "00.24"):
                # Suffix is the integer temperature (10 <= suffix <= 50).
                # Prefix is the fractional byte (prefix / 256.0).
                if 10 <= suffix_val <= 50:
                    return round(float(suffix_val) + (prefix_val / 256.0), 2)
            except (ValueError, IndexError):
                return toFloat(val_str)

    return toFloat(val_str)


def decode_diagnostic_frame(b64_payload: str) -> dict[str, Any]:
    """Decode binary Base64 TLV diagnostic frame from the .../diag MQTT topic.

    Extracts internal microcontroller registers:
    - 0xB2: Indoor evaporator coil temperature in °C
    - 0xB4: Indoor fan speed stage
    - 0xC2: Compressor frequency in Hz
    - 0xC3: Outdoor condenser temperature in °C
    - 0xC5: Compressor / AC current in A (raw centi-Amps / 100)
    - 0xC9: AC mains voltage in V
    - 0xF2: Diagnostic error code (e.g. 'H00' = normal)
    """
    import base64

    try:
        raw = base64.b64decode(b64_payload)
    except Exception:
        return {}

    result: dict[str, Any] = {}
    i = 0
    raw_len = len(raw)

    while i < raw_len:
        tag = raw[i]
        # 1-byte value tags
        if tag in (0xB1, 0xB2, 0xB3, 0xB4, 0xB7, 0xC1, 0xC2, 0xC3, 0xC4, 0xC6, 0xCA, 0xE1, 0xE2, 0xE3, 0xF1):
            if i + 1 < raw_len:
                val = raw[i + 1]
                if tag == 0xB2:
                    result["evaporator_coil_temp_c"] = val
                elif tag == 0xB4:
                    result["fan_stage"] = val
                elif tag == 0xC2:
                    result["compressor_frequency_hz"] = val
                elif tag == 0xC3:
                    result["outdoor_condenser_temp_c"] = val
                result[f"0x{tag:02X}"] = val
            i += 2
        # 2-byte signed value tags
        elif tag in (0xB5, 0xB6, 0xC5, 0xC7, 0xC8, 0xC9, 0xF5, 0xF6):
            if i + 2 < raw_len:
                val = int.from_bytes(raw[i + 1 : i + 3], "big", signed=True)
                if tag == 0xC5:
                    result["current_a"] = round(val / 100.0, 2)
                elif tag == 0xC9:
                    result["voltage_v"] = val
                result[f"0x{tag:02X}"] = val
            i += 3
        # Error code string (4 bytes, e.g. b'\xf2H00')
        elif tag == 0xF2:
            if i + 3 < raw_len:
                code_str = raw[i + 1 : i + 4].decode("ascii", errors="ignore").strip()
                result["error_code"] = code_str
            i += 4
        # 4-byte value tags
        elif tag == 0xF3:
            if i + 4 < raw_len:
                result["0xF3"] = raw[i + 1 : i + 5].hex()
            i += 5
        else:
            i += 1

    return result
