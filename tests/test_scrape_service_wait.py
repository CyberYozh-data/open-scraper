from __future__ import annotations

from dataclasses import dataclass

import pytest

import src.scrape_service as svc
from src.scrape_service import scrape_service


@dataclass
class _Rec:
    status: str
    results: list | None = None


class _FakeQueue:
    """Returns a scripted sequence of records on successive get() calls."""

    def __init__(self, records):
        self._records = list(records)
        self._submitted = None

    async def submit(self, pages):
        self._submitted = pages
        return "job-1"

    async def get(self, job_id):
        # Hold on the last record once the script is exhausted.
        return self._records.pop(0) if len(self._records) > 1 else self._records[0]


@pytest.fixture
def fake_queue(mocker):
    def _install(records):
        q = _FakeQueue(records)
        mocker.patch.object(svc, "get_job_queue", lambda: q)
        return q
    return _install


class TestRunAndWait:
    @pytest.mark.asyncio
    async def test_returns_results_when_terminal(self, fake_queue):
        fake_queue([_Rec("running"), _Rec("done", results=["a", "b"])])
        out = await scrape_service.run_and_wait([], timeout_s=5)
        assert out == ["a", "b"]

    @pytest.mark.asyncio
    async def test_failed_with_no_results_returns_empty(self, fake_queue):
        fake_queue([_Rec("failed", results=None)])
        out = await scrape_service.run_and_wait([], timeout_s=5)
        assert out == []

    @pytest.mark.asyncio
    async def test_timeout_returns_partial_results(self, fake_queue):
        # Never reaches a terminal status; the deadline elapses while running,
        # but the record already carries partial results.
        # timeout < poll interval -> exactly one fetch, then the deadline passes.
        fake_queue([_Rec("running", results=["partial"])])
        out = await scrape_service.run_and_wait([], timeout_s=0.05)
        assert out == ["partial"]
