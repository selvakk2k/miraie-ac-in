# Write a function check if the given string is a valid email address.

import re


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