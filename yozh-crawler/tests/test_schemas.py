from __future__ import annotations

from src.schemas import CrawlRequest


def test_crawl_request_accepts_wait_until_load():
    req = CrawlRequest(seed_url="https://example.com", scrape_options={"wait_until": "load"})
    assert req.scrape_options.wait_until == "load"
