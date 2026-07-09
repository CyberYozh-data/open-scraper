from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.app import create_app
from src.queue.broker import CONSUMER_GROUP, QUEUE_NAME
from src.queue.store import get_job_store


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def _run(coro):
    return asyncio.run(coro)


def test_queue_stats_inmemory_backend(client):
    # Under tests the broker is InMemoryBroker (TASKIQ_INMEMORY=1), so /stats
    # reports the inmemory backend without touching a stream.
    resp = client.get("/api/v1/queue/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["backend"] == "inmemory"
    assert data["depth"] == 0
    assert data["in_flight"] == 0
    assert data["consumers"] == []


def test_queue_stats_is_additive_route(client):
    # The endpoint exists and is GET-only (additive surface; never shadows
    # /scrape/{job_id} since it lives under the /queue prefix).
    assert client.post("/api/v1/queue/stats").status_code == 405


def test_queue_stats_redis_backend_shape(client, mocker):
    # Force the redis branch and stand up a real (fakeredis) stream + group.
    mocker.patch("src.api.queue_stats.is_inmemory_broker", return_value=False)
    redis = get_job_store().client

    async def _setup():
        await redis.xgroup_create(QUEUE_NAME, CONSUMER_GROUP, id="0", mkstream=True)
        await redis.xadd(QUEUE_NAME, {"x": "1"})
        await redis.xreadgroup(CONSUMER_GROUP, "c1", {QUEUE_NAME: ">"}, count=1)
    _run(_setup())

    data = client.get("/api/v1/queue/stats").json()
    assert data["backend"] == "redis"
    assert data["depth"] == 1
    assert data["in_flight"] == 1  # one entry read but unacked
    assert any(c["name"] == "c1" for c in data["consumers"])


def test_queue_stats_redis_no_group_yet(client, mocker):
    # Redis backend but no consumer group created yet (no kiq): graceful empty.
    mocker.patch("src.api.queue_stats.is_inmemory_broker", return_value=False)
    data = client.get("/api/v1/queue/stats").json()
    assert data["backend"] == "redis"
    assert data["in_flight"] == 0 and data["consumers"] == []
