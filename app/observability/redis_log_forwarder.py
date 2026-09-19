"""Redis-backed shared log buffer for cross-process log aggregation.

Workers and the API process both push structured log events to a Redis
list.  The /v1/debug/logs endpoint reads from this shared buffer so the
debug tab shows logs from ALL containers (API, matching-worker,
dispatcher, browser-worker, etc.).
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import MutableMapping
from typing import Any

import redis

_REDIS_LOG_KEY = "jsa:debug:logs"
_REDIS_LOG_MAX = 10000  # trim to this many entries
_REDIS_LOG_TTL = 86400  # 24h TTL on the key

_redis_client: redis.Redis | None = None
_init_lock = threading.Lock()
# Every log call asks for the client. When Redis is down or wedged (a dead Docker port forward
# accepts the TCP connection and never answers), retrying on each call stalled the whole process,
# so sockets time out fast and a failed attempt is not repeated for a while.
_SOCKET_TIMEOUT_SECONDS = 0.5
_RETRY_AFTER_SECONDS = 30.0
_retry_not_before = 0.0


def _get_redis() -> redis.Redis | None:
    global _redis_client, _retry_not_before
    if _redis_client is not None:
        return _redis_client
    if time.monotonic() < _retry_not_before:
        return None
    with _init_lock:
        if _redis_client is not None:
            return _redis_client
        if time.monotonic() < _retry_not_before:
            return None
        redis_url = os.environ.get("APP_REDIS_URL", "redis://localhost:6379/0")
        try:
            client = redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=_SOCKET_TIMEOUT_SECONDS,
                socket_timeout=_SOCKET_TIMEOUT_SECONDS,
            )
            client.ping()
            _redis_client = client
            return client
        except Exception:
            _retry_not_before = time.monotonic() + _RETRY_AFTER_SECONDS
            return None


def redis_log_processor(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor that pushes every event to a Redis list."""
    client = _get_redis()
    if client is None:
        return event_dict

    try:
        entry = {
            "timestamp": str(event_dict.get("timestamp", "")),
            "level": str(event_dict.get("level", "info")),
            "event": str(event_dict.get("event", "")),
            "logger": str(event_dict.get("logger", "")),
            "process": os.environ.get("HOSTNAME", "api"),
        }
        # Include all non-standard fields
        skip = {"event", "level", "timestamp", "logger"}
        fields = {}
        for key, value in event_dict.items():
            if key not in skip and value is not None:
                fields[key] = value
        if fields:
            entry["fields"] = fields

        client.lpush(_REDIS_LOG_KEY, json.dumps(entry, ensure_ascii=False, default=str))
        client.ltrim(_REDIS_LOG_KEY, 0, _REDIS_LOG_MAX - 1)
        client.expire(_REDIS_LOG_KEY, _REDIS_LOG_TTL)
    except Exception:
        pass  # never break logging

    return event_dict


def read_redis_logs(
    *,
    min_level: str = "debug",
    limit: int = 200,
    since: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """Read log entries from the shared Redis buffer."""
    from app.observability.log_buffer import LOG_LEVELS

    client = _get_redis()
    if client is None:
        return []

    min_severity = LOG_LEVELS.get(min_level, 0)
    search_lower = search.strip().casefold() if search else None

    try:
        raw_entries = client.lrange(_REDIS_LOG_KEY, 0, limit * 3)
    except Exception:
        return []

    result: list[dict[str, Any]] = []
    for raw in raw_entries:
        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        entry_level = entry.get("level", "info")
        if LOG_LEVELS.get(entry_level, 0) < min_severity:
            continue
        if since and entry.get("timestamp", "") <= since:
            continue
        if search_lower:
            event_text = entry.get("event", "")
            fields = entry.get("fields", {})
            if isinstance(fields, dict):
                msg_str = json.dumps(fields, ensure_ascii=False)
            else:
                msg_str = str(fields)
            if search_lower not in event_text.casefold() and search_lower not in msg_str.casefold():
                continue

        # Build message from event + fields
        parts = [entry.get("event", "")]
        fields = entry.get("fields", {})
        if isinstance(fields, dict):
            if "method" in fields and "url" in fields:
                parts.append(f"{fields['method']} {fields['url']}")
            if "status_code" in fields:
                parts.append(f"→ {fields['status_code']}")
            elif "status" in fields:
                parts.append(f"→ {fields['status']}")
            if "duration_s" in fields:
                parts.append(f"({fields['duration_s']:.3f}s)")
            if "error_type" in fields:
                parts.append(f"[{fields['error_type']}]")
            if "error" in fields and isinstance(fields["error"], str):
                parts.append(fields["error"][:200])

        entry["message"] = " ".join(parts)
        entry.setdefault("process", "unknown")
        result.append(entry)
        if len(result) >= limit:
            break

    return result
