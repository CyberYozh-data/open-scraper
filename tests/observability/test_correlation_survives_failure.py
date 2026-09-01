"""The id the caller holds must be the id in the log — on the paths that matter.

`make_error_payload` minted a FRESH request_id, so on every failure branch —
timeout, session error, any exception — the id returned to the caller was a
different string from the one in every log line about that run, with nothing
joining them. The code already said so, in a comment at
src/queue/scrape_runner.py:836-839. Failure is exactly when someone goes
looking, so that is exactly where correlation was broken.
"""
from __future__ import annotations

import pytest

from src.observability.log_context import bind_log_context
from src.queue.tasks import make_error_payload


def test_the_error_payload_adopts_the_run_that_produced_it():
    with bind_log_context(request_id="req_run"):
        payload = make_error_payload({"url": "https://e.com"}, "boom")
    assert payload.request_id == "req_run"


def test_an_explicit_id_wins_over_the_ambient_one():
    with bind_log_context(request_id="req_ambient"):
        payload = make_error_payload({"url": "https://e.com"}, "boom", request_id="req_explicit")
    assert payload.request_id == "req_explicit"


def test_outside_a_run_it_still_mints_one():
    """`scrape_service` builds error slots for pages that never reached a
    worker — there is no run to adopt, and a response without an id would fail
    validation on read-back."""
    payload = make_error_payload({"url": "https://e.com"}, "never queued")
    assert payload.request_id
    assert payload.request_id.startswith("req_")


def test_two_error_payloads_in_one_run_share_the_id():
    with bind_log_context(request_id="req_run"):
        a = make_error_payload({"url": "https://a.com"}, "boom")
        b = make_error_payload({"url": "https://b.com"}, "boom")
    assert a.request_id == b.request_id == "req_run"
