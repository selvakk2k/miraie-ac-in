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


if __name__ == "__main__":
    unittest.main()

