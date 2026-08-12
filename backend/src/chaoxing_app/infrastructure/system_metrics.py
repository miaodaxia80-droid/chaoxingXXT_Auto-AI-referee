from __future__ import annotations

import ctypes
import platform
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ResourceMetric:
    value: float | None
    unit: str

    @property
    def available(self) -> bool:
        return self.value is not None


class _WindowsMemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _FileTime(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", ctypes.c_ulong),
        ("dwHighDateTime", ctypes.c_ulong),
    ]


def _file_time_value(value: _FileTime) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


class SystemMetricsSampler:
    """Small cross-platform sampler with no privileged or optional dependencies."""

    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._started_at = monotonic()
        self._last_cpu = self._read_cpu_times()

    @property
    def uptime_seconds(self) -> float:
        return max(self._monotonic() - self._started_at, 0.0)

    def cpu_percent(self) -> ResourceMetric:
        current = self._read_cpu_times()
        previous = self._last_cpu
        self._last_cpu = current
        if current is None or previous is None:
            return ResourceMetric(None, "percent")
        total_delta = current[0] - previous[0]
        idle_delta = current[1] - previous[1]
        if total_delta <= 0:
            return ResourceMetric(None, "percent")
        value = min(max((total_delta - idle_delta) / total_delta * 100, 0.0), 100.0)
        return ResourceMetric(round(value, 1), "percent")

    @staticmethod
    def _read_cpu_times() -> tuple[int, int] | None:
        if platform.system() == "Windows":
            idle_time = _FileTime()
            kernel = _FileTime()
            user = _FileTime()
            try:
                function = ctypes.windll.kernel32.GetSystemTimes
                if not function(
                    ctypes.byref(idle_time),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                ):
                    return None
            except (AttributeError, OSError, ValueError):
                return None
            return (
                _file_time_value(kernel) + _file_time_value(user),
                _file_time_value(idle_time),
            )
        try:
            fields = Path("/proc/stat").read_text(encoding="ascii").splitlines()[0].split()[1:]
            values = tuple(int(value) for value in fields)
            if len(values) < 4:
                return None
            idle_ticks = values[3] + (values[4] if len(values) > 4 else 0)
            return sum(values), idle_ticks
        except (OSError, IndexError, ValueError):
            return None

    def memory_percent(self) -> ResourceMetric:
        if platform.system() == "Windows":
            status = _WindowsMemoryStatus()
            status.dwLength = ctypes.sizeof(_WindowsMemoryStatus)
            try:
                kernel32 = ctypes.windll.kernel32
                function = kernel32.GlobalMemoryStatusEx
                if function(ctypes.byref(status)):
                    return ResourceMetric(round(float(status.dwMemoryLoad), 1), "percent")
            except (AttributeError, OSError, ValueError):
                return ResourceMetric(None, "percent")
        try:
            values: dict[str, int] = {}
            for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
                key, raw = line.split(":", 1)
                values[key] = int(raw.strip().split()[0])
            total = values["MemTotal"]
            available = values["MemAvailable"]
            return ResourceMetric(round((total - available) / total * 100, 1), "percent")
        except (OSError, KeyError, ValueError, ZeroDivisionError):
            return ResourceMetric(None, "percent")

    def temperature_celsius(self) -> ResourceMetric:
        roots = (Path("/sys/class/thermal"), Path("/sys/class/hwmon"))
        candidates: list[Path] = []
        for root in roots:
            try:
                candidates.extend(root.glob("thermal_zone*/temp"))
                candidates.extend(root.glob("hwmon*/temp*_input"))
            except OSError:
                continue
        values: list[float] = []
        for path in candidates:
            try:
                value = float(path.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                continue
            if value > 1000:
                value /= 1000
            if 0 < value < 150:
                values.append(value)
        if not values:
            return ResourceMetric(None, "celsius")
        return ResourceMetric(round(max(values), 1), "celsius")
