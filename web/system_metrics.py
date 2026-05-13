from __future__ import annotations

import os
import platform
import time
from pathlib import Path


def _read_cpu_times():
    with open("/proc/stat", "r", encoding="utf-8") as handle:
        first_line = handle.readline().strip().split()
    values = [int(v) for v in first_line[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    return idle, total


def _read_cpu_percent(sample_seconds: float = 0.12):
    try:
        idle_1, total_1 = _read_cpu_times()
        time.sleep(sample_seconds)
        idle_2, total_2 = _read_cpu_times()
    except (FileNotFoundError, OSError, ValueError):
        return None

    total_delta = total_2 - total_1
    idle_delta = idle_2 - idle_1
    if total_delta <= 0:
        return None
    return round((1 - idle_delta / total_delta) * 100, 1)


def _read_memory_info():
    try:
        meminfo = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                key, value = line.split(":", 1)
                meminfo[key] = int(value.strip().split()[0])
    except (FileNotFoundError, OSError, ValueError):
        return None

    total_kb = meminfo.get("MemTotal")
    available_kb = meminfo.get("MemAvailable")
    if not total_kb or available_kb is None:
        return None

    used_kb = total_kb - available_kb
    return {
        "total_mb": round(total_kb / 1024, 1),
        "used_mb": round(used_kb / 1024, 1),
        "percent": round((used_kb / total_kb) * 100, 1),
    }


def _read_temperature():
    candidates = [
        Path("/sys/class/thermal/thermal_zone0/temp"),
        Path("/sys/devices/virtual/thermal/thermal_zone0/temp"),
    ]
    for path in candidates:
        try:
            value = path.read_text(encoding="utf-8").strip()
            return round(int(value) / 1000, 1)
        except (FileNotFoundError, OSError, ValueError):
            continue
    return None


def get_system_metrics():
    cpu_percent = _read_cpu_percent()
    memory = _read_memory_info()
    return {
        "platform": platform.platform(),
        "hostname": platform.node(),
        "is_raspberry_pi": platform.machine() in {"armv7l", "aarch64"} or os.path.exists("/proc/device-tree/model"),
        "cpu_percent": cpu_percent,
        "memory_percent": memory["percent"] if memory else None,
        "memory_used_mb": memory["used_mb"] if memory else None,
        "memory_total_mb": memory["total_mb"] if memory else None,
        "temperature_c": _read_temperature(),
    }
