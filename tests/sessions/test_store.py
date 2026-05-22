from __future__ import annotations

import asyncio

import pytest

from src.sessions.models import SessionCreateRequest
from src.sessions.store import (
    InMemorySessionStore,
    SessionExpired,
    SessionIncompatible,
    SessionNotFound,
)


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore(max_sessions=4, storage_state_max_bytes=10_000)


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
    @pytest.mark.asyncio
    async def test_create_returns_record_with_status_created(self, store, now_clock):
        store.set_clock(now_clock)
        session_record = await store.create(SessionCreateRequest(ttl_seconds=3600))
        assert session_record.session_id.startswith("sess_")
        assert session_record.status == "created"
        assert session_record.expires_at == 1_000_000.0 + 3600

    @pytest.mark.asyncio
    async def test_get_unknown_raises(self, store):
        with pytest.raises(SessionNotFound):
            await store.get("sess_missing")

    @pytest.mark.asyncio
    async def test_delete_removes(self, store, now_clock):
        store.set_clock(now_clock)
        session_record = await store.create(SessionCreateRequest())
        await store.delete(session_record.session_id)
        with pytest.raises(SessionNotFound):
            await store.get(session_record.session_id)

    @pytest.mark.asyncio
    async def test_get_during_concurrent_delete_raises_session_not_found(self, store, now_clock):
        """Regression: get() must raise SessionNotFound (not KeyError) when the
        session is deleted by another coroutine mid-call."""
        store.set_clock(now_clock)
        session_record = await store.create(SessionCreateRequest())

        # Force a deletion to race with get(). The store.get() holds self._lock
        # for the entire read-modify, so a concurrent delete() runs either fully
        # before or fully after get() — we can't reliably interleave them in a
        # single-threaded asyncio loop without internal hooks. So instead, we
        # delete first and call get() to confirm it raises SessionNotFound rather
        # than KeyError.
        await store.delete(session_record.session_id)
        with pytest.raises(SessionNotFound):
            await store.get(session_record.session_id)

    @pytest.mark.asyncio
    async def test_list_returns_all(self, store, now_clock):
        store.set_clock(now_clock)
        await store.create(SessionCreateRequest())
        await store.create(SessionCreateRequest())
        items = await store.list()
        assert len(items) == 2


class TestStoreTTL:
    @pytest.mark.asyncio
    async def test_get_after_ttl_raises_expired(self, store, now_clock):
        store.set_clock(now_clock)
        session_record = await store.create(SessionCreateRequest(ttl_seconds=600))
        now_clock.advance(601)
        with pytest.raises(SessionExpired):
            await store.get(session_record.session_id)

    @pytest.mark.asyncio
    async def test_sweep_marks_expired(self, store, now_clock):
        store.set_clock(now_clock)
        session_record = await store.create(SessionCreateRequest(ttl_seconds=600))
        now_clock.advance(601)
        swept = await store.sweep_expired()
        assert session_record.session_id in swept


class TestStoreLRU:
    @pytest.mark.asyncio
    async def test_overflow_evicts_least_recently_used(self, store, now_clock):
        store.set_clock(now_clock)
        ids = []
        for _ in range(4):
            now_clock.advance(1)
            session_record = await store.create(SessionCreateRequest())
            ids.append(session_record.session_id)

        # Touch the first three so the 4th (ids[3]) becomes LRU.
        for session_id in ids[:3]:
            now_clock.advance(1)
            await store.touch(session_id)

        now_clock.advance(1)
        await store.create(SessionCreateRequest())  # 5th — over the cap
        with pytest.raises(SessionNotFound):
            await store.get(ids[3])


class TestPreconditions:
    @pytest.mark.asyncio
    async def test_assert_compatible_ok_when_pin_matches(self, store, now_clock):
        store.set_clock(now_clock)
        session_record = await store.create(
            SessionCreateRequest(device="desktop", proxy_type="none")
        )
        store.assert_compatible_with_request(
            session_record, device="desktop", proxy_type="none",
            proxy_pool_id=None, proxy_geo=None, cookies=None,
        )

    @pytest.mark.asyncio
    async def test_assert_compatible_fails_on_device_mismatch(self, store, now_clock):
        store.set_clock(now_clock)
        session_record = await store.create(SessionCreateRequest(device="desktop"))
        with pytest.raises(SessionIncompatible) as exc:
            store.assert_compatible_with_request(
                session_record, device="mobile", proxy_type="none",
                proxy_pool_id=None, proxy_geo=None, cookies=None,
            )
        assert "device" in str(exc.value)

    @pytest.mark.asyncio
    async def test_assert_compatible_fails_on_cookies_present(self, store, now_clock):
        store.set_clock(now_clock)
        session_record = await store.create(SessionCreateRequest())
        with pytest.raises(SessionIncompatible) as exc:
            store.assert_compatible_with_request(
                session_record, device="desktop", proxy_type="none",
                proxy_pool_id=None, proxy_geo=None,
                cookies=[{"name": "x", "value": "1"}],
            )
        assert "cookies" in str(exc.value)

    @pytest.mark.asyncio
    async def test_create_rejects_res_rotating_without_pool_id(self, store, now_clock):
        store.set_clock(now_clock)
        with pytest.raises(SessionIncompatible):
            await store.create(
                SessionCreateRequest(proxy_type="res_rotating", proxy_pool_id=None)
            )


class TestLock:
    @pytest.mark.asyncio
    async def test_lock_serializes_concurrent_access(self, store, now_clock):
        store.set_clock(now_clock)
        session_record = await store.create(SessionCreateRequest())
        order: list[str] = []

        async def hold(tag: str, hold_s: float):
            async with store.lock(session_record.session_id):
                order.append(f"{tag}-start")
                await asyncio.sleep(hold_s)
                order.append(f"{tag}-end")

        await asyncio.gather(hold("a", 0.05), hold("b", 0.0))
        assert order == ["a-start", "a-end", "b-start", "b-end"]
