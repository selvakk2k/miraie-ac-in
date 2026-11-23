# MirAIe AC API Client (`miraie-ac-in`)

<p align="center">
  <a href="https://pypi.org/project/miraie-ac-in/"><img src="https://img.shields.io/pypi/v/miraie-ac-in?style=flat-square" alt="PyPI version"></a>
  <a href="https://pypi.org/project/miraie-ac-in/"><img src="https://img.shields.io/pypi/pyversions/miraie-ac-in?style=flat-square" alt="Python Version"></a>
  <a href="https://github.com/selvakk2k/miraie-ac-in/releases"><img src="https://img.shields.io/github/v/release/selvakk2k/miraie-ac-in?style=flat-square" alt="Release"></a>
  <a href="https://github.com/selvakk2k"><img src="https://img.shields.io/badge/AI%20Assisted-Antigravity%20%7C%20Claude%20%7C%20CodeRabbit-blueviolet?style=flat-square" alt="AI-Assisted"></a>
  <a href="https://aiattribution.github.io/interpret-attribution"><img src="https://img.shields.io/badge/AI%20Attribution-AIA%20PAI%20Nc%20Hin-orange?style=flat-square" alt="AI Attribution"></a>
</p>

A Python client library and CLI tool for controlling Panasonic MirAIe smart air conditioners. It supports direct local UDP control on LAN, cloud MQTT communication, dynamic hardware capability lookup, and telemetry decoding.

> [!IMPORTANT]
> This library is designed exclusively for Panasonic Air Conditioners that use the **MirAIe** application. It is not compatible with models that use Panasonic Comfort Cloud.

---

## Table of Contents

* [Features](#features)
* [Installation](#installation)
* [Python API Quick Start](#python-api-quick-start)
* [Command-Line Interface (CLI)](#command-line-interface-cli)
* [My Python Libraries](#my-python-libraries)
* [Credits & License](#credits--license)

---

## Features

* **Local UDP Control**: Direct local network control on UDP ports 5001 and 5002 using AES-256-CBC encryption, providing fast command execution (under 30 ms) without requiring internet access.
* **Automatic Cloud Fallback**: Automatically falls back to cloud MQTT if a local UDP command is dropped or the device is unreachable on LAN.
* **Dynamic Hardware Capabilities**: Automatically queries official model capabilities (0.5°C vs 1.0°C temperature steps, motorized horizontal swing, night display mode, convertible capacity percentages, Nanoe air purification, and buzzer controls).
* **Firmware 3.02+ Temperature Support**: Decodes high-resolution ambient room temperature reported by newer firmware versions, while retaining standard decoding for older firmware.
* **Convertible Capacity Modes**: Supports 4-in-1, 7-in-1, and 8-in-1 compressor profiles (40% to 110%).
* **Telemetry & Diagnostics**: Parses power state, fan speed, swing modes, Nanoe status, filter cleaning alerts, Wi-Fi RSSI signal strength, error codes, and control sources.
* **Resource Lifecycle Management**: Provides an async `close()` method to clean up network sockets, background tasks, and HTTP client sessions.

---

## Installation

Install the package via `pip`:

```bash
pip install miraie-ac-in
```

---

## Python API Quick Start

```python
import asyncio
from miraie_ac import MirAIeHub, MirAIeBroker
from miraie_ac.enums import SwingMode, PresetMode, ConvertiMode, DisplayMode

async def main():
    broker = MirAIeBroker()
    hub = MirAIeHub()

    # Sign in using your registered mobile number or email
    await hub.init("+919999999999", "your_password", broker)
    
    # Inspect discovered devices and dynamically fetched capabilities
    for device in hub.home.devices:
        print(f"Device: {device.friendly_name} (ID: {device.id})")
        print(f"  Model: {getattr(device.details, 'model_number', 'Unknown')}")
        print(f"  Local UDP Supported: {device.supports_local_udp}")
        print(f"  Temp Step: {device.target_temperature_step}°C")
        print(f"  Horizontal Swing: {device.supports_horizontal_swing}")
        print(f"  Nanoe Air Purification: {device.supports_nanoe}")
        print(f"  Buzzer Control: {device.supports_buzzer}")
        print(f"  Convertible Modes: {device.supported_convertible_modes}")

    # Control a device using verified capabilities
    if hub.home.devices:
        device = hub.home.devices[0]
        
        # Basic controls
        await device.turn_on()
        await device.set_temperature(24.0)
        
        # Horizontal swing (if supported by hardware)
        if device.supports_horizontal_swing:
            await device.set_horizontal_swing(SwingMode.AUTO)

        # Nanoe air purification (if supported by hardware)
        if device.supports_nanoe:
            await device.set_nanoe(True)

        # Convertible capacity limit (e.g. 70% or 80%)
        if device.supports_convertible:
            await device.set_converti_mode(ConvertiMode.C70)

        # Display mode (e.g. night mode or off)
        if device.supports_night_display:
            await device.set_display_mode(DisplayMode.NIGHT)

    # Clean up network sessions and tasks
    await hub.close()

asyncio.run(main())
```

---

## Command-Line Interface (CLI)

`miraie-ac-in` includes a command-line tool `miraie-ac` for managing, testing, and controlling your ACs.

### Account Authentication & Discovery

```bash
# Verify cloud credentials
miraie-ac auth --user +919999999999

# List registered ACs and print their verified hardware capabilities
miraie-ac devices --user +919999999999

# Export local UDP encryption keys, IVs, MAC addresses, and device IDs in JSON
miraie-ac dump-keys --user +919999999999

# Discover Panasonic ACs broadcasting on the local network via UDP
miraie-ac discover
```

### Local Network Diagnostics

```bash
# Ping an AC on UDP port 5001 and measure round-trip latency
miraie-ac ping --ip 192.168.1.100 --device-id 0123456789ab --key "$MIRAIE_KEY" --iv "$MIRAIE_IV"

# Stream live telemetry from the local UDP socket (port 5002)
miraie-ac listen --transport udp --device-id 0123456789ab --key "$MIRAIE_KEY" --iv "$MIRAIE_IV"

# Stream live telemetry from Cloud MQTT
miraie-ac listen --transport cloud --user +919999999999 --device-id 0123456789ab
```

### Device Control

Send commands over **Local UDP** (offline, fast) or **Cloud MQTT**:

```bash
# Basic Local UDP control (Power, Temperature, Mode)
miraie-ac control --transport udp --ip 192.168.1.100 --device-id 0123456789ab \
  --key "$MIRAIE_KEY" --iv "$MIRAIE_IV" --power on --temp 24 --mode cool

# Advanced feature control via Local UDP (Nanoe, Convertible Capacity, Horizontal Swing)
miraie-ac control --transport udp --ip 192.168.1.100 --device-id 0123456789ab \
  --key "$MIRAIE_KEY" --iv "$MIRAIE_IV" \
  --nanoe on --converti 70 --swing-h auto --display night --buzzer off

# Basic Cloud MQTT control
miraie-ac control --transport cloud --user +919999999999 --device-id 0123456789ab --power off

# Preset mode via Cloud MQTT (Eco, Boost, Clean)
miraie-ac control --transport cloud --user +919999999999 --device-id 0123456789ab --preset eco
```

---

## My Python Libraries

| Library | PyPI Package | Description | Status |
| :--- | :--- | :--- | :--- |
| [Panasonic AC Models](https://github.com/selvakk2k/panasonic-ac-models) | `panasonic-ac-models` | Hardware profiles, capability lookup & IR protocol generator for Indian Panasonic ACs | `Stable` |
| [IFB Washer Models](https://github.com/selvakk2k/ifb-washer-models) | `ifb-washer-models` | Unified hardware database, model lookup & cycle capability gating for IFB smart washers | `Stable` |
| [MirAIe AC API Client](https://github.com/selvakk2k/miraie-ac-in) | `miraie-ac-in` | Async MQTT & REST API client for Panasonic MirAIe-connected Air Conditioners | `Stable` |
| [Qubopy](https://github.com/selvakk2k/qubopy) | `qubopy` | Async client & CLI for Hero Qubo cameras, smart locks, air purifiers & smart home devices | `Stable` |
| [PyAtomberg](https://github.com/selvakk2k/pyatomberg) | `pyatomberg` | Async client for Atomberg smart locks (BLE/Wi-Fi), ceiling fans & home appliances | `Stable` |

---

## Credits & License

### Contributors & Protocol Research
* **Protocol Research & Initial Library**: [@rkzofficial](https://github.com/rkzofficial), [@deCodeIt](https://github.com/deCodeIt), [@gutpull](https://github.com/gutpull), and [@ankitduseja](https://github.com/ankitduseja).
* **Local UDP Protocol**: [@dtechterminal](https://github.com/dtechterminal) ([ha-panasonic-miraie-local](https://github.com/dtechterminal/ha-panasonic-miraie-local)) for the initial reverse engineering of the local AES-256-CBC UDP protocol.

### Maintenance & Development
* **Lead Architecture & Hardware Testing**: [@selvakk2k](https://github.com/selvakk2k) — physical device telemetry capture, protocol analysis, and hardware verification.
* **Code Implementation & Engineering**: **Antigravity** (Google DeepMind) — asyncio local UDP transport, dynamic capabilities, resource management, and test suite.
* **Automated Code Review**: **CodeRabbit** ([@coderabbitai](https://github.com/coderabbitai)) — automated pull request and pre-commit code reviews.
* **Code Review & Auditing**: **Claude** (Anthropic) — code auditing and verification.

Licensed under the **MIT License**. See the [LICENSE](LICENSE) file for details.
