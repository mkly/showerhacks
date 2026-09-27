import json
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from showerhacks.sensor import Reading, SensorPoller


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
    def test_five_minute_reference_filters_outliers_with_zero_mad(self) -> None:
        poller = SensorPoller(FakeSensor)
        now = datetime.now().astimezone()
        for index in range(100):
            poller._room_readings.append(Reading(now - timedelta(seconds=200 - index), 200, 400))
        for index in range(15):
            poller._room_readings.append(Reading(now - timedelta(seconds=74 - index), 4000, 400))
        self.assertEqual(poller.room_reference(), 200)
        self.assertEqual(poller._room_readings.maxlen, 300)

    def test_room_reference_tracks_rising_room_level_without_following_one_spike(self) -> None:
        poller = SensorPoller(FakeSensor)
        now = datetime.now().astimezone()
        for index, value in enumerate([50] * 285 + [150] * 14 + [4000]):
            poller._room_readings.append(Reading(now - timedelta(seconds=299 - index), value, 400))
        self.assertEqual(poller.room_reference(), 150)

        poller._room_readings.clear()
        for index, value in enumerate([150] * 299 + [4000]):
            poller._room_readings.append(Reading(now - timedelta(seconds=299 - index), value, 400))
        self.assertEqual(poller.room_reference(), 150)

    def test_restores_only_recent_valid_room_readings(self) -> None:
        now = datetime.now().astimezone()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "room.jsonl"
            records = [
                {"timestamp": (now - timedelta(seconds=age)).isoformat(),
                 "tvoc_ppb": value, "eco2_ppm": 400}
                for age, value in [(301, 4000), (180, 200), (10, 220), (5, 47347), (-60, 3000)]
            ]
            path.write_text("\n".join(json.dumps(record) for record in records)
                            + '\n{"timestamp":\nnull\n')
            poller = SensorPoller(FakeSensor, room_history_file=path)
            self.assertEqual([item.tvoc_ppb for item in poller._room_readings], [200, 220])
            self.assertEqual(poller.room_reference(), 210)
            self.assertEqual(poller.snapshot()[0], [])

    def test_room_history_survives_restart_without_round_readings(self) -> None:
        sensor = FakeSensor()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "room.jsonl"
            poller = SensorPoller(lambda: sensor, interval=0.01, room_history_file=path)
            poller.start()
            deadline = time.monotonic() + 2
            while sensor.calls < 3 and time.monotonic() < deadline:
                time.sleep(0.005)
            poller.set_room_sampling(False)
            frozen = poller.room_reference()
            paused_at = sensor.calls
            while sensor.calls < paused_at + 3 and time.monotonic() < deadline:
                time.sleep(0.005)
            poller.stop()
            self.assertGreaterEqual(sensor.calls, paused_at + 3)
            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertTrue(records)
            self.assertLess(max(record["tvoc_ppb"] for record in records), sensor.calls)
            restored = SensorPoller(FakeSensor, room_history_file=path)
            self.assertEqual(restored.room_reference(), frozen)
            self.assertEqual(list(restored._room_readings), list(poller._room_readings))

    def test_room_reference_follows_recovery_after_a_high_spike(self) -> None:
        poller = SensorPoller(FakeSensor)
        now = datetime.now().astimezone()
        for index, value in enumerate([2800] * 285 + [250] * 15):
            poller._room_readings.append(Reading(now - timedelta(seconds=299 - index), value, 400))
        self.assertEqual(poller.room_reference(), 250)

        poller._room_readings.clear()
        for index, value in enumerate([2800] * 299 + [250]):
            poller._room_readings.append(Reading(now - timedelta(seconds=299 - index), value, 400))
        self.assertEqual(poller.room_reference(), 2800)

    def test_implausible_tvoc_is_logged_but_not_used_for_gameplay(self) -> None:
        sensor = FakeSensor()
        values = (250, 47_347, 260)

        def measure() -> tuple[int, int]:
            sensor.calls += 1
            return 400, values[min(sensor.calls - 1, 2)]

        sensor.iaq_measure = measure
        with TemporaryDirectory() as directory:
            path = Path(directory) / "baselines.jsonl"
            poller = SensorPoller(lambda: sensor, interval=0.01,
                                  baseline_file=path, baseline_interval=0)
            poller.start()
            deadline = time.monotonic() + 2
            while sensor.calls < 3 and time.monotonic() < deadline:
                time.sleep(0.005)
            poller.stop()
            readings, _ = poller.snapshot()
            logged = [json.loads(line)["tvoc_ppb"] for line in path.read_text().splitlines()]

        self.assertEqual(readings[0].tvoc_ppb, 250)
        self.assertIn(260, [reading.tvoc_ppb for reading in readings])
        self.assertNotIn(47_347, [reading.tvoc_ppb for reading in readings])
        self.assertLessEqual(poller.room_reference(), 260)
        self.assertIn(47_347, logged)

    def test_round_readings_do_not_change_room_reference(self) -> None:
        sensor = FakeSensor()
        poller = SensorPoller(lambda: sensor, interval=0.001)
        poller.start()
        deadline = time.monotonic() + 2
        while sensor.calls < 3 and time.monotonic() < deadline:
            time.sleep(0.005)
        poller.set_room_sampling(False)
        frozen_reference = poller.room_reference()
        paused_at = sensor.calls
        while sensor.calls < paused_at + 5 and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertGreaterEqual(sensor.calls, paused_at + 5)
        self.assertEqual(poller.room_reference(), frozen_reference)
        poller.set_room_sampling(True)
        while poller.room_reference() == frozen_reference and time.monotonic() < deadline:
            time.sleep(0.005)
        poller.stop()
        self.assertGreater(poller.room_reference(), frozen_reference)

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
        self.assertEqual(poller.room_reference(), round((1 + sensor.calls) / 2))

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
