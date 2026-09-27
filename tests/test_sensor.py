import json
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from showerhacks.sensor import Reading, SensorPoller, sustained_peak


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
    def test_old_room_history_cannot_enable_start(self) -> None:
        poller = SensorPoller(FakeSensor)
        now = datetime.now().astimezone()
        for index in range(100):
            poller._room_readings.append(Reading(now - timedelta(seconds=200 - index), 200, 400))
        for index in range(15):
            poller._room_readings.append(Reading(now - timedelta(seconds=74 - index), 4000, 400))
        self.assertIsNone(poller.room_reference())
        self.assertEqual(poller._room_readings.maxlen, 300)

    def test_room_reference_tracks_rising_room_level_without_following_one_spike(self) -> None:
        poller = SensorPoller(FakeSensor)
        now = datetime.now().astimezone()
        poller._room_sampling_since = now - timedelta(seconds=300)
        for index, value in enumerate([50] * 285 + [150] * 14 + [4000]):
            poller._room_readings.append(Reading(now - timedelta(seconds=299 - index), value, 400))
        self.assertEqual(poller.room_reference(), 150)

        poller._room_readings.clear()
        for index, value in enumerate([150] * 299 + [4000]):
            poller._room_readings.append(Reading(now - timedelta(seconds=299 - index), value, 400))
        self.assertEqual(poller.room_reference(), 150)

    def test_start_uses_fresh_median_and_robust_noise_and_freezes(self) -> None:
        poller = SensorPoller(FakeSensor)
        now = datetime.now().astimezone()
        poller._room_sampling_since = now - timedelta(seconds=15)
        for index, value in enumerate([80, 90, 100, 110, 4000]):
            poller._room_readings.append(Reading(now - timedelta(seconds=4-index), value, 400))
        self.assertEqual(poller.starting_conditions(freeze=True), (100, 15))
        self.assertFalse(poller._room_sampling_enabled)
        poller.set_room_sampling(True)
        self.assertIsNone(poller.starting_conditions())

    def test_start_requires_three_current_contiguous_samples(self) -> None:
        poller = SensorPoller(FakeSensor)
        now = datetime.now().astimezone()
        poller._room_sampling_since = now - timedelta(seconds=20)
        for age in (10, 9, 8):
            poller._room_readings.append(Reading(now - timedelta(seconds=age), 100, 400))
        self.assertIsNone(poller.starting_conditions())
        for age in (2, 1):
            poller._room_readings.append(Reading(now - timedelta(seconds=age), 200, 400))
        self.assertIsNone(poller.starting_conditions())
        poller._room_readings.append(Reading(now, 200, 400))
        self.assertEqual(poller.starting_conditions(), (200, 0))

    def test_sustained_peak_rejects_one_spike_but_keeps_a_sustained_rise(self) -> None:
        start = datetime.now().astimezone()
        def readings(values):
            return [Reading(start + timedelta(seconds=index), value, 400)
                    for index, value in enumerate(values)]
        end = start + timedelta(seconds=20)
        self.assertEqual(sustained_peak(readings([100, 100, 4000, 100, 100]), start, end), 100)
        self.assertEqual(sustained_peak(readings([100, 200, 210, 200, 100]), start, end), 200)
        self.assertEqual(sustained_peak(readings([4000, 4000]), start, end), 0)
        # GET READY and late samples cannot contribute, nor can samples across a gap.
        samples = [Reading(start + timedelta(seconds=offset), 4000, 400)
                   for offset in (-2, -1, 0, 10, 20, 21, 22)]
        self.assertEqual(sustained_peak(samples, start, end), 0)

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
            self.assertIsNone(poller.room_reference())
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
            self.assertIsNone(restored.room_reference())
            self.assertEqual(list(restored._room_readings), list(poller._room_readings))

    def test_room_reference_follows_recovery_after_a_high_spike(self) -> None:
        poller = SensorPoller(FakeSensor)
        now = datetime.now().astimezone()
        poller._room_sampling_since = now - timedelta(seconds=300)
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
        self.assertTrue(all(reading.tvoc_ppb <= 260 for reading in readings))
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
        while (poller.room_reference() is None or poller.room_reference() == frozen_reference) and time.monotonic() < deadline:
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
        while len(poller.snapshot()[0]) < 3 and time.monotonic() < deadline:
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
