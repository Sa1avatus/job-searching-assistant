"""In-process ring buffer for structured log events.

A structlog processor feeds every log event into a thread-safe ring buffer.
The /v1/debug/logs endpoint reads from this buffer to power the debug tab.
"""

from __future__ import annotations

import threading
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Severity ordering for filtering (higher number = more severe)
LOG_LEVELS: dict[str, int] = {
    "verbose": 0,
    "debug": 1,
    "info": 2,
    "warning": 3,
    "error": 4,
    "critical": 5,
    "major": 6,
}

# Map standard structlog/Python log names to our canonical names
_LEVEL_ALIASES: dict[str, str] = {
    "verbose": "verbose",
    "debug": "debug",
    "info": "info",
    "warning": "warning",
    "warn": "warning",
    "error": "error",
    "critical": "critical",
    "fatal": "critical",
    "major": "major",
}


@dataclass(slots=True)
class LogEntry:
    timestamp: str  # ISO 8601 UTC
    level: str  # canonical level name
    event: str  # structlog event key
    logger: str  # logger name
    message: str  # human-readable summary
    fields: dict[str, Any] = field(default_factory=dict)


class LogRingBuffer:
    """Thread-safe bounded ring buffer for log entries."""

    def __init__(self, maxsize: int = 5000) -> None:
        self._maxsize = maxsize
        self._buffer: list[LogEntry] = []
        self._lock = threading.Lock()
        self._seq: int = 0  # monotonic counter for ordering

    def append(self, entry: LogEntry) -> None:
        with self._lock:
            self._seq += 1
            self._buffer.append(entry)
            if len(self._buffer) > self._maxsize:
                # Drop oldest 10% to amortise the shift cost
                drop = self._maxsize // 10
                self._buffer = self._buffer[drop:]

    def query(
        self,
        *,
        min_level: str = "debug",
        limit: int = 200,
        since: str | None = None,
        search: str | None = None,
    ) -> list[LogEntry]:
        """Return entries matching filters, newest first."""
        min_severity = LOG_LEVELS.get(min_level, 0)
        search_lower = search.strip().casefold() if search else None

        with self._lock:
            entries = list(self._buffer)

        result: list[LogEntry] = []
        for entry in reversed(entries):
            if LOG_LEVELS.get(entry.level, 0) < min_severity:
                continue
            if since and entry.timestamp <= since:
                continue
            if search_lower:
                in_message = search_lower in entry.message.casefold()
                in_event = search_lower in entry.event.casefold()
                if not in_message and not in_event:
                    continue
            result.append(entry)
            if len(result) >= limit:
                break
        return result

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)


# --- Global singleton ---
_log_buffer = LogRingBuffer(maxsize=5000)


def get_log_buffer() -> LogRingBuffer:
    return _log_buffer


def log_buffer_processor(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor that captures every event into the ring buffer."""
    level_raw = event_dict.get("level", "info")
    level = _LEVEL_ALIASES.get(str(level_raw).casefold(), "info")
    event = str(event_dict.get("event", ""))
    timestamp = str(event_dict.get("timestamp", datetime.now(UTC).isoformat()))
    logger_name = str(event_dict.get("logger", ""))

    # Build human-readable message
    message_parts: list[str] = []
    if event:
        message_parts.append(event)

    # Extract API/integration details if present
    fields: dict[str, Any] = {}
    api_keys = (
        "method",
        "url",
        "status_code",
        "status",
        "duration_s",
        "duration_ms",
        "request_url",
        "request_method",
        "response_status",
        "error_type",
        "error",
        "detail",
        "path",
        "query",
        "body",
        "headers",
        "http_status",
        "latency_ms",
        "operation",
    )
    for key in api_keys:
        if key in event_dict:
            fields[key] = event_dict[key]

    # Include all other non-standard fields
    skip_keys = {"event", "level", "timestamp", "logger"}
    for key, value in event_dict.items():
        if key not in skip_keys and key not in fields:
            fields[key] = value

    if fields:
        # Add key details to the message for quick scanning
        if "method" in fields and "url" in fields:
            message_parts.append(f"{fields['method']} {fields['url']}")
        if "status_code" in fields:
            message_parts.append(f"→ {fields['status_code']}")
        elif "status" in fields:
            message_parts.append(f"→ {fields['status']}")
        if "duration_s" in fields:
            message_parts.append(f"({fields['duration_s']:.3f}s)")
        elif "duration_ms" in fields:
            message_parts.append(f"({fields['duration_ms']}ms)")
        if "error_type" in fields:
            message_parts.append(f"[{fields['error_type']}]")
        if "error" in fields and isinstance(fields["error"], str):
            message_parts.append(fields["error"][:200])

    message = " ".join(message_parts) if message_parts else event

    entry = LogEntry(
        timestamp=timestamp,
        level=level,
        event=event,
        logger=logger_name,
        message=message,
        fields=fields,
    )
    _log_buffer.append(entry)

    # Pass through — this processor does not modify the event
    return event_dict
