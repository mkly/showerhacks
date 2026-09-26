import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from smellcity.sensor import SensorPoller


class FakeSensor:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = False
        self.baseline_calls = 0

    def iaq_measure(self) -> tuple[int, int]:
        self.calls += 1
        return 400, self.calls

    def close(self) -> None:
        self.closed = True

    def get_baseline(self) -> tuple[int, int]:
        self.baseline_calls += 1
        return 0x8973, 0x8AAE


class SensorPollerTest(unittest.TestCase):
    def test_zero_tvoc_is_a_valid_ready_reading(self) -> None:
        sensor = FakeSensor()
        sensor.iaq_measure = lambda: (400, 0)
        poller = SensorPoller(lambda: sensor, interval=0.001)
        self.assertIsNone(poller.room_reference())
        poller.start()
        deadline = time.monotonic() + 2
        while not poller.snapshot()[0] and time.monotonic() < deadline:
            time.sleep(0.005)
        poller.stop()
        self.assertEqual(poller.room_reference(), 0)

    def test_thread_keeps_latest_50_readings(self) -> None:
        sensor = FakeSensor()
        poller = SensorPoller(lambda: sensor, interval=0.001)
        poller.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            readings, _ = poller.snapshot()
            if readings and readings[-1].tvoc_ppb >= 55:
                break
            time.sleep(0.005)
        poller.stop()

        readings, error = poller.snapshot()
        self.assertGreaterEqual(sensor.calls, 55)
        self.assertEqual(len(readings), 50)
        self.assertEqual(readings[-1].tvoc_ppb, sensor.calls)
        self.assertEqual(readings[0].tvoc_ppb, sensor.calls - 49)
        self.assertIsNone(error)
        self.assertTrue(sensor.closed)
        self.assertEqual(poller.room_reference(), round((max(1, sensor.calls - 59) + sensor.calls) / 2))

    def test_baseline_is_logged_as_jsonl(self) -> None:
        sensor = FakeSensor()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "baselines.jsonl"
            poller = SensorPoller(lambda: sensor, interval=0.001, baseline_file=path, baseline_interval=0.01)
            poller.start()
            deadline = time.monotonic() + 2
            while not path.exists() and time.monotonic() < deadline:
                time.sleep(0.005)
            poller.stop()

            self.assertTrue(path.exists())
            lines = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertGreaterEqual(len(lines), 1)
            self.assertEqual(lines[0]["eco2_baseline_hex"], "0x8973")
            self.assertEqual(lines[0]["tvoc_baseline_hex"], "0x8AAE")
            self.assertGreater(lines[0]["tvoc_ppb"], 0)
            self.assertIn("timestamp", lines[0])


if __name__ == "__main__":
    unittest.main()
