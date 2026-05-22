from __future__ import annotations

from src.schemas import CrawlRequest, CrawlScope, ScrapeOptions


def test_scrape_options_session_id_default_is_none():
    scrape_options = ScrapeOptions()
    assert scrape_options.session_id is None


def test_scrape_options_session_id_set():
    scrape_options = ScrapeOptions(session_id="sess_abc")
    assert scrape_options.session_id == "sess_abc"


def test_crawl_request_carries_session_id_through():
    crawl_request = CrawlRequest(
        seed_url="https://x.com",
        scope=CrawlScope(),
        scrape_options=ScrapeOptions(session_id="sess_abc"),
    )
    assert crawl_request.scrape_options.session_id == "sess_abc"
