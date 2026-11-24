"""Thermodynamic Inverter Power & Energy Estimator for Panasonic MirAIe ACs.

Estimates real-time power (W) and cumulative energy consumption (kWh) locally
for offline/UDP connected units using BEE star-label certified parameters and
inverter modulation thermodynamics.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .utils import parse_room_temp


@dataclass
class ACInverterProfile:
    """Electrical and thermal characteristics of an AC model."""

    rated_cooling_w: float = 5100.0  # Certified full cooling capacity (e.g. 1.45 Ton = 5100W)
    iseer: float = 5.80  # Certified ISEER (Wh/Wh)
    rated_power_w: float = 1480.0  # Full-load electrical input power (W)
    half_power_w: float = 480.0  # 50% load electrical input power (W)
    min_power_w: float = 290.0  # Minimum inverter modulation compressor floor (W)
    standby_power_w: float = 2.5  # Standby power with MCU & Wi-Fi active (W)

    # Fan speed electrical consumption (W)
    fan_power_map: Dict[str, float] = None

    def __post_init__(self):
        if self.fan_power_map is None:
            self.fan_power_map = {
                "quiet": 12.0,
                "low": 18.0,
                "medium": 24.0,
                "high": 32.0,
                "powerful": 38.0,
                "auto": 20.0,
            }


class InverterPowerEstimator:
    """Estimates instantaneous power (W) and integrates cumulative energy (kWh)."""

    def __init__(
        self,
        profile: Optional[ACInverterProfile] = None,
        pull_down_delta_span: float = 2.5,
    ) -> None:
        """Initialize the estimator.

        :param profile: Certified profile or default 1.5-ton 5-star inverter profile.
        :param pull_down_delta_span: Temperature delta (°C) across which compressor ramps to maximum.
        """
        self.profile = profile or ACInverterProfile()
        self.pull_down_delta_span = max(1.0, pull_down_delta_span)

        self._last_timestamp: Optional[float] = None
        self._cumulative_energy_kwh: float = 0.0
        self._last_power_w: float = 0.0

    @property
    def cumulative_energy_kwh(self) -> float:
        """Return the accumulated energy in kWh."""
        return self._cumulative_energy_kwh

    @property
    def last_power_w(self) -> float:
        """Return the most recently calculated instantaneous power in Watts."""
        return self._last_power_w

    def reset_energy(self, initial_kwh: float = 0.0) -> None:
        """Reset the energy accumulator."""
        self._cumulative_energy_kwh = initial_kwh
        self._last_timestamp = None

    def calculate_power(
        self,
        payload: Dict[str, Any],
        external_room_temp: Optional[float] = None,
        firmware_version: str = "3.02",
        compressor_hz: Optional[float] = None,
        outdoor_temp_c: Optional[float] = None,
    ) -> float:
        """Calculate the instantaneous electrical power in Watts from AC telemetry.

        :param payload: Telemetry dict from UDP frame or MQTT status.
        :param external_room_temp: Optional high-accuracy room temperature from Zigbee/BLE sensor.
        :param firmware_version: AC firmware version for rmtmp parsing.
        :param compressor_hz: Optional hardware compressor frequency in Hz from .../diag (tag 0xB1).
        :param outdoor_temp_c: Optional outdoor condenser temperature in °C from .../diag (tag 0xB3).
        :return: Instantaneous power in Watts.
        """
        power_state = str(payload.get("ps", "off")).lower()
        if power_state != "on":
            return self.profile.standby_power_w

        mode = str(payload.get("acmd", "cool")).lower()
        fan_speed = str(payload.get("acfs", "auto")).lower()
        fan_power = self.profile.fan_power_map.get(fan_speed, 20.0)

        # In fan-only mode, only the indoor blower runs
        if mode == "fan":
            return self.profile.standby_power_w + fan_power

        # Converti limit (e.g. cnv = 80, 55, 40 or 0 for 100%)
        cnv_val = payload.get("cnv", 0)
        try:
            cnv_pct = float(cnv_val)
        except (ValueError, TypeError):
            cnv_pct = 0.0

        if cnv_pct > 0 and cnv_pct <= 100:
            converti_factor = cnv_pct / 100.0
        else:
            converti_factor = 1.0

        comp_max_w = self.profile.rated_power_w * converti_factor
        comp_min_w = self.profile.min_power_w

        # --- MODEL A: Physical Compressor Frequency available from .../diag (0xB1) ---
        if compressor_hz is not None:
            if compressor_hz <= 0:
                comp_power = 0.0
            else:
                # Rated frequency is ~90-100Hz at full load, min floor is ~20Hz
                # Linear electrical mapping with outdoor temperature thermodynamic penalty
                hz_ratio = min(1.0, max(0.0, (compressor_hz - 15.0) / 75.0))
                base_comp_w = comp_min_w + (comp_max_w - comp_min_w) * hz_ratio
                
                # Outdoor condenser temperature coefficient:
                # Laboratory standard is 35°C. For every 1°C below 35°C, head pressure drops ~1.2%
                if outdoor_temp_c is not None:
                    outdoor_factor = 1.0 + (outdoor_temp_c - 35.0) * 0.012
                    base_comp_w *= max(0.7, min(1.25, outdoor_factor))
                
                comp_power = base_comp_w

            total_power = self.profile.standby_power_w + fan_power + comp_power
            return round(total_power, 1)

        # --- MODEL B: Thermodynamic Delta Inverter Model (Local UDP / Offline) ---
        # Determine target temperature
        try:
            target_temp = float(payload.get("actmp", 24.0))
        except (ValueError, TypeError):
            target_temp = 24.0

        # Determine current room temperature
        if external_room_temp is not None:
            room_temp = float(external_room_temp)
        else:
            raw_rmtmp = payload.get("rmtmp")
            room_temp = parse_room_temp(raw_rmtmp, firmware_version=firmware_version)
            if room_temp is None:
                room_temp = target_temp  # Fallback to neutral delta

        # Thermal Delta: delta_t > 0 means room is warmer than target (needs cooling)
        delta_t = room_temp - target_temp

        if delta_t <= -1.0:
            # Overcooled (room is more than 1°C below target): compressor shuts off or idles
            comp_power = comp_min_w * 0.5
        elif delta_t <= 0.0:
            # Reached target temperature: run at minimum inverter floor
            comp_power = comp_min_w
        else:
            # Modulate between minimum floor and maximum capacity
            ratio = min(1.0, delta_t / self.pull_down_delta_span)
            comp_power = comp_min_w + (comp_max_w - comp_min_w) * (ratio ** 1.1)

        total_power = self.profile.standby_power_w + fan_power + comp_power
        return round(total_power, 1)

    def process_telemetry(
        self,
        payload: Dict[str, Any],
        timestamp: Optional[float] = None,
        external_room_temp: Optional[float] = None,
        firmware_version: str = "3.02",
    ) -> float:
        """Calculate power and integrate energy across time elapsed since last call.

        :param payload: Telemetry status dict.
        :param timestamp: Unix timestamp of the telemetry event.
        :param external_room_temp: Optional external sensor temperature.
        :param firmware_version: AC firmware version.
        :return: Instantaneous power in Watts.
        """
        current_time = timestamp if timestamp is not None else time.time()
        power_w = self.calculate_power(
            payload,
            external_room_temp=external_room_temp,
            firmware_version=firmware_version,
        )

        if self._last_timestamp is not None and current_time > self._last_timestamp:
            dt_seconds = current_time - self._last_timestamp
            # Trapezoidal integration for smooth energy estimation
            avg_power = (self._last_power_w + power_w) / 2.0
            energy_kwh = (avg_power * dt_seconds) / (1000.0 * 3600.0)
            self._cumulative_energy_kwh += energy_kwh

        self._last_timestamp = current_time
        self._last_power_w = power_w
        return power_w
