from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Protocol


class RedisCoordinationClient(Protocol):
    async def set(self, name: str, value: str, *, nx: bool, ex: int) -> bool | None: ...

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int: ...

    async def incr(self, name: str) -> int: ...

    async def expire(self, name: str, seconds: int) -> bool: ...


@dataclass(frozen=True, slots=True)
class WorkerLease:
    resource_key: str
    owner_token: str
    expires_in_seconds: int


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    is_allowed: bool
    current_count: int
    limit: int
    retry_after_seconds: int


class RedisCoordinator:
    _RELEASE_SCRIPT = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        end
        return 0
    """

    def __init__(self, client: RedisCoordinationClient, namespace: str = "recruitment") -> None:
        self._client = client
        self._namespace = namespace

    async def acquire_lease(self, resource_key: str, *, lease_seconds: int) -> WorkerLease | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        owner_token = str(uuid.uuid4())
        was_acquired = await self._client.set(
            self._lease_key(resource_key), owner_token, nx=True, ex=lease_seconds
        )
        if not was_acquired:
            return None
        return WorkerLease(resource_key, owner_token, lease_seconds)

    async def release_lease(self, lease: WorkerLease) -> bool:
        deleted_count = await self._client.eval(
            self._RELEASE_SCRIPT,
            1,
            self._lease_key(lease.resource_key),
            lease.owner_token,
        )
        return deleted_count == 1

    async def check_domain_rate_limit(
        self,
        domain: str,
        *,
        limit: int,
        window_seconds: int,
        now_seconds: float | None = None,
    ) -> RateLimitDecision:
        if limit < 1 or window_seconds < 1:
            raise ValueError("limit and window_seconds must be positive")
        current_time = now_seconds if now_seconds is not None else time.time()
        window_number = int(current_time // window_seconds)
        redis_key = f"{self._namespace}:rate:{domain.casefold()}:{window_number}"
        current_count = await self._client.incr(redis_key)
        if current_count == 1:
            await self._client.expire(redis_key, window_seconds + 1)
        elapsed_seconds = int(current_time % window_seconds)
        return RateLimitDecision(
            is_allowed=current_count <= limit,
            current_count=current_count,
            limit=limit,
            retry_after_seconds=max(window_seconds - elapsed_seconds, 1),
        )

    def _lease_key(self, resource_key: str) -> str:
        return f"{self._namespace}:lease:{resource_key}"
