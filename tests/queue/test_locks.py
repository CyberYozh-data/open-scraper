from __future__ import annotations

import asyncio

import fakeredis.aioredis
import pytest
import pytest_asyncio

from src.queue.locks import LockTimeout, RedisLock

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client():
    c = fakeredis.aioredis.FakeRedis()
    yield c
    await c.aclose()


async def test_mutual_exclusion(client):
    order = []

    async def worker(n):
        async with RedisLock(client, "k", ttl_ms=5000, retry_delay_s=0.01):
            order.append(("in", n))
            await asyncio.sleep(0.05)
            order.append(("out", n))

    await asyncio.gather(worker(1), worker(2))
    # the two critical sections never interleave
    assert order in (
        [("in", 1), ("out", 1), ("in", 2), ("out", 2)],
        [("in", 2), ("out", 2), ("in", 1), ("out", 1)],
    )


async def test_acquire_timeout(client):
    async with RedisLock(client, "k", ttl_ms=60000):
        with pytest.raises(LockTimeout):
            async with RedisLock(client, "k", acquire_timeout_s=0.2, retry_delay_s=0.05):
                pass


async def test_release_only_own_token(client):
    lock = RedisLock(client, "k", ttl_ms=60000)
    async with lock:
        # someone else stomps the key
        await client.set("k", "intruder")
    # our release must NOT delete the intruder's value
    assert await client.get("k") == b"intruder"


async def test_renewal_keeps_lock_alive(client):
    # ttl 300ms, hold for ~0.7s — renewal at ttl/3 must keep it
    async with RedisLock(client, "k", ttl_ms=300):
        await asyncio.sleep(0.7)
        assert await client.get("k") is not None
