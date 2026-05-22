"""Guards the ExtractRule/FieldRule consolidation.

Before PR-2 there were two independent definitions: `src.schemas.FieldRule`
(API-facing, rich descriptions) and `src.extract.models.FieldRule`
(extractor, gained post_process/type in PR-1). They drifted: a preset's
parsing_instructions carry post_process, but ScrapeRequest.extract used the
schemas copy which would silently drop those fields on validation.

These tests assert the two names now resolve to the SAME class so a preset
can flow into ScrapeRequest.extract without losing post_process/type.
"""
from __future__ import annotations

from src.extract.models import ExtractRule as ExtractRuleCore
from src.extract.models import FieldRule as FieldRuleCore
from src.schemas import ExtractRule as ExtractRuleApi
from src.schemas import FieldRule as FieldRuleApi
from src.schemas import ScrapeRequest


def test_field_rule_is_single_class():
    assert FieldRuleApi is FieldRuleCore


def test_extract_rule_is_single_class():
    assert ExtractRuleApi is ExtractRuleCore


def test_scrape_request_extract_preserves_post_process():
    req = ScrapeRequest(
        url="https://example.com",
        extract={
            "type": "css",
            "fields": {
                "price": {
                    "selector": ".a-price",
                    "post_process": [{"op": "parse_price", "args": ["us"]}],
                },
                "title": {
                    "selector": "//h1/text()",
                    "type": "xpath",
                },
            },
        },
    )
    assert req.extract is not None
    price = req.extract.fields["price"]
    assert price.post_process[0].op == "parse_price"
    assert req.extract.fields["title"].type == "xpath"

    # Round-trip through JSON (this is what the worker actually receives)
    dumped = req.model_dump(mode="json")
    assert dumped["extract"]["fields"]["price"]["post_process"] == [
        {"op": "parse_price", "args": ["us"]}
    ]
