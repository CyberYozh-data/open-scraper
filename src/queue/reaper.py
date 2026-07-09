"""Pending-entry reclaim: redeliver stream messages whose consumer died
mid-task. Idempotent slot writes make double-delivery safe.

Invoked periodically by the `reclaim_stale` scheduled task (see queue/tasks.py),
driven by the taskiq scheduler process rather than a hand-rolled loop."""
from __future__ import annotations

import logging

from src.queue.broker import CONSUMER_GROUP, QUEUE_NAME, STREAM_MAXLEN
from src.queue.store import get_job_store
from src.settings import settings

log = logging.getLogger(__name__)


async def reclaim_once() -> int:
    client = get_job_store().client
    reclaimed = 0
    cursor = "0-0"
    while True:
        next_cursor, claimed, _ = await client.xautoclaim(
            name=QUEUE_NAME,
            groupname=CONSUMER_GROUP,
            consumername="reaper",
            min_idle_time=int(settings.reclaim_idle_s * 1000),
            start_id=cursor,
            count=50,
        )
        for msg_id, fields in claimed:
            # Re-add before ack: a crash here duplicates (safe — idempotent slot
            # writes) rather than losing the task.
            await client.xadd(QUEUE_NAME, fields, maxlen=STREAM_MAXLEN, approximate=True)
            await client.xack(QUEUE_NAME, CONSUMER_GROUP, msg_id)
            reclaimed += 1
        if isinstance(next_cursor, bytes):
            next_cursor = next_cursor.decode()
        # `count` bounds entries *scanned* per call, not claimed, so an empty
        # batch with an advancing cursor just means the scanned ones were too
        # fresh — keep going. Stop when the cursor wraps ("0-0", real Redis) or
        # stops advancing (no more to scan — also how fakeredis signals the end).
        if next_cursor in ("0-0",) or next_cursor == cursor:
            return reclaimed
        cursor = next_cursor
