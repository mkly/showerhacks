"""Read an SGP30 once a second without blocking the Arcade event loop."""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import sys
from math import pi, sin
from pathlib import Path
from statistics import fmean, median
from threading import Event, Lock, Thread
from time import monotonic
from typing import Callable, Protocol


MAX_GAME_TVOC_PPB = 5_000
ROOM_HISTORY_SECONDS = 300
ROOM_RECOVERY_SECONDS = 15


class SGP30(Protocol):
    def iaq_measure(self) -> tuple[int, int]: ...
    def get_baseline(self) -> tuple[int, int]: ...


class PicoSensor:
    """Request one measurement from the Pico's MicroPython main.py."""

    def __init__(self, port: str) -> None:
        import serial

        self._serial = serial.Serial(port, 115200, timeout=0.2, write_timeout=0.2)

    def iaq_measure(self) -> tuple[int, int]:
        parts = self._exchange(b"READ\n", "OK")
        if len(parts) != 3:
            raise ValueError(f"Invalid Pico measurement: {parts}")
        return int(parts[1]), int(parts[2])

    def get_baseline(self) -> tuple[int, int]:
        parts = self._exchange(b"BASELINE\n", "BASELINE")
        if len(parts) != 3:
            raise ValueError(f"Invalid Pico baseline: {parts}")
        return int(parts[1], 16), int(parts[2], 16)

    def _exchange(self, command: bytes, expected: str) -> list[str]:
        self._serial.reset_input_buffer()
        self._serial.write(command)
        deadline = monotonic() + 0.8
        while monotonic() < deadline:
            line = self._serial.readline().decode("ascii", errors="replace").strip()
            if line.startswith("ERR,"):
                raise OSError(line[4:])
            if line.startswith(expected + ","):
                return line.split(",")
        raise TimeoutError(f"No {expected} response from Pico")

    def close(self) -> None:
        self._serial.close()


class DemoSensor:
    """Repeat a changing TVOC reading for demos without Pico hardware."""

    def __init__(self) -> None:
        self._samples = 0

    def iaq_measure(self) -> tuple[int, int]:
        self._samples += 1
        tvoc = 230 + max(0, round(270 * sin(self._samples * pi / 15)))
        return 400, tvoc

    def get_baseline(self) -> tuple[int, int]:
        return 0, 0


@dataclass(frozen=True)
class Reading:
    taken_at: datetime
    tvoc_ppb: int
    eco2_ppm: int


class SensorPoller:
    def __init__(
        self,
        sensor_factory: Callable[[], SGP30],
        interval: float = 1.0,
        baseline_file: Path | None = None,
        baseline_interval: float = 60.0,
        room_history_file: Path | None = None,
    ) -> None:
        self._sensor_factory = sensor_factory
        self._interval = interval
        self._baseline_file = baseline_file
        self._baseline_interval = baseline_interval
        self._room_history_file = room_history_file
        self._readings: deque[Reading] = deque(maxlen=50)
        self._room_readings: deque[Reading] = deque(maxlen=ROOM_HISTORY_SECONDS)
        self._room_sampling_enabled = True
        self._lock = Lock()
        self._stop = Event()
        self._thread = Thread(target=self._poll, name="sgp30-poller", daemon=True)
        self._error: str | None = None
        self._load_room_history()

    def _load_room_history(self) -> None:
        if self._room_history_file is None:
            return
        now = datetime.now().astimezone()
        cutoff = now - timedelta(seconds=ROOM_HISTORY_SECONDS)
        restored = []
        try:
            with self._room_history_file.open() as file:
                for line in file:
                    try:
                        record = json.loads(line)
                        reading = Reading(datetime.fromisoformat(record["timestamp"]),
                                          int(record["tvoc_ppb"]), int(record["eco2_ppm"]))
                        if cutoff <= reading.taken_at <= now and 0 <= reading.tvoc_ppb <= MAX_GAME_TVOC_PPB:
                            restored.append(reading)
                    except (ValueError, TypeError, KeyError):
                        continue
        except FileNotFoundError:
            return
        except OSError as exc:
            print(f"Could not load room history: {exc}", file=sys.stderr, flush=True)
            return
        self._room_readings.extend(sorted(restored, key=lambda item: item.taken_at))

    def _save_room_history(self, readings: list[Reading]) -> None:
        if self._room_history_file is None:
            return
        temporary = self._room_history_file.with_suffix(".jsonl.tmp")
        try:
            with temporary.open("w") as file:
                for reading in readings:
                    file.write(json.dumps({
                        "timestamp": reading.taken_at.isoformat(),
                        "tvoc_ppb": reading.tvoc_ppb,
                        "eco2_ppm": reading.eco2_ppm,
                    }) + "\n")
            temporary.replace(self._room_history_file)
        except OSError as exc:
            print(f"Could not save room history: {exc}", file=sys.stderr, flush=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    def snapshot(self) -> tuple[list[Reading], str | None]:
        with self._lock:
            return list(self._readings), self._error

    def set_room_sampling(self, enabled: bool) -> None:
        with self._lock:
            self._room_sampling_enabled = enabled

    def room_reference(self) -> int | None:
        """Average five minutes after MAD filtering, with faster downward recovery."""
        now = datetime.now().astimezone()
        cutoff = now - timedelta(seconds=ROOM_HISTORY_SECONDS)
        with self._lock:
            recent = [item for item in self._room_readings if cutoff <= item.taken_at <= now]
        positive = [item.tvoc_ppb for item in recent if item.tvoc_ppb > 0]
        if not positive:
            return 0 if recent else None
        center = median(positive)
        mad = median(abs(value - center) for value in positive)
        # A 10 ppb floor handles steady readings where MAD is zero.
        limit = max(10, 3 * 1.4826 * mad)
        reference = fmean(value for value in positive if abs(value - center) <= limit)
        recovery_cutoff = now - timedelta(seconds=ROOM_RECOVERY_SECONDS)
        short = [item.tvoc_ppb for item in recent if item.taken_at >= recovery_cutoff]
        if len(short) >= 3:
            short_positive = [value for value in short if value > 0]
            reference = min(reference, median(short_positive) if short_positive else 0)
        return round(reference)

    def _poll(self) -> None:
        try:
            sensor = self._sensor_factory()
        except Exception as exc:
            with self._lock:
                self._error = f"Could not open SGP30: {exc}"
            return

        next_read = monotonic()
        next_baseline = next_read + self._baseline_interval
        try:
            while not self._stop.is_set():
                if self._stop.wait(max(0, next_read - monotonic())):
                    break
                try:
                    eco2, tvoc = sensor.iaq_measure()
                    reading = Reading(
                        taken_at=datetime.now().astimezone(),
                        tvoc_ppb=tvoc,
                        eco2_ppm=eco2,
                    )
                    with self._lock:
                        if 0 <= tvoc <= MAX_GAME_TVOC_PPB:
                            self._readings.append(reading)
                            if self._room_sampling_enabled:
                                self._room_readings.append(reading)
                            self._error = None
                        else:
                            self._error = f"SGP30 TVOC spike ignored: {tvoc} ppb"
                            print(f"{reading.taken_at.isoformat()} {self._error}",
                                  file=sys.stderr, flush=True)
                        cutoff = reading.taken_at - timedelta(seconds=ROOM_HISTORY_SECONDS)
                        while self._room_readings and self._room_readings[0].taken_at < cutoff:
                            self._room_readings.popleft()
                        room_history = list(self._room_readings)
                    self._save_room_history(room_history)
                    if self._baseline_file is not None and monotonic() >= next_baseline:
                        next_baseline = monotonic() + self._baseline_interval
                        eco2_base, tvoc_base = sensor.get_baseline()
                        record = {
                            "timestamp": reading.taken_at.isoformat(),
                            "tvoc_ppb": tvoc,
                            "eco2_baseline_hex": f"0x{eco2_base:04X}",
                            "tvoc_baseline_hex": f"0x{tvoc_base:04X}",
                        }
                        with self._baseline_file.open("a") as file:
                            file.write(json.dumps(record) + "\n")
                except Exception as exc:
                    with self._lock:
                        self._error = f"SGP30 request failed: {exc}"
                next_read += self._interval
                if next_read < monotonic():
                    next_read = monotonic() + self._interval
        finally:
            close = getattr(sensor, "close", None)
            if close is not None:
                close()
