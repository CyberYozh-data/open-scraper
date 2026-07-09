from __future__ import annotations

import asyncio

import fakeredis.aioredis
import pytest
import pytest_asyncio

from src.sessions.models import SessionCreateRequest
from src.sessions.redis_store import RedisSessionStore
from src.sessions.store import (
    SessionExpired,
    SessionIncompatible,
    SessionNotFound,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def redis_client():
    c = fakeredis.aioredis.FakeRedis()
    yield c
    await c.aclose()


@pytest_asyncio.fixture
async def store(redis_client):
    return RedisSessionStore(redis_client, max_sessions=4, storage_state_max_bytes=10_000)


@pytest.fixture
def now_clock():
    """Mutable clock the tests advance manually."""
    clock_ref = [1_000_000.0]

    def get_now() -> float:
        return clock_ref[0]

    def advance(dt: float) -> None:
        clock_ref[0] += dt

    get_now.advance = advance  # type: ignore[attr-defined]
    return get_now


class TestStoreLifecycle:
    async def test_create_returns_record_with_status_created(self, store, now_clock):
        store.set_clock(now_clock)
        record = await store.create(SessionCreateRequest(ttl_seconds=3600))
        assert record.session_id.startswith("sess_")
        assert record.status == "created"
        assert record.expires_at == 1_000_000.0 + 3600

    async def test_get_unknown_raises(self, store):
        with pytest.raises(SessionNotFound):
            await store.get("sess_missing")

    async def test_delete_removes(self, store, now_clock):
        store.set_clock(now_clock)
        record = await store.create(SessionCreateRequest())
        await store.delete(record.session_id)
        with pytest.raises(SessionNotFound):
            await store.get(record.session_id)

    async def test_delete_unknown_raises(self, store):
        with pytest.raises(SessionNotFound):
            await store.delete("sess_missing")

    async def test_get_during_concurrent_delete_raises_session_not_found(self, store, now_clock):
        """Regression: get() must raise SessionNotFound (not KeyError) when the
        session is deleted before the call."""
        store.set_clock(now_clock)
        record = await store.create(SessionCreateRequest())
        await store.delete(record.session_id)
        with pytest.raises(SessionNotFound):
            await store.get(record.session_id)

    async def test_list_returns_all(self, store, now_clock):
        store.set_clock(now_clock)
        await store.create(SessionCreateRequest())
        await store.create(SessionCreateRequest())
        items = await store.list()
        assert len(items) == 2


class TestStoreTTL:
    async def test_get_after_ttl_raises_expired(self, store, now_clock):
        store.set_clock(now_clock)
        record = await store.create(SessionCreateRequest(ttl_seconds=600))
        now_clock.advance(601)
        with pytest.raises(SessionExpired):
            await store.get(record.session_id)

    async def test_sweep_marks_expired(self, store, now_clock):
        store.set_clock(now_clock)
        record = await store.create(SessionCreateRequest(ttl_seconds=600))
        now_clock.advance(601)
        swept = await store.sweep_expired()
        assert record.session_id in swept

    async def test_sweep_updates_status_in_redis(self, store, now_clock):
        store.set_clock(now_clock)
        record = await store.create(SessionCreateRequest(ttl_seconds=600))
        now_clock.advance(601)
        await store.sweep_expired()
        # After sweep the record should have status=expired and no storage_state
        peeked = await store._peek(record.session_id)
        assert peeked.status == "expired"
        assert peeked.storage_state is None
        assert peeked.storage_state_bytes == 0


class TestStoreLRU:
    async def test_overflow_evicts_least_recently_used(self, store, now_clock):
        store.set_clock(now_clock)
        ids = []
        for _ in range(4):
            now_clock.advance(1)
            record = await store.create(SessionCreateRequest())
            ids.append(record.session_id)

        # Touch the first three so the 4th (ids[3]) becomes LRU.
        for sid in ids[:3]:
            now_clock.advance(1)
            await store.touch(sid)

        now_clock.advance(1)
        await store.create(SessionCreateRequest())  # 5th — over the cap
        with pytest.raises(SessionNotFound):
            await store.get(ids[3])


class TestPreconditions:
    async def test_assert_compatible_ok_when_pin_matches(self, store, now_clock):
        store.set_clock(now_clock)
        record = await store.create(
            SessionCreateRequest(device="desktop", proxy_type="none")
        )
        store.assert_compatible_with_request(
            record, device="desktop", proxy_type="none",
            proxy_pool_id=None, proxy_geo=None, cookies=None,
        )

    async def test_assert_compatible_fails_on_device_mismatch(self, store, now_clock):
        store.set_clock(now_clock)
        record = await store.create(SessionCreateRequest(device="desktop"))
        with pytest.raises(SessionIncompatible) as exc:
            store.assert_compatible_with_request(
                record, device="mobile", proxy_type="none",
                proxy_pool_id=None, proxy_geo=None, cookies=None,
            )
        assert "device" in str(exc.value)

    async def test_assert_compatible_fails_on_cookies_present(self, store, now_clock):
        store.set_clock(now_clock)
        record = await store.create(SessionCreateRequest())
        with pytest.raises(SessionIncompatible) as exc:
            store.assert_compatible_with_request(
                record, device="desktop", proxy_type="none",
                proxy_pool_id=None, proxy_geo=None,
                cookies=[{"name": "x", "value": "1"}],
            )
        assert "cookies" in str(exc.value)

    async def test_create_rejects_res_rotating_without_pool_id(self, store, now_clock):
        store.set_clock(now_clock)
        with pytest.raises(SessionIncompatible):
            await store.create(
                SessionCreateRequest(proxy_type="res_rotating", proxy_pool_id=None)
            )


class TestLock:
    async def test_lock_serializes_concurrent_access(self, store, now_clock):
        store.set_clock(now_clock)
        record = await store.create(SessionCreateRequest())
        order: list[str] = []

        async def hold(tag: str, hold_s: float):
            async with store.lock(record.session_id):
                order.append(f"{tag}-start")
                await asyncio.sleep(hold_s)
                order.append(f"{tag}-end")

        await asyncio.gather(hold("a", 0.05), hold("b", 0.0))
        assert order == ["a-start", "a-end", "b-start", "b-end"]


class TestSetStatus:
    async def test_set_status_updates_record(self, store, now_clock):
        store.set_clock(now_clock)
        record = await store.create(SessionCreateRequest())
        await store.set_status(record.session_id, "logging_in")
        peeked = await store._peek(record.session_id)
        assert peeked.status == "logging_in"

    async def test_set_status_with_last_error(self, store, now_clock):
        store.set_clock(now_clock)
        record = await store.create(SessionCreateRequest())
        await store.set_status(record.session_id, "failed", last_error="boom")
        peeked = await store._peek(record.session_id)
        assert peeked.status == "failed"
        assert peeked.last_error == "boom"

    async def test_set_status_missing_raises(self, store):
        with pytest.raises(SessionNotFound):
            await store.set_status("sess_missing", "ready")


class TestUpdateStorageState:
    async def test_update_storage_state_happy_path(self, store, now_clock):
        store.set_clock(now_clock)
        record = await store.create(SessionCreateRequest())
        state = {"cookies": [{"name": "sid", "value": "abc"}], "origins": []}
        updated = await store.update_storage_state(record.session_id, state)
        assert updated.storage_state is not None
        assert updated.storage_state_bytes > 0

    async def test_update_storage_state_over_cap_raises(self, store, now_clock):
        store.set_clock(now_clock)
        record = await store.create(SessionCreateRequest())
        # Build a payload that exceeds the 10_000 byte cap:
        # 50 cookies × ~220 bytes each ≈ 11 000 bytes after normalization.
        big_state = {
            "cookies": [{"name": "x" * 100, "value": "y" * 100}] * 50,
            "origins": [],
        }
        with pytest.raises(SessionIncompatible) as exc:
            await store.update_storage_state(record.session_id, big_state)
        assert "storage_state exceeds" in str(exc.value)

    async def test_update_storage_state_missing_raises(self, store):
        with pytest.raises(SessionNotFound):
            await store.update_storage_state("sess_missing", {"cookies": [], "origins": []})


class TestDeadlockAvoidance:
    """set_status and update_storage_state inside a public lock must not deadlock."""

    async def test_set_status_under_public_lock_does_not_deadlock(self, store, now_clock):
        store.set_clock(now_clock)
        record = await store.create(SessionCreateRequest())
        # Hold the public session lock while calling set_status (mimics login flow).
        async with store.lock(record.session_id):
            await store.set_status(record.session_id, "logging_in")
        peeked = await store._peek(record.session_id)
        assert peeked.status == "logging_in"

    async def test_update_storage_state_under_public_lock_does_not_deadlock(
        self, store, now_clock
    ):
        store.set_clock(now_clock)
        record = await store.create(SessionCreateRequest())
        state = {"cookies": [], "origins": []}
        async with store.lock(record.session_id):
            await store.update_storage_state(record.session_id, state)
        peeked = await store._peek(record.session_id)
        assert peeked.storage_state == {"cookies": [], "origins": []}
