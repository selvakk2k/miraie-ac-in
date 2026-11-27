import json
import unittest
from miraie_ac.energy import InverterPowerEstimator, ACInverterProfile


class TestInverterPowerEstimator(unittest.TestCase):
    """Test thermodynamic power and energy estimation engine."""

    def setUp(self):
        self.profile = ACInverterProfile(
            rated_power_w=1480.0,
            half_power_w=480.0,
            min_power_w=290.0,
            standby_power_w=2.5,
        )
        self.estimator = InverterPowerEstimator(profile=self.profile, pull_down_delta_span=2.5)

    def test_standby_power(self):
        """When AC is off, power should equal standby power."""
        payload = {"ps": "off", "actmp": "24.0", "rmtmp": "28.0"}
        power = self.estimator.calculate_power(payload)
        self.assertEqual(power, 2.5)

    def test_fan_only_mode(self):
        """In fan mode, compressor is off; only blower runs."""
        payload = {"ps": "on", "acmd": "fan", "acfs": "low", "actmp": "24.0"}
        power = self.estimator.calculate_power(payload)
        # 2.5W standby + 18W low fan = 20.5W
        self.assertEqual(power, 20.5)

    def test_converti_power_capping(self):
        """Converti percentage limits maximum compressor draw."""
        # 40% converti mode
        payload_full = {"ps": "on", "acmd": "cool", "acfs": "low", "actmp": "20.0", "rmtmp": "30.0", "cnv": 0}
        payload_cnv = {"ps": "on", "acmd": "cool", "acfs": "low", "actmp": "20.0", "rmtmp": "30.0", "cnv": 40}

        power_full = self.estimator.calculate_power(payload_full)
        power_cnv = self.estimator.calculate_power(payload_cnv)

        self.assertGreater(power_full, power_cnv)
        # Max compressor power capped at 1480 * 0.40 = 592W (+ 18W fan + 2.5W standby) = ~612.5W
        self.assertAlmostEqual(power_cnv, 612.5, delta=5.0)

    def test_minimum_inverter_modulation_floor(self):
        """When target is reached, power drops to minimum modulation floor."""
        payload_target = {"ps": "on", "acmd": "cool", "acfs": "low", "actmp": "24.0", "rmtmp": "24.0"}
        power = self.estimator.calculate_power(payload_target)
        # 2.5W standby + 18W low fan + 290W min comp = 310.5W
        self.assertEqual(power, 310.5)

    def test_ac_session_replay_calibration(self):
        """Replaying physical session telemetry should match Panasonic cloud calculation within 2%."""
        with open(r"d:\GitHub\miraie-ac\ac_session.jsonl", encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]

        for r in records:
            self.estimator.process_telemetry(
                r["payload"],
                timestamp=r["timestamp"],
                firmware_version="3.02",
            )

        cloud_energy_kwh = 0.163166666666667
        estimated_kwh = self.estimator.cumulative_energy_kwh

        # Absolute error < 0.005 kWh (error < 2%)
        self.assertAlmostEqual(estimated_kwh, cloud_energy_kwh, delta=0.005)

    def test_model_a_and_stale_zero_hz_fallback(self):
        """Model A uses real 0xC2 Hz, while stale 0 Hz falls back to Model B."""
        payload = {"ps": "on", "acmd": "cool", "acfs": "high", "actmp": "26.0", "rmtmp": "30.0"}
        power_49hz = self.estimator.calculate_power(payload, compressor_hz=49, outdoor_temp_c=42)
        self.assertGreater(power_49hz, 350.0)
        self.assertLess(power_49hz, 550.0)

        # Stale 0 Hz while AC is ON must fall back to Model B (full pull-down > 1000W), not fan-only 20W
        power_stale_zero = self.estimator.calculate_power(payload, compressor_hz=0)
        self.assertGreater(power_stale_zero, 1000.0)

    def test_decode_diagnostic_frame_on_frame(self):
        """Verify 0xC2 Hz, 0xC3 outdoor temp, 0xC5 current, and 0xC9 voltage decoding."""
        import base64
        from miraie_ac.utils import decode_diagnostic_frame

        raw = bytes.fromhex(
            "b1 54 b2 0f b3 1a b4 06 b5 00 73 b6 ff ff b7 ff "
            "c1 ff c2 3d c3 2a c4 ff c5 00 b6 c6 14 c7 00 c2 "
            "c8 02 00 c9 00 e8 ca 2b e1 24 e2 24 e3 24 f1 ff "
            "f2 48 30 30 f3 ff ff ff ff"
        )
        decoded = decode_diagnostic_frame(base64.b64encode(raw).decode("ascii"))
        self.assertEqual(decoded["compressor_frequency_hz"], 61)
        self.assertEqual(decoded["evaporator_coil_temp_c"], 15)
        self.assertEqual(decoded["outdoor_condenser_temp_c"], 42)
        self.assertEqual(decoded["fan_stage"], 6)
        self.assertEqual(decoded["current_a"], 1.82)
        self.assertEqual(decoded["voltage_v"], 232)
        self.assertEqual(decoded["error_code"], "H00")

    def test_partial_rssi_preserves_power_and_rest_seed_diag(self):
        """Partial MQTT updates keep full state and REST set_status seeds diag estimation."""
        import base64
        from miraie_ac.device import (
            ConvertiMode,
            Device,
            DeviceStatus,
            DisplayMode,
            FanMode,
            HVACMode,
            PowerMode,
            PresetMode,
            SwingMode,
        )

        dev = Device(
            id="dev1",
            name="AC",
            friendly_name="AC",
            control_topic="a/b/c/ctrl",
            status_topic="a/b/c/status",
            connection_status_topic="a/b/c/conn",
            broker=None,
        )
        dev.set_status(
            DeviceStatus(
                is_online=True,
                temperature=24.0,
                room_temperature=28.0,
                power_mode=PowerMode.ON,
                fan_mode=FanMode.AUTO,
                v_swing_mode=SwingMode.AUTO,
                h_swing_mode=SwingMode.AUTO,
                display_mode=DisplayMode.OFF,
                hvac_mode=HVACMode.COOL,
                preset_mode=PresetMode.NONE,
                converti_mode=ConvertiMode.OFF,
            )
        )
        self.assertIsNotNone(dev.status.estimated_power_w)
        self.assertGreater(dev.status.estimated_power_w, 300.0)

        raw = bytes.fromhex(
            "b1 54 b2 0f b3 1a b4 06 b5 00 73 b6 ff ff b7 ff "
            "c1 ff c2 3d c3 2a c4 ff c5 00 b6 c6 14 c7 00 c2 "
            "c8 02 00 c9 00 e8 ca 2b e1 24 e2 24 e3 24 f1 ff "
            "f2 48 30 30 f3 ff ff ff ff"
        )
        dev.diag_handler({"di_a": [base64.b64encode(raw).decode("ascii")]})
        self.assertEqual(dev.status.compressor_frequency_hz, 61)
        self.assertGreater(dev.status.estimated_power_w, 300.0)

        dev.status_handler({"rssi": -55})
        self.assertEqual(dev.status.power_mode, PowerMode.ON)
        self.assertGreater(dev.status.estimated_power_w, 300.0)


if __name__ == "__main__":
    unittest.main()

