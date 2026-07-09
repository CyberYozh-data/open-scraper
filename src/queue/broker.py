"""taskiq broker singleton. RedisStreamBroker in production (at-least-once,
acked); InMemoryBroker when TASKIQ_INMEMORY=1 (unit tests only — never a
production mode)."""
from __future__ import annotations

import os

from taskiq import InMemoryBroker
from taskiq_redis import RedisStreamBroker

from src.settings import settings

QUEUE_NAME = "scraper:tasks"
CONSUMER_GROUP = "scraper-workers"
STREAM_MAXLEN = 10_000  # safe: live depth is capped by submit backpressure (~QUEUE_MAXSIZE)


def _build_broker():
    if os.environ.get("TASKIQ_INMEMORY") == "1":
        return InMemoryBroker()
    return RedisStreamBroker(
        url=settings.redis_url,
        queue_name=QUEUE_NAME,
        consumer_group_name=CONSUMER_GROUP,
        maxlen=STREAM_MAXLEN,
    )


broker = _build_broker()


def is_inmemory_broker() -> bool:
    """True under unit tests (TASKIQ_INMEMORY=1). Gates stream-only behavior
    (backpressure depth check, reaper) that is meaningless for InMemoryBroker."""
    return type(broker).__name__ == "InMemoryBroker"
