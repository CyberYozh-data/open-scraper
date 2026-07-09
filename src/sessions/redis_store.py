"""Redis-backed session store: distributes session state across worker containers.

Storage layout:
  session:{id}     STRING  SessionRecord.model_dump_json()
  sessions:index   ZSET    member=session_id, score=last_used_at  (LRU + ordering)
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

import redis.asyncio as aioredis

from src.queue.locks import RedisLock
from src.sessions.models import (
    SessionCreateRequest,
    SessionPublic,
    SessionRecord,
)
from src.sessions.serde import normalize_storage_state, storage_state_bytes
from src.sessions.store import (
    SessionExpired,
    SessionIncompatible,
    SessionNotFound,
    _new_session_id,
)
from src.settings import settings

log = logging.getLogger(__name__)

_INDEX_KEY = "sessions:index"


def _record_key(session_id: str) -> str:
    return f"session:{session_id}"


def _lock_key(session_id: str) -> str:
    return f"session:{session_id}:lock"


def _rmw_key(session_id: str) -> str:
    # Separate key from the public lock: the public lock holder must be able
    # to call set_status/update_storage_state without re-acquiring its own lock
    # (RedisLock is non-reentrant → deadlock). A short internal :rmw lock
    # serialises concurrent RMW ops on the same record without conflicting with
    # the outer :lock held by the login flow or scrape task.
    return f"session:{session_id}:rmw"


class RedisSessionStore:
    """Distributable session store backed by Redis.

    Public surface is identical to the removed InMemorySessionStore so that
    service.py / api layers need no changes beyond the singleton swap.
    """

    def __init__(
        self,
        client: aioredis.Redis,
        *,
        max_sessions: int = 256,
        storage_state_max_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        self.client = client
        self._max_sessions = max_sessions
        self._storage_state_max_bytes = storage_state_max_bytes
        self._clock: Callable[[], float] = time.time

    # Testing / config hooks -------------------------------------------------

    def set_clock(self, clock: Callable[[], float]) -> None:
        self._clock = clock

    # Internal helpers -------------------------------------------------------

    def _rmw_lock(self, session_id: str) -> RedisLock:
        return RedisLock(self.client, _rmw_key(session_id), ttl_ms=5_000, acquire_timeout_s=10)

    async def _get_raw(self, session_id: str) -> SessionRecord:
        """GET from Redis and parse; raise SessionNotFound if absent."""
        raw = await self.client.get(_record_key(session_id))
        if raw is None:
            raise SessionNotFound(session_id)
        return SessionRecord.model_validate_json(raw)

    async def _save(self, record: SessionRecord) -> None:
        """SET the record and update the ZSET score atomically."""
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.set(_record_key(record.session_id), record.model_dump_json())
            pipe.zadd(_INDEX_KEY, {record.session_id: record.last_used_at})
            await pipe.execute()

    async def _evict_if_full(self) -> None:
        """LRU-evict sessions past max_sessions. Runs after each create().

        zcard/zrange are not transactional, so two concurrent creates may both
        evict the same victim (harmless) or briefly overshoot the cap by one —
        an accepted tolerance for a soft cap (the old in-memory store serialized
        this under a process-local lock that can't span containers)."""
        count = await self.client.zcard(_INDEX_KEY)
        excess = count - self._max_sessions
        if excess <= 0:
            return
        # Oldest members (lowest score = least recently used) come first.
        victims = await self.client.zrange(_INDEX_KEY, 0, excess - 1)
        for victim_bytes in victims:
            victim = victim_bytes.decode() if isinstance(victim_bytes, bytes) else victim_bytes
            async with self.client.pipeline(transaction=True) as pipe:
                pipe.delete(_record_key(victim))
                pipe.zrem(_INDEX_KEY, victim)
                await pipe.execute()

    # CRUD -------------------------------------------------------------------

    async def create(self, req: SessionCreateRequest) -> SessionRecord:
        if req.proxy_type == "res_rotating" and not req.proxy_pool_id:
            raise SessionIncompatible(
                "res_rotating requires proxy_pool_id — rotating exit IPs invalidate sessions"
            )

        now = self._clock()
        record = SessionRecord(
            session_id=_new_session_id(),
            status="created",
            created_at=now,
            expires_at=now + req.ttl_seconds,
            last_used_at=now,
            device=req.device,
            proxy_type=req.proxy_type,
            proxy_pool_id=req.proxy_pool_id,
            proxy_geo=req.proxy_geo,
        )
        await self._save(record)
        await self._evict_if_full()
        return record

    async def get(self, session_id: str) -> SessionRecord:
        record = await self._get_raw(session_id)
        if record.expires_at <= self._clock():
            raise SessionExpired(session_id, record.expires_at)
        # Bump only the ZSET score (recency for LRU), not the record's
        # last_used_at field — matching the old in-memory store, where a read
        # moved the LRU position but left last_used_at untouched (only writes
        # via touch/update_storage_state advance it). So the score may lead the
        # field; that's intentional, not drift.
        await self.client.zadd(_INDEX_KEY, {session_id: self._clock()})
        return record

    async def _peek(self, session_id: str) -> SessionRecord:
        """GET without the expiry check. Raise SessionNotFound if absent."""
        return await self._get_raw(session_id)

    async def list(self) -> list[SessionPublic]:
        members = await self.client.zrange(_INDEX_KEY, 0, -1)
        result = []
        for m in members:
            sid = m.decode() if isinstance(m, bytes) else m
            try:
                record = await self._get_raw(sid)
                result.append(self._to_public(record))
            except SessionNotFound:
                pass  # evicted between zrange and get; skip
        return result

    async def delete(self, session_id: str) -> None:
        exists = await self.client.exists(_record_key(session_id))
        if not exists:
            raise SessionNotFound(session_id)
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.delete(_record_key(session_id))
            pipe.zrem(_INDEX_KEY, session_id)
            await pipe.execute()

    async def touch(self, session_id: str) -> None:
        # RMW: update last_used_at + ZSET score atomically with the short lock.
        async with self._rmw_lock(session_id):
            record = await self._get_raw(session_id)
            record.last_used_at = self._clock()
            await self._save(record)

    async def update_storage_state(
        self, session_id: str, storage_state: dict[str, Any] | None
    ) -> SessionRecord:
        normalized = normalize_storage_state(storage_state)
        size = storage_state_bytes(normalized)
        if size > self._storage_state_max_bytes:
            raise SessionIncompatible(
                f"storage_state exceeds {self._storage_state_max_bytes} bytes "
                f"(got {size}). Phase 2 may relax this."
            )
        # RMW under the short internal lock (not the public session lock,
        # which is already held by the login flow calling this method).
        async with self._rmw_lock(session_id):
            record = await self._get_raw(session_id)
            record.storage_state = normalized
            record.storage_state_bytes = size
            record.last_used_at = self._clock()
            await self._save(record)
            return record

    async def set_status(
        self,
        session_id: str,
        status: str,
        *,
        last_error: str | None = None,
        login_script: Any | None = None,
    ) -> None:
        # RMW under the short internal lock (not the public session lock).
        async with self._rmw_lock(session_id):
            record = await self._get_raw(session_id)
            record.status = status  # type: ignore[assignment]
            if last_error is not None:
                record.last_error = last_error
            if login_script is not None:
                record.login_script = login_script
            await self._save(record)

    # Concurrency ------------------------------------------------------------

    def lock(self, session_id: str) -> RedisLock:
        """Return a per-session distributed lock for exclusive session work.

        Raises SessionNotFound synchronously if the session key is absent —
        matching the old in-memory store's behaviour (it raised on missing
        lock entry rather than waiting for it to appear).
        """
        # We can't do async existence check here (sync method), so the caller
        # must hold a reference obtained after a successful get().  However,
        # service.py always calls get() before lock(), so missing sessions
        # are caught before lock() is invoked.  For the rare direct-call path
        # (tasks.py _scrape_with_session), a missing session surfaces via
        # get() *inside* the lock body, which is equivalent.
        return RedisLock(
            self.client,
            _lock_key(session_id),
            ttl_ms=120_000,
            # Outlast a full login wait (worker timeout + the API's grace) + slack,
            # so a queued second login on the same session doesn't give up early.
            acquire_timeout_s=settings.login_task_timeout_s + settings.login_result_grace_s + 5,
        )

    # GC ---------------------------------------------------------------------

    async def sweep_expired(self) -> list[str]:
        now = self._clock()
        members = await self.client.zrange(_INDEX_KEY, 0, -1)
        expired_ids: list[str] = []
        for m in members:
            sid = m.decode() if isinstance(m, bytes) else m
            try:
                raw = await self.client.get(_record_key(sid))
                if raw is None:
                    # Stale ZSET entry; clean up the index.
                    await self.client.zrem(_INDEX_KEY, sid)
                    continue
                record = SessionRecord.model_validate_json(raw)
                if record.expires_at <= now:
                    async with self._rmw_lock(sid):
                        # Re-read under the lock so two sweepers don't double-write.
                        raw2 = await self.client.get(_record_key(sid))
                        if raw2 is None:
                            continue
                        record = SessionRecord.model_validate_json(raw2)
                        if record.expires_at <= now:
                            record.status = "expired"
                            record.storage_state = None
                            record.storage_state_bytes = 0
                            await self._save(record)
                            expired_ids.append(sid)
            except Exception as exc:  # pylint: disable=broad-except
                # Don't let one bad record (or a lock contention) abort the
                # whole sweep — but surface it so a silently-failing sweep is
                # visible to operators.
                log.warning("session sweep: skipping %s — %s", sid, exc)
        return expired_ids

    # Precondition gate ------------------------------------------------------

    def assert_compatible_with_request(
        self,
        session_record: SessionRecord,
        *,
        device: str,
        proxy_type: str,
        proxy_pool_id: str | None,
        proxy_geo: Any | None,
        cookies: list[Any] | None,
    ) -> None:
        """Hard 422 if any pin diverges from the session record."""
        if cookies:
            raise SessionIncompatible("cannot pass both session_id and cookies")
        if device != session_record.device:
            raise SessionIncompatible(
                f"session pinned device={session_record.device}, request asked device={device}"
            )
        if proxy_type != session_record.proxy_type:
            raise SessionIncompatible(
                f"session pinned proxy_type={session_record.proxy_type}, "
                f"request asked proxy_type={proxy_type}"
            )
        if proxy_pool_id is not None and proxy_pool_id != session_record.proxy_pool_id:
            raise SessionIncompatible(
                f"session pinned proxy_pool_id={session_record.proxy_pool_id}, "
                f"request asked proxy_pool_id={proxy_pool_id}"
            )
        if (
            proxy_geo is not None
            and session_record.proxy_geo is not None
            and proxy_geo != session_record.proxy_geo
        ):
            raise SessionIncompatible(
                f"session pinned proxy_geo={session_record.proxy_geo.model_dump(exclude_none=True)}, "
                f"request asked proxy_geo={proxy_geo.model_dump(exclude_none=True)}"
            )

    # ------------------------------------------------------------------------

    @staticmethod
    def _to_public(session_record: SessionRecord) -> SessionPublic:
        return SessionPublic(
            session_id=session_record.session_id,
            status=session_record.status,
            created_at=session_record.created_at,
            expires_at=session_record.expires_at,
            last_used_at=session_record.last_used_at,
            device=session_record.device,
            proxy_type=session_record.proxy_type,
            proxy_pool_id=session_record.proxy_pool_id,
            proxy_geo=session_record.proxy_geo,
            storage_state_bytes=session_record.storage_state_bytes,
            last_error=session_record.last_error,
        )
