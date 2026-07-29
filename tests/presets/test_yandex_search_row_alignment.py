"""Guards for the yandex_search row-alignment fix.

extract_fields matches each field's selector against the whole document and
returns flat parallel arrays that consumers zip by index. The shipped preset
scoped every field to `li.serp-item` and gave two of them a permissive second
alternative, so the four arrays described different row sets:

    titles=21, links=143, snippets=18, result_blocks=19   (live ru, 2026-07-26)

`li.serp-item a[href^='http']` matched *every* link inside a result — nav,
sitelinks, favicon hosts — and `li.serp-item h2` swept in widget headings
("Люди ищут", "Может заинтересовать"). The damage was not theoretical: on the
live kz capture the title "Ноутбуки в Алматы — Kaspi.kz" was paired with a link
to alser.kz, and "DNS" with kaspi.kz.

The fix scopes every field to the organic block. Two live shapes make
`li.serp-item` the wrong anchor and are pinned below:

  * a serp-item can carry NO organic result (an ad or a "people also search"
    widget) — live kz had 13 serp-items for 12 organic results;
  * a serp-item can carry TWO — live am had 13 serp-items for 14 organic
    results.

Counts across the three live captures after the fix: ru 18/18/18/18,
am 14/14/14/14, kz 12/12/12/12, no warnings.
"""
from __future__ import annotations

import json

from src.extract.extractor import extract_fields
from src.presets.models import Preset
from src.presets.store import DEFAULT_BUILTIN_DIR


def _load(name: str) -> Preset:
    path = DEFAULT_BUILTIN_DIR / f"{name}.json"
    return Preset(**json.loads(path.read_text(encoding="utf-8")))


def _organic(
    title: str,
    href: str = "https://kaspi.kz/shop/?q=1&sort=2",
    snippet: str = "snippet text",
) -> str:
    """Mirror of a live organic block: the title text sits in a span the link
    wraps, and Yandex wraps query terms in <b> inside it."""
    return f"""
    <div class="Organic organic">
      <h2 class="OrganicTitle">
        <a class="Link OrganicTitle-Link" href="{href}">
          <span class="OrganicTitle-LinkText">{title}</span>
        </a>
      </h2>
      <div class="OrganicText">{snippet}</div>
    </div>
    """


def _item(inner: str, extra_class: str = "") -> str:
    return f'<li class="serp-item {extra_class}">{inner}</li>'


def _page(items: list[str]) -> str:
    return (
        "<html><body><ul class='serp-list'>"
        + "".join(items)
        + '</ul><div class="VanillaReact Pager"><a href="?p=1">2</a></div>'
        "</body></html>"
    )


class TestYandexSearchSelectors:
    def setup_method(self):
        self.preset = _load("yandex_search")
        self.fields = self.preset.parsing_instructions.fields

    def test_every_field_anchors_on_the_same_organic_block(self):
        # The invariant src/README.md states: a field pointed at an element
        # that exists only when its value does shrinks the array and shifts
        # every later row. All four read the block's html instead.
        for name in ("titles", "links", "snippets", "result_blocks"):
            rule = self.fields[name]
            assert rule.selector == "li.serp-item .Organic", name
            assert rule.attr == "html", name
            assert rule.all is True, name
        assert self.fields["titles"].required is True

    def test_result_blocks_anchor_on_the_organic_block_not_the_list_item(self):
        # The list item is the wrong unit: it can hold zero organic results
        # (ad/widget) or two.
        assert self.fields["result_blocks"].selector == "li.serp-item .Organic"

    def test_wait_anchor_does_not_fail_closed(self):
        # `.Pager` was tried and reverted: a SERP that fits on one page renders
        # no pager, so the miss cost a rotation of three premium exits and the
        # 120s task ceiling on a perfectly good page. Waiting for the load
        # event handles the streaming instead, and never fails closed.
        assert self.preset.request_defaults["wait_for_selector"] == "li.serp-item"
        assert self.preset.request_defaults["wait_until"] == "load"

    def test_version_bumped(self):
        assert self.preset.version >= 3


class TestYandexSearchExtraction:
    def setup_method(self):
        self.preset = _load("yandex_search")

    def _extract(self, items):
        return extract_fields(_page(items), self.preset.parsing_instructions)

    def test_three_organic_results_stay_aligned(self):
        data, warnings = self._extract(
            [
                _item(_organic("Kaspi", "https://kaspi.kz/shop/")),
                _item(_organic("DNS", "https://dns-shop.kz/catalog/")),
                _item(_organic("Mechta", "https://mechta.kz/section/")),
            ]
        )
        assert data["titles"] == ["Kaspi", "DNS", "Mechta"]
        assert data["links"] == [
            "https://kaspi.kz/shop/",
            "https://dns-shop.kz/catalog/",
            "https://mechta.kz/section/",
        ]
        assert len(data["snippets"]) == 3
        assert len(data["result_blocks"]) == 3
        assert not warnings

    def test_widget_item_without_an_organic_block_is_not_a_row(self):
        """The live kz capture had 13 serp-items for 12 organic results.

        Under the old anchor the widget contributed a result_block and its
        heading contributed a title, shifting every later row.
        """
        widget = _item('<h2 class="WidgetTitle">Люди ищут</h2><a href="https://x.kz/">x</a>')
        data, warnings = self._extract(
            [
                _item(_organic("Kaspi", "https://kaspi.kz/shop/")),
                widget,
                _item(_organic("DNS", "https://dns-shop.kz/catalog/")),
            ]
        )
        assert data["titles"] == ["Kaspi", "DNS"]
        assert data["links"] == ["https://kaspi.kz/shop/", "https://dns-shop.kz/catalog/"]
        assert len(data["result_blocks"]) == 2
        assert not warnings

    def test_one_item_carrying_two_organic_results_yields_two_rows(self):
        """The live am capture had 13 serp-items for 14 organic results."""
        grouped = _item(
            _organic("Wildberries", "https://wildberries.am/catalog/")
            + _organic("Ozon", "https://am.ozon.com/category/")
        )
        data, warnings = self._extract([grouped, _item(_organic("Complife", "https://complife.am/"))])
        assert data["titles"] == ["Wildberries", "Ozon", "Complife"]
        assert len(data["links"]) == 3
        assert len(data["result_blocks"]) == 3
        assert not warnings

    def test_sitelinks_and_nav_anchors_do_not_multiply_the_links(self):
        """The failure that produced 143 links for 19 items."""
        noisy = _item(
            _organic("Kaspi", "https://kaspi.kz/shop/")
            + '<div class="Sitelinks">'
            '<a href="https://kaspi.kz/a">A</a><a href="https://kaspi.kz/b">B</a>'
            "</div>"
        )
        data, warnings = self._extract([noisy])
        assert data["links"] == ["https://kaspi.kz/shop/"]
        assert not warnings

    def test_title_is_paired_with_its_own_link(self):
        """The concrete corruption seen live: titles zipped to other rows' urls."""
        data, _ = self._extract(
            [
                _item(_organic("Ноутбуки в Алматы — Kaspi.kz", "https://kaspi.kz/shop/")),
                _item(_organic("DNS", "https://www.dns-shop.kz/catalog/")),
            ]
        )
        assert dict(zip(data["titles"], data["links"])) == {
            "Ноутбуки в Алматы — Kaspi.kz": "https://kaspi.kz/shop/",
            "DNS": "https://www.dns-shop.kz/catalog/",
        }

    def test_highlighted_query_terms_do_not_split_the_title(self):
        # Yandex wraps matched words in <b>; attr defaults to text.
        data, _ = self._extract(
            [_item(_organic("Купить <b>ноутбук</b> в Алматы", "https://kaspi.kz/shop/"))]
        )
        assert data["titles"] == ["Купить ноутбук в Алматы"]

    def test_result_without_a_snippet_yields_null_in_its_own_slot(self):
        """The invariant the whole rework rests on.

        The first attempt pointed `snippets` at `.OrganicText`, which exists
        only when the snippet does — so a result without one shrank the array
        and every later row wore the next row's snippet.
        """
        no_snippet = _item(
            """<div class="Organic organic"><h2 class="OrganicTitle">
                 <a class="Link OrganicTitle-Link" href="https://b.kz/"><span
                    class="OrganicTitle-LinkText">B</span></a></h2></div>"""
        )
        data, warnings = self._extract(
            [
                _item(_organic("A", "https://a.kz/", "snippet A")),
                no_snippet,
                _item(_organic("C", "https://c.kz/", "snippet C")),
            ]
        )
        assert data["titles"] == ["A", "B", "C"]
        assert data["links"] == ["https://a.kz/", "https://b.kz/", "https://c.kz/"]
        assert data["snippets"] == ["snippet A", None, "snippet C"]
        assert not warnings

    def test_result_whose_title_link_is_relative_keeps_its_slot(self):
        relative = _item(
            """<div class="Organic organic"><h2 class="OrganicTitle">
                 <a class="Link OrganicTitle-Link" href="/redir/xyz"><span
                    class="OrganicTitle-LinkText">B</span></a></h2>
                 <div class="OrganicText">snippet B</div></div>"""
        )
        data, warnings = self._extract(
            [_item(_organic("A", "https://a.kz/", "snippet A")), relative]
        )
        assert data["titles"] == ["A", "B"]
        assert data["links"] == ["https://a.kz/", None]
        assert not warnings

    def test_ad_block_is_emitted_as_a_row(self):
        """Documented behaviour, not an oversight: a paid block carries the
        same .Organic markup, so it is structurally indistinguishable here. The
        live ru capture's first row is an ad. Consumers filter on the link."""
        ad = _item(
            _organic("Купить ноутбук", "https://yabs.yandex.ru/count/WhOejI", "Реклама"),
            extra_class="serp-item_type_ad",
        )
        data, _ = self._extract([ad, _item(_organic("DNS", "https://dns-shop.ru/"))])
        assert data["titles"] == ["Купить ноутбук", "DNS"]
        assert data["links"][0].startswith("https://yabs.yandex.ru/count/")

    def test_minified_markup_extracts_the_same_as_pretty_printed(self):
        """`.` does not cross a newline and re.DOTALL is not set.

        Live Yandex html is minified, so a `.*?` capture worked on every real
        capture and returned null for every field on readable markup — the
        failure would have shipped invisible to the live check.
        """
        minified = (
            '<li class="serp-item"><div class="Organic organic">'
            '<h2 class="OrganicTitle"><a class="Link OrganicTitle-Link" '
            'href="https://a.kz/"><span class="OrganicTitle-LinkText">A</span>'
            '</a></h2><div class="OrganicText">snip</div></div></li>'
        )
        data, warnings = self._extract([minified])
        assert data["titles"] == ["A"]
        assert data["links"] == ["https://a.kz/"]
        assert data["snippets"] == ["snip"]
        assert not warnings

    def test_query_string_in_a_link_is_not_entity_encoded(self):
        """attr:"html" is lxml-re-serialised, so "&" comes back as "&amp;".

        Left unescaped, every ad link (yabs always carries a query string)
        reaches consumers with parse_qs keys like "amp;url" — non-null and
        plausible, so a count/null check cannot see it.
        """
        data, _ = self._extract(
            [_item(_organic("A", "https://yabs.yandex.ru/count/X?q=1&url=https%3A%2F%2Fa.ru&e=2"))]
        )
        assert data["links"] == ["https://yabs.yandex.ru/count/X?q=1&url=https%3A%2F%2Fa.ru&e=2"]
        assert "&amp;" not in data["links"][0]

    def test_snippet_with_a_leading_date_span_is_not_truncated_to_the_date(self):
        """A lazy terminator stops at the first NESTED closing tag.

        Yandex puts a date span before the snippet body, so the field came back
        as just "14 мар 2025" — non-null, plausible and wrong.
        """
        dated = _item(
            '''<div class="Organic"><h2 class="OrganicTitle">
                 <a class="Link OrganicTitle-Link" href="https://a.kz/"><span
                    class="OrganicTitle-LinkText">A</span></a></h2>
                 <div class="TextContainer OrganicText">
                   <span class="OrganicText-Date">14 мар 2025</span>
                   <span class="OrganicTextContentSpan">Body of the snippet</span>
                 </div></div>'''
        )
        data, _ = self._extract([dated])
        assert "Body of the snippet" in data["snippets"][0]
