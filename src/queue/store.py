"""Redis-backed job store: the durable replacement for InMemoryJobQueue.

Data model:
  job:{id}           hash   status,total,done,error,cancelled,finished_at,created_at
                            + chain_next:{i} fields for session chains
  job:{id}:pages     hash   index -> JSON(ScrapeRequest)
  job:{id}:results   hash   index -> gzip(JSON(ScrapeResponse))

All multi-step mutations are Lua scripts: idempotent slot writes with
exactly-once done-counting and atomic last-page finalization.
"""
from __future__ import annotations

import gzip
import json
import logging
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Mapping

import redis.asyncio as aioredis
from pydantic import ValidationError

from src.schemas import JobStatus, ScrapeRequest, ScrapeResponse
from src.settings import settings
from src.utils.ids import new_request_id

log = logging.getLogger(__name__)

SAFETY_TTL_S = 24 * 3600  # orphan guard; finalization re-arms to result_ttl_s

TERMINAL_STATUSES = ("done", "failed", "cancelled")


# Everything a stored blob can raise on the way back to an object. gzip raises
# BadGzipFile (an OSError, NOT a ValueError) on a bad magic number, zlib.error
# on a corrupt deflate body and EOFError on a truncated one — truncation being
# the likeliest physical corruption of a Redis value, and neither of those last
# two inherits from ValueError or OSError. json/utf-8 failures and pydantic's
# ValidationError are all ValueError subclasses.
_STORED_BLOB_ERRORS = (ValueError, OSError, zlib.error, EOFError)

# Pages are stored as plain JSON (see `create`), so only the json/utf-8/pydantic
# half of the set above can fire on them. Spelled separately rather than reusing
# the wider tuple: three of those members would be unreachable there, and a dead
# except member reads as a claim about the data that is not true.
_STORED_JSON_ERRORS = (ValueError,)


class JobPagesUnreadable(RuntimeError):
    """The job exists, but its page specs cannot be decoded.

    Distinct from "not found" on purpose: collapsing the two is what left such a
    job uncancellable, since cancel answered 404 for a job whose meta and slots
    were sitting right there.
    """

    def __init__(self, job_id: str, index: int) -> None:
        super().__init__(f"job {job_id}: page {index} is unreadable")
        self.job_id = job_id
        self.index = index


class JobNotInStore(KeyError):
    """write_slot hit a job whose keys are gone (orphan past safety TTL)."""


def pack_payload(obj: Mapping[str, Any]) -> bytes:
    return gzip.compress(json.dumps(obj, separators=(",", ":")).encode("utf-8"))


def pack_response(resp: ScrapeResponse) -> bytes:
    """Single serialization point for a result slot.

    `mode="json"` so nested models (PresetMeta, AppliedWarmup) encode the way
    the API encodes them, rather than leaving python objects for the packer.
    """
    return pack_payload(resp.model_dump(mode="json"))


def unpack_payload(blob: bytes) -> dict[str, Any]:
    return json.loads(gzip.decompress(blob).decode("utf-8"))


@dataclass
class JobMeta:
    job_id: str
    status: JobStatus
    total: int
    done: int
    error: str | None
    cancelled: bool
    finished_at: float | None
    created_at: float = 0.0


@dataclass
class JobSnapshot(JobMeta):
    pages: list[ScrapeRequest] = field(default_factory=list)
    results: list[ScrapeResponse | None] = field(default_factory=list)
    # Slots that exist in Redis but would not decode. Without this a degraded
    # slot is indistinguishable from one that was never written, so a caller
    # polling for `results != null` waits forever on a job that is done.
    unreadable_slots: list[int] = field(default_factory=list)


@dataclass
class SlotWriteResult:
    done: int
    total: int
    status: str
    was_new: bool


# --- Lua -------------------------------------------------------------------
# KEYS[1]=meta KEYS[2]=results KEYS[3]=pages
# ARGV[1]=index ARGV[2]=payload ARGV[3]=result_ttl_s ARGV[4]=now ARGV[5]=safety_ttl_s
_WRITE_SLOT = """
if redis.call('EXISTS', KEYS[1]) == 0 then return {'__missing__', 0, 0, 0} end
local existed = redis.call('HEXISTS', KEYS[2], ARGV[1])
redis.call('HSET', KEYS[2], ARGV[1], ARGV[2])
local done
if existed == 0 then
  done = redis.call('HINCRBY', KEYS[1], 'done', 1)
  -- Arm the orphan guard on the results key the first time it gains a field
  -- (it doesn't exist at create() time, so it would otherwise escape the 24h
  -- safety TTL that meta/pages carry). Finalization below re-arms it shorter.
  redis.call('EXPIRE', KEYS[2], tonumber(ARGV[5]))
else
  done = tonumber(redis.call('HGET', KEYS[1], 'done') or '0')
end
local total = tonumber(redis.call('HGET', KEYS[1], 'total') or '0')
local status = redis.call('HGET', KEYS[1], 'status')
local terminal = (status == 'done' or status == 'failed' or status == 'cancelled')
if (not terminal) and existed == 0 and done >= total then
  local final = 'done'
  if redis.call('HGET', KEYS[1], 'cancelled') == '1' then final = 'cancelled' end
  redis.call('HSET', KEYS[1], 'status', final, 'finished_at', ARGV[4])
  status = final
  local ttl = tonumber(ARGV[3])
  if ttl > 0 then
    redis.call('EXPIRE', KEYS[1], ttl)
    redis.call('EXPIRE', KEYS[2], ttl)
    redis.call('EXPIRE', KEYS[3], ttl)
  end
end
return {status, done, total, 1 - existed}
"""

# KEYS as above; ARGV[1]=result_ttl_s ARGV[2]=now ARGV[3..]=index,payload pairs
_CANCEL_FILL = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
local status = redis.call('HGET', KEYS[1], 'status')
if status == 'done' or status == 'failed' or status == 'cancelled' then return 0 end
redis.call('HSET', KEYS[1], 'cancelled', '1')
for i = 3, #ARGV, 2 do
  redis.call('HSETNX', KEYS[2], ARGV[i], ARGV[i + 1])
end
local done = redis.call('HLEN', KEYS[2])
redis.call('HSET', KEYS[1], 'done', done, 'status', 'cancelled', 'finished_at', ARGV[2])
local ttl = tonumber(ARGV[1])
if ttl > 0 then
  redis.call('EXPIRE', KEYS[1], ttl)
  redis.call('EXPIRE', KEYS[2], ttl)
  redis.call('EXPIRE', KEYS[3], ttl)
end
return 1
"""

_MARK_RUNNING = """
if redis.call('HGET', KEYS[1], 'status') == 'queued' then
  redis.call('HSET', KEYS[1], 'status', 'running')
  return 1
end
return 0
"""


class RedisJobStore:
    def __init__(self, client: aioredis.Redis, *, result_ttl_s: float | None = None) -> None:
        self.client = client
        self._result_ttl_s = settings.job_result_ttl_s if result_ttl_s is None else result_ttl_s
        self._write_slot = client.register_script(_WRITE_SLOT)
        self._cancel_fill = client.register_script(_CANCEL_FILL)
        self._mark_running = client.register_script(_MARK_RUNNING)

    @staticmethod
    def _k(job_id: str) -> tuple[str, str, str]:
        return f"job:{job_id}", f"job:{job_id}:results", f"job:{job_id}:pages"

    async def create(
        self,
        pages: list[ScrapeRequest],
        *,
        chain_next: Mapping[int, int] | None = None,
    ) -> str:
        job_id = new_request_id()
        meta_k, _res_k, pages_k = self._k(job_id)
        meta: dict[str, Any] = {
            "status": "queued",
            "total": len(pages),
            "done": 0,
            "cancelled": "0",
            "created_at": time.time(),
        }
        for idx, nxt in (chain_next or {}).items():
            meta[f"chain_next:{idx}"] = nxt
        if not pages:
            meta["status"] = "done"
            meta["finished_at"] = time.time()
            async with self.client.pipeline(transaction=True) as pipe:
                pipe.hset(meta_k, mapping=meta)  # type: ignore[arg-type]
                if self._result_ttl_s > 0:
                    pipe.expire(meta_k, int(self._result_ttl_s))
                else:
                    pipe.expire(meta_k, SAFETY_TTL_S)
                await pipe.execute()
            return job_id
        pages_map = {
            str(i): json.dumps(p.model_dump(mode="json"), separators=(",", ":"))
            for i, p in enumerate(pages)
        }
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(meta_k, mapping=meta)  # type: ignore[arg-type]
            pipe.hset(pages_k, mapping=pages_map)  # type: ignore[arg-type]
            pipe.expire(meta_k, SAFETY_TTL_S)
            pipe.expire(pages_k, SAFETY_TTL_S)
            await pipe.execute()
        return job_id

    async def write_slot(self, job_id: str, index: int, payload: bytes) -> SlotWriteResult:
        meta_k, res_k, pages_k = self._k(job_id)
        result = await self._write_slot(
            keys=[meta_k, res_k, pages_k],
            args=[str(index), payload, int(self._result_ttl_s), time.time(), SAFETY_TTL_S],
        )
        status = result[0]
        done = result[1]
        total = result[2]
        was_new = result[3]
        status = status.decode() if isinstance(status, bytes) else status
        if status == "__missing__":
            log.warning("write_slot on missing job %s (orphan past safety TTL), dropping", job_id)
            raise JobNotInStore(job_id)
        return SlotWriteResult(
            done=int(done),
            total=int(total),
            status=status,
            was_new=bool(int(was_new)),
        )

    async def mark_running(self, job_id: str) -> bool:
        meta_k, _, _ = self._k(job_id)
        return bool(int(await self._mark_running(keys=[meta_k], args=[])))

    async def request_cancel(self, job_id: str, *, stub_payloads: Mapping[int, bytes]) -> bool:
        meta_k, res_k, pages_k = self._k(job_id)
        args: list[Any] = [int(self._result_ttl_s), time.time()]
        for idx, payload in stub_payloads.items():
            args.extend([str(idx), payload])
        return bool(int(await self._cancel_fill(keys=[meta_k, res_k, pages_k], args=args)))

    async def get_meta(self, job_id: str) -> JobMeta | None:
        meta_k, _, _ = self._k(job_id)
        raw = await self.client.hgetall(meta_k)
        if not raw:
            return None
        d = {k.decode(): v.decode() for k, v in raw.items()}  # type: ignore[union-attr]
        return JobMeta(
            job_id=job_id,
            status=d.get("status", "queued"),  # type: ignore[arg-type]
            total=int(d.get("total", 0)),
            done=int(d.get("done", 0)),
            error=d.get("error"),
            cancelled=d.get("cancelled") == "1",
            finished_at=float(d["finished_at"]) if "finished_at" in d else None,
            created_at=float(d["created_at"]) if "created_at" in d else 0.0,
        )

    async def get_page(self, job_id: str, index: int) -> dict[str, Any] | None:
        _, _, pages_k = self._k(job_id)
        raw = await self.client.hget(pages_k, str(index))
        return None if raw is None else json.loads(raw)

    async def slot_filled(self, job_id: str, index: int) -> bool:
        _, res_k, _ = self._k(job_id)
        return bool(await self.client.hexists(res_k, str(index)))

    async def get_chain_next(self, job_id: str, index: int) -> int | None:
        meta_k, _, _ = self._k(job_id)
        raw = await self.client.hget(meta_k, f"chain_next:{index}")
        return None if raw is None else int(raw)

    async def get_full(self, job_id: str) -> JobSnapshot | None:
        meta = await self.get_meta(job_id)
        if meta is None:
            return None
        _, res_k, pages_k = self._k(job_id)
        raw_pages = await self.client.hgetall(pages_k)
        raw_results = await self.client.hgetall(res_k)
        pages: list[ScrapeRequest] = []
        for i in range(meta.total):
            blob = raw_pages.get(str(i).encode())
            if blob is None:
                return None
            try:
                pages.append(ScrapeRequest.model_validate(json.loads(blob)))
            except _STORED_JSON_ERRORS as exc:
                # A page cannot degrade to None the way a result can: `results`
                # is index-aligned with this list, and dropping one would shift
                # every later slot — the silent misalignment this codebase has
                # been bitten by before. So the job cannot be described — but it
                # still EXISTS, and saying "absent" here is what left it
                # uncancellable. Callers that need the spec (the results
                # response carries `pages`) surface this; cancel does not need
                # it and converges the job anyway.
                raise JobPagesUnreadable(job_id, i) from exc
        results: list[ScrapeResponse | None] = [None] * meta.total
        unreadable: list[int] = []      # placeable, but the blob would not decode
        unplaceable: list[str] = []     # the FIELD NAME itself is not a slot
        for k, blob in raw_results.items():
            # Everything about a slot comes out of the same possibly-corrupt
            # hash — the FIELD NAME as much as the value — so the parse and the
            # bounds check belong inside the guard with the decode. Left
            # outside, a non-numeric field raised ValueError (the original bug
            # verbatim), an out-of-range one raised IndexError, and a NEGATIVE
            # one silently wrote a plausible result into the last slot, which is
            # exactly the misalignment the page branch above refuses to allow.
            # Not named `field`: this module imports dataclasses.field, and it
            # is used for the very snapshot attribute this loop populates.
            raw_field = k.decode(errors="replace") if isinstance(k, bytes) else str(k)
            try:
                idx = int(raw_field)
            except ValueError:
                unplaceable.append(raw_field)
                continue
            if not 0 <= idx < meta.total:
                unplaceable.append(raw_field)
                continue
            try:
                results[idx] = ScrapeResponse.model_validate(
                    unpack_payload(blob)  # type: ignore[arg-type]
                )
            except _STORED_BLOB_ERRORS:
                # Per slot, because this loop backs the results endpoint, cancel
                # AND run_and_wait: one bad blob used to take all three down
                # until the TTL expired, losing the pages that were fine and
                # leaving the job uncancellable. Degrade the field, keep the
                # page. The likeliest cause is not corruption but schema skew —
                # a rolling deploy moves ScrapeResponse while jobs written by
                # the previous build are still in Redis, and this host deploys
                # by swapping the checkout under a running worker.
                unreadable.append(idx)
        if unreadable or unplaceable:
            # One line per read, not one per slot: reads are client-driven, and
            # a schema-skew deploy invalidates every in-flight job at once. The
            # payload never appears here — page content in an operator- or
            # caller-visible field is how a scan gets published with a false
            # verdict.
            log.warning(
                "job %s: %d of %d result slots unreadable %s; unplaceable fields: %s",
                job_id, len(unreadable), meta.total,
                sorted(unreadable), unplaceable or "none",
            )
        return JobSnapshot(
            **meta.__dict__, pages=pages, results=results,
            unreadable_slots=sorted(unreadable),
        )

    async def queue_depth(self, stream_key: str) -> int:
        """Live backlog: undelivered entries (group ``lag``) plus delivered-
        but-unacked entries (group ``pending``), summed across consumer groups.

        Deliberately NOT ``XLEN``. A Redis stream retains every entry until it
        is trimmed, and taskiq acks consumed messages but never deletes them,
        so ``XLEN`` counts long-finished jobs. Using it for submit backpressure
        made the depth climb monotonically until it permanently exceeded
        ``QUEUE_MAXSIZE`` and rejected every new job. ``lag + pending`` reflects
        only work still queued or in flight, so it drains back to 0.

        Before any worker has created the consumer group, all entries are still
        undelivered, so fall back to ``XLEN`` (and to 0 if the stream is absent).
        """
        try:
            groups = await self.client.xinfo_groups(stream_key)
        except aioredis.ResponseError:
            return 0  # stream key does not exist yet
        if not groups:
            return int(await self.client.xlen(stream_key))
        depth = 0
        for group in groups:
            # `lag` is None only when entries were trimmed out from under the
            # group, which cannot happen while depth is held below QUEUE_MAXSIZE;
            # treat that as drained rather than risk a phantom backlog.
            depth += int(group.get("lag") or 0) + int(group.get("pending") or 0)
        return depth


# --- module singleton wiring (patched in tests) ------------------------------
_store: RedisJobStore | None = None


def make_redis_client(url: str) -> aioredis.Redis:
    """Seam for tests: monkeypatch this to return fakeredis."""
    return aioredis.from_url(url)


def init_job_store(url: str | None = None) -> RedisJobStore:
    global _store
    _store = RedisJobStore(make_redis_client(url or settings.redis_url))
    return _store


def get_job_store() -> RedisJobStore:
    assert _store is not None, "init_job_store() must run in lifespan/worker startup"
    return _store
