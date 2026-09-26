"""Read an SGP30 once a second without blocking the Arcade event loop."""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
from statistics import median
from threading import Event, Lock, Thread
from time import monotonic
from typing import Callable, Protocol


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
    ) -> None:
        self._sensor_factory = sensor_factory
        self._interval = interval
        self._baseline_file = baseline_file
        self._baseline_interval = baseline_interval
        self._readings: deque[Reading] = deque(maxlen=50)
        self._minute_readings: deque[Reading] = deque(maxlen=60)
        self._lock = Lock()
        self._stop = Event()
        self._thread = Thread(target=self._poll, name="sgp30-poller", daemon=True)
        self._error: str | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    def snapshot(self) -> tuple[list[Reading], str | None]:
        with self._lock:
            return list(self._readings), self._error

    def room_reference(self) -> int | None:
        """Median of positive TVOC readings from the last 60 seconds."""
        cutoff = datetime.now().astimezone() - timedelta(seconds=60)
        with self._lock:
            values = [item.tvoc_ppb for item in self._minute_readings if item.taken_at >= cutoff and item.tvoc_ppb > 0]
        return round(median(values)) if values else None

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
                        self._readings.append(reading)
                        self._minute_readings.append(reading)
                        self._error = None
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
