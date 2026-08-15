from collections import defaultdict
from threading import Lock


class MetricsRegistry:
    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = {}
        self._observations: dict[str, tuple[int, float]] = {}
        self._lock = Lock()

    @staticmethod
    def _validate_name(metric_name: str) -> None:
        if not metric_name.replace("_", "").isalnum():
            raise ValueError("metric_name must contain only letters, numbers, and underscores")

    def increment(self, metric_name: str, amount: int = 1) -> None:
        self._validate_name(metric_name)
        if amount < 0:
            raise ValueError("counter amount cannot be negative")
        with self._lock:
            self._counters[metric_name] += amount

    def set_gauge(self, metric_name: str, value: float) -> None:
        self._validate_name(metric_name)
        with self._lock:
            self._gauges[metric_name] = value

    def observe(self, metric_name: str, value: float) -> None:
        self._validate_name(metric_name)
        if value < 0:
            raise ValueError("observed value cannot be negative")
        with self._lock:
            count, total = self._observations.get(metric_name, (0, 0.0))
            self._observations[metric_name] = (count + 1, total + value)

    def render_prometheus(self) -> str:
        with self._lock:
            counters = "".join(
                f"# TYPE {name} counter\n{name} {count}\n"
                for name, count in sorted(self._counters.items())
            )
            gauges = "".join(
                f"# TYPE {name} gauge\n{name} {value:g}\n"
                for name, value in sorted(self._gauges.items())
            )
            summaries = "".join(
                (f"# TYPE {name} summary\n{name}_count {count}\n{name}_sum {total:g}\n")
                for name, (count, total) in sorted(self._observations.items())
            )
            return counters + gauges + summaries


metrics = MetricsRegistry()
