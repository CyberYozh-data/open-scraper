"""Queue observability: stream depth, in-flight (pending) count, live consumers.

Additive, read-only. After the taskiq migration this is the real worker-fleet
visibility — /health.workers only reflects a configured env value, while this
reads the live Redis stream + consumer group."""
from __future__ import annotations

from fastapi import APIRouter
from redis.exceptions import ResponseError

from src.queue.broker import CONSUMER_GROUP, QUEUE_NAME, is_inmemory_broker
from src.queue.store import get_job_store

router = APIRouter()


def _decode(value) -> str:
    return value.decode() if isinstance(value, bytes) else value


@router.get("/stats", operation_id="get_queue_stats")
async def queue_stats() -> dict:
    """Live queue stats. `active_jobs` is intentionally omitted — counting them
    needs an unbounded SCAN over job:* keys; revisit only if a real need shows up."""
    if is_inmemory_broker():
        return {"backend": "inmemory", "depth": 0, "in_flight": 0, "consumers": []}

    client = get_job_store().client
    depth = int(await client.xlen(QUEUE_NAME))
    try:
        pending = await client.xpending(QUEUE_NAME, CONSUMER_GROUP)
        consumers = await client.xinfo_consumers(QUEUE_NAME, CONSUMER_GROUP)
    except (ResponseError, IndexError):
        # The consumer group doesn't exist until the first kiq: real Redis
        # raises ResponseError (NOGROUP), fakeredis raises IndexError. Other
        # errors (a Redis outage → ConnectionError) must surface, not
        # masquerade as an empty queue on the very fleet-visibility endpoint.
        return {"backend": "redis", "depth": depth, "in_flight": 0, "consumers": []}

    in_flight = pending["pending"] if isinstance(pending, dict) else pending[0]
    return {
        "backend": "redis",
        "depth": depth,
        "in_flight": int(in_flight or 0),
        "consumers": [
            {
                "name": _decode(c.get("name")),
                "pending": c.get("pending"),
                "idle_ms": c.get("idle"),
            }
            for c in consumers
        ],
    }
