"""Operational helpers: JSON logging, rate limiting and simple metrics."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter, defaultdict, deque
from contextvars import ContextVar

request_id: ContextVar[str] = ContextVar("request_id", default="-")

_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line, ready for any log platform."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "event": record.getMessage(),
            "request_id": request_id.get(),
        }
        entry.update({k: v for k, v in record.__dict__.items() if k not in _STANDARD})
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("find_my_service")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False


class RateLimiter:
    """Allows `limit` requests per client in any rolling 60-second window."""

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client: str) -> tuple[bool, int]:
        """Returns whether the request is allowed and seconds until the next free slot."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[client]
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False, max(1, int(self.window - (now - hits[0])) + 1)
            hits.append(now)
            return True, 0


class Metrics:
    """In-memory counters, exposed at /metrics."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self._lock = threading.Lock()

    def increment(self, *names: str) -> None:
        with self._lock:
            self.counts.update(names)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(sorted(self.counts.items()))
