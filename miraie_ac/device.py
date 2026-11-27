from __future__ import annotations

from typing import Any, Callable, Dict, Optional
from .broker import MirAIeBroker
from .energy import ACInverterProfile, InverterPowerEstimator
from .enums import PowerMode, FanMode, SwingMode, DisplayMode, HVACMode, PresetMode, ConvertiMode
from .utils import toFloat, parse_room_temp, decode_diagnostic_frame
from .logger import LOGGER


class DeviceStatus:
    def __init__(
        self,
        is_online: bool,
        temperature: float,
        room_temperature: float,
        power_mode: PowerMode,
        fan_mode: FanMode,
        v_swing_mode: SwingMode,
        h_swing_mode: SwingMode,
        display_mode: DisplayMode,
        hvac_mode: HVACMode,
        preset_mode: PresetMode,
        converti_mode: ConvertiMode,
        nanoe_mode: str = "off",
        filter_clean_alert: bool = False,
        wifi_signal: int = 0,
        control_source: str = "an",
        buzzer: bool = False,
        error_code: str = "",
        warning_code: str = "",
        compressor_frequency_hz: Optional[int] = None,
        evaporator_coil_temp_c: Optional[int] = None,
        outdoor_condenser_temp_c: Optional[int] = None,
        fan_stage: Optional[int] = None,
        voltage_v: Optional[int] = None,
        current_a: Optional[float] = None,
        diag_error_code: str = "",
        estimated_power_w: Optional[float] = None,
        estimated_energy_kwh: float = 0.0,
    ):
        self.is_online = is_online
        self.temperature = temperature
        self.room_temperature = room_temperature
        self.power_mode = power_mode
        self.fan_mode = fan_mode
        self.v_swing_mode = v_swing_mode
        self.h_swing_mode = h_swing_mode
        self.display_mode = display_mode
        self.hvac_mode = hvac_mode
        self.preset_mode = preset_mode
        self.converti_mode = converti_mode
        self.nanoe_mode = nanoe_mode
        self.filter_clean_alert = filter_clean_alert
        self.buzzer = buzzer
        self.error_code = error_code
        self.warning_code = warning_code
        self.wifi_signal = wifi_signal
        self.control_source = control_source
        self.compressor_frequency_hz = compressor_frequency_hz
        self.evaporator_coil_temp_c = evaporator_coil_temp_c
        self.outdoor_condenser_temp_c = outdoor_condenser_temp_c
        self.fan_stage = fan_stage
        self.voltage_v = voltage_v
        self.current_a = current_a
        self.diag_error_code = diag_error_code
        self.estimated_power_w = estimated_power_w
        self.estimated_energy_kwh = estimated_energy_kwh
        LOGGER.debug(f"Device status: {self.__str__()}")
    
    def __str__(self):
        return (
            f"Is online? - {self.is_online}\n" +
            f"Temperature: {self.temperature}\n" +
            f"Room temperature: {self.room_temperature}\n" +
            f"Power mode: {self.power_mode}\n" +
            f"Fan mode: {self.fan_mode}\n" +
            f"Vertical swing mode: {self.v_swing_mode}\n" +
            f"Horizontal swing mode: {self.h_swing_mode}\n" +
            f"Display mode: {self.display_mode}\n" +
            f"Hvac mode: {self.hvac_mode}\n" +
            f"Preset mode: {self.preset_mode}\n" +
            f"Converti mode: {self.converti_mode}\n" +
            f"Nanoe mode: {self.nanoe_mode}\n" +
            f"Filter clean alert: {self.filter_clean_alert}\n" +
            f"Buzzer: {self.buzzer}\n" +
            f"Error code: {self.error_code}\n" +
            f"Warning code: {self.warning_code}\n" +
            f"Wifi signal: {self.wifi_signal}\n" +
            f"Control source: {self.control_source}\n"
        )
        
    def __repr__(self):
        return self.__str__()

class DeviceDetails:
    def __init__(
        self,
        model_name,
        mac_address,
        category,
        brand,
        firmware_version,
        serial_number,
        model_number,
        product_serial_number,
        udp_control_key: Optional[str] = None,
        udp_control_iv: Optional[str] = None,
        multicast_ip: Optional[str] = None,
    ):
        self.model_name = model_name
        self.mac_address = mac_address
        self.category = category
        self.brand = brand
        self.firmware_version = firmware_version
        self.serial_number = serial_number
        self.model_number = model_number
        self.product_serial_number = product_serial_number
        self.udp_control_key = udp_control_key
        self.udp_control_iv = udp_control_iv
        self.multicast_ip = multicast_ip
        LOGGER.debug(f"Device details: {self.__str__()}")
        
    def __str__(self):
        return (
            f"Brand: {self.brand}\n" +
            f"Category: {self.category}\n" +
            f"Model Name: {self.model_name}\n" +
            f"Model #: {self.model_number}\n" +
            f"MAC address: {self.mac_address}\n" +
            f"Firmware version: {self.firmware_version}\n" +
            f"Serial number: {self.serial_number}\n" +
            f"Model number: {self.model_number}\n" +
            f"Product serial number: {self.product_serial_number}\n" +
            f"UDP Control Key: {'configured' if self.udp_control_key else 'none'}\n"
        )
    
    def __repr__(self):
        return self.__str__()

class Device:
    def __init__(
        self,
        id: str,
        name: str,
        friendly_name: str,
        control_topic: str,
        status_topic: str,
        connection_status_topic: str,
        broker: MirAIeBroker,
    ):
        self.id = id
        self.name = name
        self.friendly_name = friendly_name
        self.control_topic = control_topic
        self.status_topic = status_topic
        self.connection_status_topic = connection_status_topic
        self.diag_topic = f"{control_topic.rsplit('/', 1)[0]}/diag" if control_topic else ""
        self.broker = broker
        self.details = None
        self.model_capabilities: dict = {}
        self.udp_transport = None
        self.use_local_udp: bool = False
        self.controller_id: str = "ha"
        self._last_status_ts: int = 0
        self._last_status_payload: Optional[dict] = None
        self._estimator = InverterPowerEstimator()

        self._callbacks = set()
        if self.broker:
            self.broker.register_device_callback(self.status_topic, self.status_handler)
            self.broker.register_device_callback(
                self.connection_status_topic, self.connection_status_handler
            )
            if self.diag_topic:
                self.broker.register_device_callback(self.diag_topic, self.diag_handler)

    def close(self):
        if self.broker:
            self.broker.remove_device_callback(self.status_topic)
            self.broker.remove_device_callback(self.connection_status_topic)
            if self.diag_topic:
                self.broker.remove_device_callback(self.diag_topic)
        if self.udp_transport:
            self.udp_transport.unregister_device(self.id)
        
    def __str__(self):
        return (
            f"Id: {self.id}\n" +
            f"Name: {self.name}\n" +
            f"Friendly name: {self.friendly_name}\n" +
            f"Control topic: {self.control_topic}\n" +
            f"Status topic: {self.status_topic}\n" +
            f"Connection status topic: {self.connection_status_topic}\n"
        )
        
    def __repr__(self):
        return self.__str__()

    def refresh(self):
        for callback in list(self._callbacks):
            try:
                callback()
            except Exception as err:
                LOGGER.exception("Error in device callback: %s", err)

    def register_callback(self, callback: Callable[[], None]) -> None:
        """Register callback, called when Roller changes state."""
        self._callbacks.add(callback)

    def remove_callback(self, callback: Callable[[], None]) -> None:
        """Remove previously registered callback."""
        self._callbacks.discard(callback)

    def status_handler(self, status: any):
        LOGGER.debug(f"Raw device status: {status}")
        cur = getattr(self, "status", None)
        firmware_version = getattr(getattr(self, "details", None), "firmware_version", "")

        if "onlineStatus" in status:
            is_online = status["onlineStatus"] == "true"
        else:
            is_online = True

        if "actmp" in status:
            try:
                temp = toFloat(status["actmp"])
            except (ValueError, TypeError):
                temp = cur.temperature if cur else 24.0
        else:
            temp = cur.temperature if cur else 24.0

        if "rmtmp" in status:
            rm_temp = parse_room_temp(status["rmtmp"], firmware_version)
        else:
            rm_temp = cur.room_temperature if cur else 24.0

        if "ps" in status:
            power_mode = PowerMode(status["ps"])
        else:
            power_mode = cur.power_mode if cur else PowerMode.OFF

        if "acfs" in status:
            fan_mode = FanMode(status["acfs"])
        else:
            fan_mode = cur.fan_mode if cur else FanMode.AUTO

        if "acvs" in status:
            try:
                v_swing = SwingMode(int(status["acvs"]))
            except (ValueError, TypeError, KeyError):
                v_swing = cur.v_swing_mode if cur else SwingMode.AUTO
        else:
            v_swing = cur.v_swing_mode if cur else SwingMode.AUTO

        if "achs" in status:
            try:
                h_swing = SwingMode(int(status["achs"]))
            except (ValueError, TypeError, KeyError):
                h_swing = cur.h_swing_mode if cur else SwingMode.AUTO
        else:
            h_swing = cur.h_swing_mode if cur else SwingMode.AUTO

        if "acdc" in status:
            try:
                display_mode = DisplayMode(status["acdc"])
            except (ValueError, KeyError):
                display_mode = cur.display_mode if cur else DisplayMode.OFF
        else:
            display_mode = cur.display_mode if cur else DisplayMode.OFF

        if "acmd" in status:
            hvac_mode = HVACMode(status["acmd"])
        else:
            hvac_mode = cur.hvac_mode if cur else HVACMode.COOL

        if "acpm" in status or "acem" in status or "acec" in status:
            acpm = status.get("acpm", "on" if cur and cur.preset_mode == PresetMode.BOOST else "off")
            acem = status.get("acem", "on" if cur and cur.preset_mode == PresetMode.ECO else "off")
            acec = status.get("acec", "on" if cur and cur.preset_mode == PresetMode.CLEAN else "off")
            preset_mode = (
                PresetMode.BOOST
                if acpm == "on"
                else PresetMode.ECO
                if acem == "on"
                else PresetMode.CLEAN
                if acec == "on"
                else PresetMode.NONE
            )
        else:
            preset_mode = cur.preset_mode if cur else PresetMode.NONE

        if "cnv" in status:
            try:
                converti_mode = ConvertiMode(int(status["cnv"]))
            except (ValueError, TypeError, KeyError):
                converti_mode = cur.converti_mode if cur else ConvertiMode.OFF
        else:
            converti_mode = cur.converti_mode if cur else ConvertiMode.OFF

        wifi_sig = getattr(cur, "wifi_signal", 0)
        if "rssi" in status and status["rssi"] is not None and str(status["rssi"]).strip():
            try:
                wifi_sig = int(status["rssi"])
            except (ValueError, TypeError):
                pass

        if isinstance(status, dict):
            if self._last_status_payload is None:
                self._last_status_payload = dict(status)
            else:
                self._last_status_payload.update(status)

        if power_mode == PowerMode.OFF:
            comp_hz = None
            out_temp = None
            volt_v = None
            curr_a = None
        else:
            comp_hz = getattr(cur, "compressor_frequency_hz", None)
            out_temp = getattr(cur, "outdoor_condenser_temp_c", None)
            volt_v = getattr(cur, "voltage_v", None)
            curr_a = getattr(cur, "current_a", None)

        status_obj = DeviceStatus(
            is_online=is_online,
            temperature=temp,
            room_temperature=rm_temp,
            power_mode=power_mode,
            fan_mode=fan_mode,
            v_swing_mode=v_swing,
            h_swing_mode=h_swing,
            display_mode=display_mode,
            hvac_mode=hvac_mode,
            preset_mode=preset_mode,
            converti_mode=converti_mode,
            nanoe_mode=status.get("acngs", status.get("acng", getattr(cur, "nanoe_mode", "off"))),
            filter_clean_alert=status.get("acfc", "on" if getattr(cur, "filter_clean_alert", False) else "off") == "on",
            buzzer=status.get("bzr", "on" if getattr(cur, "buzzer", False) else "off") == "on",
            error_code=str(status.get("errors", getattr(cur, "error_code", "")) or ""),
            warning_code=str(status.get("warnings", getattr(cur, "warning_code", "")) or ""),
            wifi_signal=wifi_sig,
            control_source=status.get("cnt", getattr(cur, "control_source", "an")),
            compressor_frequency_hz=comp_hz,
            evaporator_coil_temp_c=getattr(cur, "evaporator_coil_temp_c", None),
            outdoor_condenser_temp_c=out_temp,
            fan_stage=getattr(cur, "fan_stage", None),
            voltage_v=volt_v,
            current_a=curr_a,
            diag_error_code=getattr(cur, "diag_error_code", ""),
            estimated_power_w=None,
            estimated_energy_kwh=None,
        )

        self.set_status(status_obj)
        self.refresh()

    def diag_handler(self, payload: Any) -> None:
        """Handle incoming .../diag MQTT telemetry frames."""
        if not isinstance(payload, dict):
            return
        di_a = payload.get("di_a")
        if not di_a or not isinstance(di_a, list):
            return
        latest_frame = next((f for f in reversed(di_a) if isinstance(f, str)), None)
        if not latest_frame:
            return
        try:
            import base64
            raw_hex = base64.b64decode(latest_frame).hex(" ")
        except Exception:
            raw_hex = latest_frame
        decoded = decode_diagnostic_frame(latest_frame)
        LOGGER.debug(
            "MirAIe /diag frame (device=%s, count=%s, in_s=%s): raw_hex=[%s] decoded=%s",
            self.id,
            len(di_a),
            payload.get("in_s"),
            raw_hex,
            decoded,
        )
        if not decoded:
            return

        cur = getattr(self, "status", None)
        if cur is None:
            return

        is_outdoor_off = (
            decoded.get("outdoor_condenser_temp_c", 0) == 0
            and decoded.get("compressor_frequency_hz", 0) == 0
            and decoded.get("voltage_v", 0) == 0
        )
        if "compressor_frequency_hz" in decoded:
            cur.compressor_frequency_hz = decoded["compressor_frequency_hz"]
        if "evaporator_coil_temp_c" in decoded:
            cur.evaporator_coil_temp_c = decoded["evaporator_coil_temp_c"]
        if "outdoor_condenser_temp_c" in decoded:
            cur.outdoor_condenser_temp_c = None if is_outdoor_off else decoded["outdoor_condenser_temp_c"]
        if "fan_stage" in decoded:
            cur.fan_stage = decoded["fan_stage"]
        if "voltage_v" in decoded:
            cur.voltage_v = None if is_outdoor_off else decoded["voltage_v"]
        if "current_a" in decoded:
            cur.current_a = None if is_outdoor_off else decoded["current_a"]
        if "error_code" in decoded:
            cur.diag_error_code = decoded["error_code"]

        self._update_power_estimate(cur)

        self.refresh()

    def connection_status_handler(self, status: any):
        self.status.is_online = status["onlineStatus"] == "true"
        self.refresh()

    def set_details(self, details: DeviceDetails):
        self.details = details
        self._estimator.profile = ACInverterProfile.from_model(
            getattr(details, "model_number", "")
        )
        if self.udp_transport:
            if not self.supports_local_udp:
                self.udp_transport.unregister_device(self.id)
            else:
                self.udp_transport.register_device(
                    device_id=self.id,
                    mac=details.mac_address,
                    key=details.udp_control_key,
                    iv=details.udp_control_iv,
                    multicast_ip=getattr(details, "multicast_ip", None),
                    callback=self.status_handler,
                )

    def set_model_capabilities(self, capabilities: dict):
        self.model_capabilities = capabilities or {}

    def _get_control_capabilities(self) -> list[dict]:
        """Return normalized list of control capability dictionaries."""
        caps = (self.model_capabilities or {}).get("controlCapabilities") or []
        if isinstance(caps, list):
            return [c for c in caps if isinstance(c, dict)]
        if isinstance(caps, dict):
            return [{"capability": k, "values": v} for k, v in caps.items()]
        return []

    @property
    def target_temperature_step(self) -> float:
        """Return 0.5 or 1.0 target temperature step dynamically from model capabilities."""
        if not self.model_capabilities:
            return 1.0
        control_caps = self._get_control_capabilities()
        for cap in control_caps:
            if cap.get("capability") == "ACTemperature":
                for attr in cap.get("attributes", []):
                    if attr.get("key") == "actmp":
                        increments = attr.get("range", {}).get("increment", [])
                        for inc in increments:
                            if inc.get("unit") == "celcius":
                                return float(inc.get("value", 1.0))
        return 1.0

    @property
    def supports_horizontal_swing(self) -> bool:
        """Return True if model capabilities include motorized HorizontalSwing."""
        if not self.model_capabilities:
            return True
        return any(cap.get("capability") == "HorizontalSwing" for cap in self._get_control_capabilities())

    @property
    def supports_night_display(self) -> bool:
        """Return True if model capabilities include night mode in DisplayControl."""
        if not self.model_capabilities:
            return False
        for cap in self._get_control_capabilities():
            if cap.get("capability") == "DisplayControl":
                for attr in cap.get("attributes", []):
                    if attr.get("key") == "acdc":
                        return "night" in attr.get("values", [])
        return False

    @property
    def supported_convertible_modes(self) -> dict[int, str]:
        """Return mapping of convertible percentage int values to display names."""
        valid_modes = {e.value for e in ConvertiMode if e.value not in (0, 1)}
        if not self.model_capabilities:
            return {
                0: "off",
                40: "40%",
                50: "50%",
                60: "60%",
                70: "70%",
                80: "80%",
                90: "90%",
                100: "100%",
                110: "110%",
            }
        control_caps = self._get_control_capabilities()
        for cap in control_caps:
            if cap.get("capability") == "Convertible":
                for attr in cap.get("attributes", []):
                    if isinstance(attr, dict) and attr.get("key") == "cnv":
                        int_values = attr.get("intValues", [])
                        if int_values:
                            modes = {0: "off"}
                            for val in sorted(int_values):
                                if val in valid_modes:
                                    modes[val] = f"{val}%"
                            return modes
        return {0: "off"}

    @property
    def convertible_profile_name(self) -> str | None:
        """Return classified convertible profile name ('Converti8', 'Converti7', 'Converti4') or None."""
        modes = self.supported_convertible_modes
        active_count = len([k for k in modes if k != 0])
        if active_count >= 8:
            return "Converti8"
        elif active_count == 7:
            return "Converti7"
        elif active_count > 0:
            return f"Converti{active_count}"
        return None

    @property
    def supports_convertible(self) -> bool:
        """Return True if model has Convertible mode."""
        return len([k for k in self.supported_convertible_modes if k != 0]) > 0

    @property
    def supports_nanoe(self) -> bool:
        """Return True if model has Nanoe air purification."""
        if not self.model_capabilities:
            return False
        return any(cap.get("capability") in ("NanoeG", "NanoeX", "NanoeGA") for cap in self._get_control_capabilities())

    @property
    def supports_buzzer(self) -> bool:
        """Return True if model has BuzzerControl."""
        if not self.model_capabilities:
            return False
        return any(cap.get("capability") == "BuzzerControl" for cap in self._get_control_capabilities())
        return False

    @property
    def supports_local_udp(self) -> bool:
        """Return True if device has local UDP keys available."""
        details = getattr(self, "details", None)
        if not details:
            return False
        return bool(details.udp_control_key and details.udp_control_iv)

    def attach_udp_transport(self, transport: any, use_local_udp: bool = True):
        """Attach UDP transport manager and optionally enable UDP priority routing."""
        if self.udp_transport and self.udp_transport != transport:
            self.udp_transport.unregister_device(self.id)
        self.udp_transport = transport
        self.use_local_udp = use_local_udp
        if transport and self.supports_local_udp and getattr(self, "details", None):
            transport.register_device(
                device_id=self.id,
                mac=self.details.mac_address,
                key=self.details.udp_control_key,
                iv=self.details.udp_control_iv,
                multicast_ip=getattr(self.details, "multicast_ip", None),
                callback=self.status_handler,
            )

    async def send_udp_command(self, cmd_dict: dict, controller: str | None = None) -> dict:
        """Send command strictly via Local UDP transport and update status on ACK."""
        if not self.udp_transport:
            raise RuntimeError(f"No UDP transport attached to device {self.id}")
        if not self.supports_local_udp:
            raise RuntimeError(f"Device {self.id} does not support local UDP (missing keys)")

        try:
            details = getattr(self, "details", None)
            if details and self.id not in getattr(self.udp_transport, "_devices", {}):
                self.udp_transport.register_device(
                    device_id=self.id,
                    mac=details.mac_address,
                    key=details.udp_control_key,
                    iv=details.udp_control_iv,
                    multicast_ip=getattr(details, "multicast_ip", None),
                    callback=self.status_handler,
                )
            kwargs = {}
            ctrl = controller if controller is not None else getattr(self, "controller_id", None)
            if ctrl is not None and ctrl != "ha":
                kwargs["controller"] = ctrl
            elif controller is not None:
                kwargs["controller"] = controller
            res = await self.udp_transport.send_command(self.id, cmd_dict, **kwargs)
        except Exception as err:
            raise RuntimeError(f"Local UDP command failed for device {self.id}: {err}") from err
        if res and isinstance(res, dict) and (not cmd_dict or any(k in res for k in cmd_dict)):
            return res
        raise RuntimeError(f"Local UDP command failed or rejected by device {self.id}: {res}")

    async def send_cloud_command(self, broker_coro: Callable):
        """Send command strictly via Cloud MQTT broker."""
        if not self.broker:
            raise RuntimeError(f"No MQTT broker configured for device {self.id}")
        if not callable(broker_coro):
            raise TypeError(f"broker_coro must be callable, got {type(broker_coro).__name__}")
        await broker_coro()

    async def _send_command(
        self, cmd_dict: dict, broker_coro: Callable, transport: str | None = None
    ):
        """Send command via requested transport, with optional broker fallback when transport is None."""
        if transport == "udp":
            await self.send_udp_command(cmd_dict)
            return
        elif transport == "cloud":
            await self.send_cloud_command(broker_coro)
            return
        elif transport is not None:
            raise ValueError(f"Unknown transport: {transport}")

        # Default mode (transport is None): try UDP if supported/enabled, with broker fallback if configured
        if self.use_local_udp and self.supports_local_udp and self.udp_transport:
            try:
                await self.send_udp_command(cmd_dict)
                return
            except Exception as err:
                if self.broker:
                    LOGGER.warning("UDP command failed for %s, falling back to MQTT: %s", self.id, err)
                    await self.send_cloud_command(broker_coro)
                    return
                raise
        await self.send_cloud_command(broker_coro)

    def _build_estimator_payload(self, status_obj: DeviceStatus) -> dict[str, Any]:
        """Build a complete status payload for the power estimator from DeviceStatus."""
        return {
            "ps": "on" if status_obj.power_mode == PowerMode.ON else "off",
            "acmd": status_obj.hvac_mode.value if hasattr(status_obj.hvac_mode, "value") else str(status_obj.hvac_mode),
            "acfs": status_obj.fan_mode.value if hasattr(status_obj.fan_mode, "value") else str(status_obj.fan_mode),
            "cnv": status_obj.converti_mode.value if hasattr(status_obj.converti_mode, "value") else int(status_obj.converti_mode or 0),
            "actmp": status_obj.temperature,
            "rmtmp": status_obj.room_temperature,
        }

    def _update_power_estimate(self, status_obj: DeviceStatus) -> None:
        """Update estimated power and cumulative energy on a DeviceStatus instance."""
        firmware_version = getattr(getattr(self, "details", None), "firmware_version", "")
        comp_hz = None if status_obj.power_mode == PowerMode.OFF else status_obj.compressor_frequency_hz
        out_temp = None if status_obj.power_mode == PowerMode.OFF else status_obj.outdoor_condenser_temp_c
        est_power = self._estimator.process_telemetry(
            self._build_estimator_payload(status_obj),
            external_room_temp=status_obj.room_temperature,
            firmware_version=firmware_version,
            compressor_hz=comp_hz,
            outdoor_temp_c=out_temp,
        )
        status_obj.estimated_power_w = est_power
        status_obj.estimated_energy_kwh = round(self._estimator.cumulative_energy_kwh, 4)

    def set_status(self, status: DeviceStatus):
        if status.estimated_power_w is None:
            self._update_power_estimate(status)
        self.status = status

    async def turn_on(self, transport: str | None = None):
        await self._send_command(
            {"ps": "on"},
            lambda: self.broker.set_power(self.control_topic, PowerMode.ON),
            transport=transport,
        )

    async def turn_off(self, transport: str | None = None):
        await self._send_command(
            {"ps": "off"},
            lambda: self.broker.set_power(self.control_topic, PowerMode.OFF),
            transport=transport,
        )

    async def set_temperature(self, temperature: float, transport: str | None = None):
        t_val = int(temperature) if float(temperature).is_integer() else temperature
        await self._send_command(
            {"actmp": str(t_val)},
            lambda: self.broker.set_temperature(self.control_topic, temperature),
            transport=transport,
        )

    async def set_hvac_mode(self, mode: HVACMode, transport: str | None = None):
        await self._send_command(
            {"acmd": str(mode.value)},
            lambda: self.broker.set_hvac_mode(self.control_topic, mode),
            transport=transport,
        )

    async def set_fan_mode(self, mode: FanMode, transport: str | None = None):
        await self._send_command(
            {"acfs": str(mode.value)},
            lambda: self.broker.set_fan_mode(self.control_topic, mode),
            transport=transport,
        )

    async def set_preset_mode(self, mode: PresetMode, transport: str | None = None):
        if self.broker and hasattr(self.broker, "build_preset_mode_payload"):
            cmd = self.broker.build_preset_mode_payload(mode)
            cmd_pure = {k: v for k, v in cmd.items() if k not in ("ki", "cnt", "sid")}
        else:
            payloads = {
                PresetMode.ECO: {"acpm": "off", "acem": "on", "acec": "off"},
                PresetMode.BOOST: {"acpm": "on", "acem": "off", "acec": "off"},
                PresetMode.CLEAN: {"acpm": "off", "acem": "off", "acec": "on"},
                PresetMode.NONE: {"acpm": "off", "acem": "off", "acec": "off"},
            }
            if mode not in payloads:
                raise ValueError(f"Unsupported preset mode: {mode}")
            cmd_pure = dict(payloads[mode])
        await self._send_command(
            cmd_pure,
            lambda: self.broker.set_preset_mode(self.control_topic, mode),
            transport=transport,
        )

    async def set_v_swing_mode(self, mode: SwingMode, transport: str | None = None):
        await self._send_command(
            {"acvs": mode.value},
            lambda: self.broker.set_v_swing_mode(self.control_topic, mode),
            transport=transport,
        )

    async def set_h_swing_mode(self, mode: SwingMode, transport: str | None = None):
        await self._send_command(
            {"achs": mode.value},
            lambda: self.broker.set_h_swing_mode(self.control_topic, mode),
            transport=transport,
        )

    async def set_display_mode(self, mode: DisplayMode, transport: str | None = None):
        await self._send_command(
            {"acdc": str(mode.value)},
            lambda: self.broker.set_display_mode(self.control_topic, mode),
            transport=transport,
        )

    async def set_converti_mode(self, mode: ConvertiMode, transport: str | None = None):
        val = int(mode.value) if hasattr(mode, "value") else int(mode)
        await self._send_command(
            {"cnv": val, "acem": "off", "acpm": "off"},
            lambda: self.broker.set_converti_mode(self.control_topic, mode),
            transport=transport,
        )

    async def set_nanoe(self, state: bool, transport: str | None = None):
        val = "on" if state else "off"
        key = "acngs" if any(c.get("capability") == "NanoeX" for c in self._get_control_capabilities()) else "acng"
        await self._send_command(
            {key: val},
            lambda: self.broker.set_nanoe(self.control_topic, state),
            transport=transport,
        )

    async def set_buzzer(self, state: bool, transport: str | None = None):
        val = "on" if state else "off"
        await self._send_command(
            {"bzr": val},
            lambda: self.broker.set_buzzer(self.control_topic, state),
            transport=transport,
        )