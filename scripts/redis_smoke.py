import asyncio
from typing import cast

from redis.asyncio import Redis

from app.workers.coordination import RedisCoordinationClient, RedisCoordinator


async def main() -> None:
    redis = Redis.from_url("redis://localhost:6379/0", decode_responses=True)
    await redis.flushdb()
    coordinator = RedisCoordinator(cast(RedisCoordinationClient, redis))
    lease = await coordinator.acquire_lease("smoke:application", lease_seconds=30)
    duplicate_lease = await coordinator.acquire_lease("smoke:application", lease_seconds=30)
    rate_decisions = [
        await coordinator.check_domain_rate_limit(
            "example.test", limit=2, window_seconds=60, now_seconds=120
        )
        for _ in range(3)
    ]
    if lease is None or duplicate_lease is not None:
        raise RuntimeError("Lease exclusivity check failed")
    if [decision.is_allowed for decision in rate_decisions] != [True, True, False]:
        raise RuntimeError("Rate limit check failed")
    if not await coordinator.release_lease(lease):
        raise RuntimeError("Owner release check failed")
    await redis.aclose()
    print("redis-coordination-smoke: ok")


if __name__ == "__main__":
    asyncio.run(main())
