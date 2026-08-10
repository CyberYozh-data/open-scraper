"""Typed result envelope for a single scrape run.

Plain frozen dataclasses on purpose. This value never leaves the worker
process — `run_scrape` returns it and `scrape_page` consumes it a few frames
up, so there is nothing to validate and nothing to serialize. What DOES cross
the Redis boundary is `ScrapeOk.result`, and that stays the pydantic
`ScrapeResponse` the API already validates on the way out.

The shape it replaces was a bare dict, and that cost us a real bug: a guard
read `envelope["meta"]` when the value lives at `envelope["result"]["meta"]`,
so it silently took its default and never ran. A TypedDict would have caught
that particular typo too. A union is chosen for what a TypedDict cannot give:
`isinstance` narrowing that makes "you only have `result` on the success
branch" a type error, immutability, and no `.get()` surface to slip through.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias

from src.schemas import ScrapeResponse


@dataclass(frozen=True, slots=True)
class ScrapeOk:
    """A completed scrape.

    `result` is exactly what lands in the job's result slot. `storage_state` is
    the opaque Playwright blob the session store owns — deliberately not
    modelled here. There is no `ok` flag: `isinstance` is the discriminator, so
    the branch cannot disagree with the payload it carries.
    """

    result: ScrapeResponse
    storage_state: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ScrapeErr:
    """A failed scrape.

    `traceback` is set only for an unexpected crash; a user configuration error
    or a timeout carries a message alone.
    """

    error: str
    traceback: str | None = None

    def __post_init__(self) -> None:
        """Substitute a message for an empty one — deliberately, not by raising.

        The read side's "worker_failed" default is gone by design, so an empty
        message here becomes a slot with `error=""` and `warnings=[""]`: a
        failure the caller can neither act on nor see. Raising instead would turn
        a cosmetic slip into the lost page this whole envelope exists to prevent,
        so the default is restored where it can still do its job.
        """
        if not self.error:
            object.__setattr__(self, "error", "worker_failed")


ScrapeEnvelope: TypeAlias = ScrapeOk | ScrapeErr
