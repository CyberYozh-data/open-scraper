from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from src.sessions.redis_store import RedisSessionStore


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


# --- module singleton (set by init_session_store in lifespan / worker startup) ---

_store = None  # RedisSessionStore, set by init_session_store


def init_session_store(client: "aioredis.Redis") -> "RedisSessionStore":
    """Build the Redis session store, configured from settings. Both the API
    lifespan and the worker startup call this, so the cap config is applied
    identically in every process (no API-accepts / worker-rejects skew)."""
    global _store
    from src.sessions.redis_store import RedisSessionStore
    from src.settings import settings
    _store = RedisSessionStore(
        client,
        max_sessions=settings.sessions_max,
        storage_state_max_bytes=settings.session_storage_state_max_bytes,
    )
    return _store


def get_session_store() -> "RedisSessionStore":
    assert _store is not None, "init_session_store() must run in lifespan/worker startup"
    return _store
