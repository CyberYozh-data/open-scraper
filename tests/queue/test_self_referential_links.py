"""A SERP whose every result links back to the search engine is not a SERP.

Bing wraps organic hrefs in bing.com/ck/a redirects, so `links` read straight
from href pointed at bing.com on every row — 100% populated, 100% useless, and
invisible to any fill-rate or row-count check. The preset now decodes the
wrapper, but the class of failure is not Bing-specific: any engine that starts
wrapping, or a selector that drifts onto internal navigation, produces the same
shape. This warns on the shape rather than on the engine.
"""
from __future__ import annotations

import pytest

from src.queue.scrape_runner import self_referential_link_warning as warn


class TestSelfReferentialLinks:
    def test_every_link_on_the_pages_own_host_is_reported(self):
        data = {"links": [
            "https://www.bing.com/ck/a?u=a1AAA",
            "https://www.bing.com/ck/a?u=a1BBB",
            "https://www.bing.com/ck/a?u=a1CCC",
        ]}

        out = warn(data, "https://www.bing.com/search?q=x")

        assert out is not None
        assert "bing.com" in out

    def test_real_destinations_are_not_reported(self):
        data = {"links": [
            "https://www.pcmag.com/picks/the-best-laptops",
            "https://www.cnet.com/tech/computing/best-laptop/",
            "https://www.tomshardware.com/laptops/best-laptops",
        ]}

        out = warn(data, "https://www.bing.com/search?q=x")

        assert out is None

    def test_a_single_self_link_among_real_ones_is_not_reported(self):
        """Engines legitimately link to themselves — 'more results', a cached
        copy. The signal is that there is nothing ELSE."""
        data = {"links": [
            "https://www.pcmag.com/picks/the-best-laptops",
            "https://www.cnet.com/tech/computing/best-laptop/",
            "https://www.bing.com/search?q=x&first=10",
        ]}

        out = warn(data, "https://www.bing.com/search?q=x")

        assert out is None

    def test_too_few_links_to_judge(self):
        """One or two rows is not evidence of anything; a product page that
        happens to link to itself must not trip this."""
        data = {"links": ["https://www.bing.com/ck/a?u=a1AAA"]}

        assert warn(data, "https://www.bing.com/search?q=x") is None

    def test_no_urls_at_all_is_silent(self):
        data = {"title": "A product", "price": "19.99"}

        assert warn(data, "https://shop.example/p/1") is None

    def test_a_single_object_row_is_handled(self):
        """Not every preset returns parallel arrays."""
        data = {"url": "https://shop.example/p/1", "title": "x"}

        assert warn(data, "https://shop.example/p/1") is None

    def test_no_final_url_means_nothing_to_compare_against(self):
        data = {"links": ["https://a.com/1", "https://a.com/2", "https://a.com/3"]}

        assert warn(data, None) is None

    def test_a_site_linking_within_itself_is_not_a_serp_failure(self):
        """Crawling a shop's category page yields internal links by design.

        The warning must key on the links being on the PAGE'S OWN host, which
        for a normal site is expected — so this only fires where it is odd, and
        the message says what it saw rather than asserting a diagnosis.
        """
        data = {"links": [
            "https://shop.example/p/1", "https://shop.example/p/2",
            "https://shop.example/p/3",
        ]}

        out = warn(data, "https://shop.example/category")

        # It DOES fire — the shape is identical — so the message must be
        # descriptive, not accusatory, and it must never fail the scrape.
        assert out is not None
        assert "shop.example" in out


@pytest.mark.asyncio
async def test_the_queue_actually_emits_the_warning():
    """The wiring, not just the helper.

    Removing the call site left the whole suite green, which is how a feature
    ships switched off. This drives a real `run_scrape` and reads `warnings`
    off the response the caller would receive.
    """
    from src.browser.runner import FetchResult
    from src.queue.envelope import ScrapeOk
    from src.queue.scrape_runner import run_scrape

    html = (
        '<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u=a1AAA">a</a></h2></li>'
        '<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u=a1BBB">b</a></h2></li>'
        '<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u=a1CCC">c</a></h2></li>'
    )

    class _Runner:
        async def resolve_proxy(self, proxy):
            return proxy, None

        async def fetch(self, **_kw):
            return FetchResult(
                html=html, final_url="https://www.bing.com/search?q=x",
                status_code=200, screenshot_b64=None, ok=True, error=None,
            )

    out = await run_scrape(
        _Runner(), "req_selflink",
        {
            "url": "https://www.bing.com/search?q=x", "device": "desktop",
            "proxy_type": "none",
            "extract": {"type": "css", "fields": {
                "links": {"selector": "li.b_algo h2 a", "attr": "href", "all": True}}},
        },
        None,
    )

    assert isinstance(out, ScrapeOk)
    assert any("all_extracted_links_self_referential" in w for w in out.result.warnings), (
        out.result.warnings
    )
