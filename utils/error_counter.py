"""
GoldPulse Alerts — In-memory error counters
=============================================
Tiny thread-safe per-module counters so a silent failure ("it just
stopped alerting") becomes visible as "ingestion.gdelt failed 12 times
today" via the /health command.

Counters are lost on restart — they are a cheap substitute for real
observability, not a permanent store. Module names follow the logger
names (e.g. "alerts.news", "ingestion.price").
"""

import threading
from collections import defaultdict

_lock = threading.Lock()
_COUNTERS: dict[str, int] = defaultdict(int)


def bump(module: str) -> None:
    """Increment the error count for a module (thread-safe)."""
    with _lock:
        _COUNTERS[module] += 1


def snapshot() -> dict[str, int]:
    """Copy of the current counters, sorted for stable display."""
    with _lock:
        return dict(sorted(_COUNTERS.items()))


def total() -> int:
    """Total errors across all modules."""
    with _lock:
        return sum(_COUNTERS.values())


def reset() -> None:
    """Clear all counters (used in tests)."""
    with _lock:
        _COUNTERS.clear()