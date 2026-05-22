from __future__ import annotations

import asyncio
import secrets
import time
from collections import OrderedDict
from typing import Any, Callable

from src.sessions.models import (
    SessionCreateRequest,
    SessionPublic,
    SessionRecord,
)
from src.sessions.serde import normalize_storage_state, storage_state_bytes


class SessionNotFound(KeyError):
    """Lookup failed — no such session."""


class SessionExpired(Exception):
    """Session passed its TTL. Re-create."""

    def __init__(self, session_id: str, expired_at: float) -> None:
        super().__init__(f"session {session_id} expired at {expired_at}")
        self.session_id = session_id
        self.expired_at = expired_at


class SessionIncompatible(Exception):
    """Session pin doesn't match the incoming request, or invalid creation params."""


def _new_session_id() -> str:
    return f"sess_{secrets.token_hex(12)}"


class InMemorySessionStore:
    """Holds `SessionRecord`s plus a per-session `asyncio.Lock`.

    LRU-by-`last_used_at` eviction on overflow. TTL sweep is a separate coroutine
    launched from the app's lifespan (`src/app.py`).

    The lock lives here (in the API process) — not on the record, because workers
    are subprocesses and locks don't cross that boundary.
    """

    def __init__(
        self,
        *,
        max_sessions: int = 256,
        storage_state_max_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        self._records: "OrderedDict[str, SessionRecord]" = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()
        self._max_sessions = max_sessions
        self._storage_state_max_bytes = storage_state_max_bytes
        self._clock: Callable[[], float] = time.time

    # Testing hook -----------------------------------------------------------
    def set_clock(self, clock: Callable[[], float]) -> None:
        self._clock = clock

    def configure(self, *, max_sessions: int, storage_state_max_bytes: int) -> None:
        """Re-configure caps at app startup (called from lifespan)."""
        self._max_sessions = max_sessions
        self._storage_state_max_bytes = storage_state_max_bytes

    # CRUD -------------------------------------------------------------------
    async def create(self, req: SessionCreateRequest) -> SessionRecord:
        if req.proxy_type == "res_rotating" and not req.proxy_pool_id:
            raise SessionIncompatible(
                "res_rotating requires proxy_pool_id — rotating exit IPs invalidate sessions"
            )

        now = self._clock()
        session_record = SessionRecord(
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

        async with self._lock:
            self._records[session_record.session_id] = session_record
            self._records.move_to_end(session_record.session_id)
            self._locks[session_record.session_id] = asyncio.Lock()
            self._evict_if_full_locked()
        return session_record

    async def get(self, session_id: str) -> SessionRecord:
        async with self._lock:
            session_record = self._records.get(session_id)
            if session_record is None:
                raise SessionNotFound(session_id)
            if session_record.expires_at <= self._clock():
                raise SessionExpired(session_id, session_record.expires_at)
            self._records.move_to_end(session_id)
            return session_record

    async def _peek(self, session_id: str) -> SessionRecord:
        async with self._lock:
            session_record = self._records.get(session_id)
            if session_record is None:
                raise SessionNotFound(session_id)
            return session_record

    async def list(self) -> list[SessionPublic]:
        async with self._lock:
            return [self._to_public(session_record) for session_record in self._records.values()]

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            if session_id not in self._records:
                raise SessionNotFound(session_id)
            self._records.pop(session_id)
            self._locks.pop(session_id, None)

    async def touch(self, session_id: str) -> None:
        async with self._lock:
            session_record = self._records.get(session_id)
            if session_record is None:
                raise SessionNotFound(session_id)
            session_record.last_used_at = self._clock()
            self._records.move_to_end(session_id)

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
        async with self._lock:
            session_record = self._records.get(session_id)
            if session_record is None:
                raise SessionNotFound(session_id)
            session_record.storage_state = normalized
            session_record.storage_state_bytes = size
            session_record.last_used_at = self._clock()
            self._records.move_to_end(session_id)
            return session_record

    async def set_status(
        self,
        session_id: str,
        status: str,
        *,
        last_error: str | None = None,
        login_script: Any | None = None,
    ) -> None:
        async with self._lock:
            session_record = self._records.get(session_id)
            if session_record is None:
                raise SessionNotFound(session_id)
            session_record.status = status  # type: ignore[assignment]
            if last_error is not None:
                session_record.last_error = last_error
            if login_script is not None:
                session_record.login_script = login_script

    # Concurrency -------------------------------------------------------------
    def lock(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            raise SessionNotFound(session_id)
        return lock

    # GC ---------------------------------------------------------------------
    async def sweep_expired(self) -> list[str]:
        now = self._clock()
        async with self._lock:
            expired = [session_id for session_id, session_record in self._records.items() if session_record.expires_at <= now]
            for session_id in expired:
                session_record = self._records[session_id]
                session_record.status = "expired"
                session_record.storage_state = None
                session_record.storage_state_bytes = 0
            return expired

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
        if proxy_geo is not None and session_record.proxy_geo is not None and proxy_geo != session_record.proxy_geo:
            raise SessionIncompatible(
                f"session pinned proxy_geo={session_record.proxy_geo.model_dump(exclude_none=True)}, "
                f"request asked proxy_geo={proxy_geo.model_dump(exclude_none=True)}"
            )

    # ------------------------------------------------------------------------
    def _evict_if_full_locked(self) -> None:
        while len(self._records) > self._max_sessions:
            victim, _ = self._records.popitem(last=False)  # LRU
            self._locks.pop(victim, None)

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


session_store = InMemorySessionStore()


def get_session_store() -> InMemorySessionStore:
    return session_store
