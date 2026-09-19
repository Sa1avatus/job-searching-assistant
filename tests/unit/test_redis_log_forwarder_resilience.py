import time

from app.observability import redis_log_forwarder as forwarder


def _reset(monkeypatch):
    monkeypatch.setattr(forwarder, "_redis_client", None)
    monkeypatch.setattr(forwarder, "_retry_not_before", 0.0)


def test_an_unreachable_redis_is_tried_once_then_left_alone(monkeypatch) -> None:
    _reset(monkeypatch)
    attempts = []

    def failing(*args, **kwargs):
        attempts.append(kwargs)
        raise ConnectionError("down")

    monkeypatch.setattr(forwarder.redis, "from_url", failing)

    assert [forwarder._get_redis() for _ in range(5)] == [None] * 5
    assert len(attempts) == 1  # not once per log line
    assert attempts[0]["socket_connect_timeout"] <= 1 and attempts[0]["socket_timeout"] <= 1


def test_it_retries_after_the_backoff_window(monkeypatch) -> None:
    _reset(monkeypatch)
    calls = []

    class Client:
        def ping(self):
            calls.append("ping")

    def flaky(*args, **kwargs):
        if not calls and not getattr(flaky, "failed", False):
            flaky.failed = True
            raise ConnectionError("down")
        return Client()

    monkeypatch.setattr(forwarder.redis, "from_url", flaky)
    assert forwarder._get_redis() is None
    monkeypatch.setattr(forwarder, "_retry_not_before", time.monotonic() - 1)  # window elapsed

    assert isinstance(forwarder._get_redis(), Client)
    assert forwarder._get_redis() is forwarder._get_redis()  # cached afterwards
