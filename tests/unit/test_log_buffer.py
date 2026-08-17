"""Tests for the in-process log ring buffer and processor."""

from app.observability.log_buffer import (
    LOG_LEVELS,
    LogEntry,
    LogRingBuffer,
    log_buffer_processor,
)


def test_ring_buffer_append_and_query() -> None:
    buf = LogRingBuffer(maxsize=100)
    buf.append(LogEntry(timestamp="2026-01-01T00:00:00Z", level="info", event="start", logger="test", message="app started"))
    buf.append(LogEntry(timestamp="2026-01-01T00:00:01Z", level="error", event="fail", logger="test", message="something broke"))
    buf.append(LogEntry(timestamp="2026-01-01T00:00:02Z", level="debug", event="detail", logger="test", message="trace info"))

    # All levels
    all_entries = buf.query(min_level="verbose", limit=100)
    assert len(all_entries) == 3

    # Error and above
    errors = buf.query(min_level="error", limit=100)
    assert len(errors) == 1
    assert errors[0].event == "fail"

    # Info and above (info, error — not debug)
    info_plus = buf.query(min_level="info", limit=100)
    assert len(info_plus) == 2


def test_ring_buffer_search_filter() -> None:
    buf = LogRingBuffer(maxsize=100)
    buf.append(LogEntry(timestamp="2026-01-01T00:00:00Z", level="info", event="sync", logger="email", message="syncing mail"))
    buf.append(LogEntry(timestamp="2026-01-01T00:00:01Z", level="error", event="fail", logger="email", message="connection timeout"))
    buf.append(LogEntry(timestamp="2026-01-01T00:00:02Z", level="info", event="match", logger="matching", message="matching vacancy"))

    results = buf.query(min_level="verbose", limit=100, search="timeout")
    assert len(results) == 1
    assert results[0].event == "fail"


def test_ring_buffer_since_filter() -> None:
    buf = LogRingBuffer(maxsize=100)
    buf.append(LogEntry(timestamp="2026-01-01T00:00:00Z", level="info", event="a", logger="", message="old"))
    buf.append(LogEntry(timestamp="2026-01-01T00:01:00Z", level="info", event="b", logger="", message="new"))

    results = buf.query(min_level="verbose", limit=100, since="2026-01-01T00:00:00Z")
    assert len(results) == 1
    assert results[0].event == "b"


def test_ring_buffer_overflow() -> None:
    buf = LogRingBuffer(maxsize=10)
    for i in range(20):
        buf.append(LogEntry(timestamp=f"2026-01-01T00:00:{i:02d}Z", level="info", event=f"e{i}", logger="", message=f"msg{i}"))

    assert len(buf) <= 10
    entries = buf.query(min_level="verbose", limit=100)
    # Most recent should be present
    assert entries[0].event == "e19"


def test_ring_buffer_clear() -> None:
    buf = LogRingBuffer(maxsize=100)
    buf.append(LogEntry(timestamp="2026-01-01T00:00:00Z", level="info", event="x", logger="", message="x"))
    buf.clear()
    assert len(buf) == 0


def test_log_buffer_processor_captures_event() -> None:
    event_dict = {
        "event": "http_request",
        "level": "info",
        "timestamp": "2026-01-01T00:00:00Z",
        "logger": "api",
        "method": "GET",
        "url": "/v1/users",
        "status_code": 200,
        "duration_s": 0.15,
    }
    result = log_buffer_processor(None, None, event_dict)
    # Processor passes through
    assert result["event"] == "http_request"

    from app.observability.log_buffer import get_log_buffer
    entries = get_log_buffer().query(min_level="info", limit=10, search="http_request")
    assert any(e.event == "http_request" for e in entries)


def test_log_levels_ordering() -> None:
    assert LOG_LEVELS["verbose"] < LOG_LEVELS["debug"]
    assert LOG_LEVELS["debug"] < LOG_LEVELS["info"]
    assert LOG_LEVELS["info"] < LOG_LEVELS["warning"]
    assert LOG_LEVELS["warning"] < LOG_LEVELS["error"]
    assert LOG_LEVELS["error"] < LOG_LEVELS["critical"]
    assert LOG_LEVELS["critical"] < LOG_LEVELS["major"]
