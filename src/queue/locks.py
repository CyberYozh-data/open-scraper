"""Distributed per-session lock: redis-py's built-in `Lock` (atomic SET NX PX
acquire + token-checked Lua release) wrapped with a watchdog that reacquires at
~1/3 of the TTL, so a long scrape/login can't lose the lock mid-operation.
Serializes all scrapes/logins touching one session across worker containers."""
from __future__ import annotations

import asyncio

import redis.asyncio as aioredis
from redis.exceptions import LockError


class LockTimeout(TimeoutError):
    pass


class RedisLock:
    """Async context manager around `redis.asyncio` `Lock` with TTL renewal.

    The built-in lock owns the hard parts (atomic acquire, ownership-checked
    release); we only add the renewal watchdog it doesn't provide.
    """

    def __init__(
        self,
        client: aioredis.Redis,
        key: str,
        *,
        ttl_ms: int = 30_000,
        acquire_timeout_s: float = 60.0,
        retry_delay_s: float = 0.2,
    ) -> None:
        self._key = key
        self._ttl_s = ttl_ms / 1000
        self._lock = client.lock(
            key,
            timeout=self._ttl_s,
            sleep=retry_delay_s,
            blocking=True,
            blocking_timeout=acquire_timeout_s,
            # Token in instance state, not threading.local: acquire, the renewal
            # watchdog, and release are separate coroutines that may run on
            # different threads if any is ever moved to an executor.
            thread_local=False,
        )
        self._renew_task: asyncio.Task | None = None

    async def __aenter__(self) -> "RedisLock":
        if not await self._lock.acquire():
            raise LockTimeout(self._key)
        self._renew_task = asyncio.create_task(self._renew_loop())
        return self

    async def __aexit__(self, *exc) -> None:
        if self._renew_task:
            self._renew_task.cancel()
            try:
                await self._renew_task
            except asyncio.CancelledError:
                pass
        try:
            await self._lock.release()
        except LockError:
            # Already expired, or another owner stomped the key — the built-in
            # release is ownership-checked, so there's nothing of ours to free.
            pass

    async def _renew_loop(self) -> None:
        """Reset the TTL to full at ~1/3 of it for as long as the lock is held."""
        while True:
            try:
                await asyncio.sleep(self._ttl_s / 3)
            except asyncio.CancelledError:
                return
            try:
                await self._lock.reacquire()
            except asyncio.CancelledError:
                return
            except LockError:
                return  # lost the lock (expired); stop renewing
