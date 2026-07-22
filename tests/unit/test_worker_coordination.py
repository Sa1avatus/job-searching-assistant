import asyncio

from app.workers.coordination import RedisCoordinator, WorkerLease


class FakeRedisClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.counters: dict[str, int] = {}

    async def set(self, name: str, value: str, *, nx: bool, ex: int) -> bool | None:
        if nx and name in self.values:
            return None
        self.values[name] = value
        return True

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        key, expected_value = keys_and_args
        if self.values.get(key) != expected_value:
            return 0
        del self.values[key]
        return 1

    async def incr(self, name: str) -> int:
        self.counters[name] = self.counters.get(name, 0) + 1
        return self.counters[name]

    async def expire(self, name: str, seconds: int) -> bool:
        return True


def test_only_one_worker_acquires_lease_and_owner_releases_it() -> None:
    async def run_test() -> None:
        client = FakeRedisClient()
        coordinator = RedisCoordinator(client)
        first_lease = await coordinator.acquire_lease("application:1", lease_seconds=30)
        second_lease = await coordinator.acquire_lease("application:1", lease_seconds=30)

        assert first_lease is not None
        assert second_lease is None
        wrong_owner = WorkerLease("application:1", "wrong", 30)
        assert await coordinator.release_lease(wrong_owner) is False
        assert await coordinator.release_lease(first_lease) is True

    asyncio.run(run_test())


def test_domain_rate_limit_blocks_requests_over_limit() -> None:
    async def run_test() -> None:
        coordinator = RedisCoordinator(FakeRedisClient())
        decisions = [
            await coordinator.check_domain_rate_limit(
                "example.test", limit=2, window_seconds=60, now_seconds=120
            )
            for _ in range(3)
        ]

        assert [decision.is_allowed for decision in decisions] == [True, True, False]
        assert decisions[-1].retry_after_seconds == 60

    asyncio.run(run_test())
