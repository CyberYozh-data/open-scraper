"""Two presets that reported success while dropping fields on the floor.

Both failures were invisible to a count-based check: the request was HTTP 200,
`fetch_ok` was true, and `data` was non-empty — only the *values* were wrong.

walmart_product returned `price: null` and `rating: null` on every live run
(2026-07-26). Neither field is `required`, so `_missing_required` never fired
and self-heal never engaged. The price selector was never the problem: it
matched and handed over `"Now $199.00"`, and `parse_price` returned null on the
"Now " prefix. The rating selector was genuinely dead — the live markup carries
`(4.4)|44.4K ratings` under `[data-testid='reviews-and-ratings']`, and the
shipped `.rating-number` occurs zero times.

google_shopping shipped a `urls` field that cannot be filled: Google renders
the cards with no `<a href>` at all (0 anchors across 55 live cards) and
navigates via JS. An always-empty `all: true` field is not harmless — it makes
`row_alignment_mismatch` fire on every otherwise-perfect run, which trains
readers to ignore the one warning that catches real corruption.
"""
from __future__ import annotations

import json

from src.extract.extractor import extract_fields
from src.presets.models import Preset
from src.presets.store import DEFAULT_BUILTIN_DIR


def _load(name: str) -> Preset:
    path = DEFAULT_BUILTIN_DIR / f"{name}.json"
    return Preset(**json.loads(path.read_text(encoding="utf-8")))


def _walmart_page(*, price: str = "Now $199.00", reviews: str = "(4.4)|44.4K ratings") -> str:
    """Mirror of the live product page: the price string carries a leading
    savings label, and the rating shares its node with the review count."""
    return f"""
    <html><body>
      <h1 itemprop="name" data-fs-element="name">Apple AirPods Pro (2nd Generation)</h1>
      <span hidden itemprop="priceCurrency">USD</span>
      <span class="inline-flex flex-column">
        <span itemprop="price" data-seo-id="hero-price" aria-hidden="false">{price}</span>
      </span>
      <div data-testid="reviews-and-ratings">{reviews}</div>
      <div data-testid="fulfillment-add-to-cart"><button>Add to cart</button></div>
    </body></html>
    """


class TestWalmartProductValues:
    def setup_method(self):
        self.preset = _load("walmart_product_camoufox")

    def _extract(self, **kwargs):
        data, warnings = extract_fields(
            _walmart_page(**kwargs), self.preset.parsing_instructions
        )
        assert not warnings
        return data

    def test_price_survives_the_savings_prefix(self):
        # "Now $199.00" -> parse_price alone returned null.
        assert self._extract()["price"] == 199.0

    def test_plain_price_still_parses(self):
        assert self._extract(price="$249.00")["price"] == 249.0

    def test_thousands_separator_price(self):
        assert self._extract(price="Now $1,299.99")["price"] == 1299.99

    def test_price_without_cents_still_parses(self):
        # The pattern must not require a decimal point: parse_price handled
        # "$199" before this change and a stricter regex would regress it to
        # the very silent null this commit exists to remove.
        assert self._extract(price="$199")["price"] == 199.0
        assert self._extract(price="Now $1,199")["price"] == 1199.0

    def test_savings_label_does_not_win_over_the_current_price(self):
        # An unanchored first-number-wins pattern reports the savings as the
        # price — a silently WRONG number, strictly worse than a null.
        assert self._extract(price="Save $50.00 Now $199.00")["price"] == 199.0

    def test_strikethrough_list_price_does_not_win(self):
        assert self._extract(price="was $249.00 Now $199.00")["price"] == 199.0

    def test_rating_is_read_from_the_live_reviews_node(self):
        assert self._extract()["rating"] == 4.4

    def test_comma_decimal_rating_is_not_multiplied_by_ten(self):
        # parse_float("4,4") == 44.0. The separator must be normalised first —
        # the pairing amazon_search uses after its de all-4.0 bug.
        assert self._extract(reviews="(4,4)|44.4K ratings")["rating"] == 4.4

    def test_review_count_does_not_win_over_the_rating(self):
        # "(4.4)|44.4K ratings" contains 4.4 twice — once as the rating, once
        # inside the count. An unanchored bounded regex picks whichever comes
        # first, which is why the pattern anchors on the parentheses.
        assert self._extract(reviews="(3.8)|44.4K ratings")["rating"] == 3.8

    def test_missing_reviews_node_yields_null_not_a_stray_number(self):
        data, _ = extract_fields(
            """<html><body>
                 <h1 itemprop="name">X</h1>
                 <span itemprop="price">Now $9.99</span>
               </body></html>""",
            self.preset.parsing_instructions,
        )
        assert data["rating"] is None
        assert data["price"] == 9.99

    def test_title_and_availability_did_not_regress(self):
        data = self._extract()
        assert data["title"] == "Apple AirPods Pro (2nd Generation)"
        assert data["availability"] == "Add to cart"

    def test_version_bumped(self):
        assert self.preset.version >= 2


class TestGoogleShoppingHasNoUrlsField:
    def setup_method(self):
        self.preset = _load("google_shopping_chromium")

    def test_prompt_schema_no_longer_advertises_urls(self):
        # GET /api/v1/presets/<name> returns the whole document, so a stale
        # prompt_schema key makes the public preset contradict itself.
        assert "urls" not in (self.preset.prompt_schema or {})

    def test_urls_field_is_gone(self):
        assert "urls" not in self.preset.parsing_instructions.fields

    def test_output_schema_no_longer_promises_urls(self):
        schema = self.preset.output_schema or {}
        assert "urls" not in (schema.get("properties") or {})

    def test_remaining_fields_stay_aligned_without_a_phantom_column(self):
        """The empty urls column was the whole reason a good run warned.

        Live 2026-07-26: titles=55, prices=55, merchants=55, urls=0 ->
        row_alignment_mismatch on a run whose data was entirely correct.
        """
        card = (
            '<div class="PhALMc bVO81 lLi9V">'
            '<div class="gkQHve">{title}</div>'
            '<span class="lmQWe">{price}</span>'
            '<span class="Z9qvte">{merchant}</span>'
            "</div>"
        )
        html = "<html><body>" + "".join(
            card.format(title=f"Shoe {i}", price=f"${i}9.00", merchant=f"Store {i}")
            for i in range(3)
        ) + "</body></html>"
        data, warnings = extract_fields(html, self.preset.parsing_instructions)
        lengths = {k: len(v) for k, v in data.items() if isinstance(v, list)}
        assert len(set(lengths.values())) == 1, lengths
        assert not warnings

    def test_version_bumped(self):
        assert self.preset.version >= 3
