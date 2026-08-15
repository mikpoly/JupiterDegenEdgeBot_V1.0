from __future__ import annotations

import os
import psutil


class MemoryPressure(RuntimeError):
    pass


def available_gb() -> float:
    return psutil.virtual_memory().available / (1024 ** 3)


def process_rss_mb(pid: int | None = None) -> float:
    proc = psutil.Process(pid or os.getpid())
    total = proc.memory_info().rss
    for child in proc.children(recursive=True):
        try:
            total += child.memory_info().rss
        except psutil.Error:
            pass
    return total / (1024 ** 2)


def require_free_memory(min_free_gb: float) -> None:
    free = available_gb()
    if free < min_free_gb:
        raise MemoryPressure(
            f"Mémoire disponible insuffisante: {free:.2f} Go < {min_free_gb:.2f} Go"
        )
