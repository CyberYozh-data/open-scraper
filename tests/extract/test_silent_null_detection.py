"""A post_process pipeline that nulls a value it was handed must say so.

`extract_fields` warns when a selector is invalid, when it matches nothing, and
when two `all=true` columns come back different lengths. It says nothing at all
about the remaining failure: the selector matched, the node had text, and the
pipeline turned it into None. That silence is why walmart_product shipped
`price: null` on every live run for weeks — every check the request passes
(HTTP 200, non-empty data, required fields present as a list) stays green.
"""
from __future__ import annotations

import time

import pytest

from src.extract.extractor import _has_content, extract_fields
from src.extract.models import ExtractRule, FieldRule, PostProcess


def _rule(**field_kwargs) -> ExtractRule:
    return ExtractRule(type="css", fields={"price": FieldRule(**field_kwargs)})


def test_a_pipeline_that_nulls_a_matched_value_warns():
    """The regex is valid and the node has text; it simply does not match."""
    html = "<div class='p'>Now $199.00</div>"
    rule = _rule(
        selector=".p",
        post_process=[PostProcess(op="regex", args=[r"^\$([\d.]+)$"])],
    )

    data, warnings = extract_fields(html, rule)

    assert data["price"] is None
    assert any("price" in w and "post_process" in w for w in warnings), warnings


def test_one_null_among_many_is_left_alone():
    """A single missing value is ordinary markup variance — a SERP result with
    no snippet, a row that is out of stock. Warning on it would fire on every
    optional field and teach the reader to skip the warning that matters."""
    html = "<i class='p'>$1.00</i><i class='p'>from $2.00</i><i class='p'>$3.00</i>"
    rule = _rule(
        selector=".p", all=True,
        post_process=[PostProcess(op="regex", args=[r"^\$([\d.]+)$"])],
    )

    data, warnings = extract_fields(html, rule)

    assert data["price"] == ["1.00", None, "3.00"]
    assert warnings == []


def test_a_column_nulled_in_full_is_reported_with_its_size():
    """Three of three is a dead pipeline, and `[None, None, None]` looks exactly
    like `[None]` to every check the caller can make."""
    html = "<i class='p'>from $1.00</i><i class='p'>from $2.00</i><i class='p'>from $3.00</i>"
    rule = _rule(
        selector=".p", all=True,
        post_process=[PostProcess(op="regex", args=[r"^\$([\d.]+)$"])],
    )

    data, warnings = extract_fields(html, rule)

    assert data["price"] == [None, None, None]
    assert any("3 of 3 matched" in w for w in warnings), warnings


def test_a_single_row_column_is_not_evidence_of_anything():
    """One result whose snippet is absent looks identical to a dead snippet
    pipeline. Single-result SERPs are ordinary, so guessing here would make the
    warning untrustworthy everywhere else."""
    html = "<i class='p'>from $1.00</i>"
    rule = _rule(
        selector=".p", all=True,
        post_process=[PostProcess(op="regex", args=[r"^\$([\d.]+)$"])],
    )

    data, warnings = extract_fields(html, rule)

    assert data["price"] == [None]
    assert warnings == []


def test_an_empty_container_is_empty_even_though_its_markup_is_not():
    """`attr="html"` is the pattern this repo mandates for row-aligned fields.

    `<div class="p"></div>` is a non-empty STRING and an empty VALUE, so judging
    emptiness on the markup makes the guard vacuous for every container-anchored
    field — which is most of the shipped presets.
    """
    html = "<i class='p'></i><i class='p'></i>"
    rule = _rule(
        selector=".p", all=True, attr="html",
        post_process=[PostProcess(op="regex", args=[r"data-price=\"([\d.]+)\""])],
    )

    data, warnings = extract_fields(html, rule)

    assert data["price"] == [None, None]
    assert warnings == []


def test_one_blank_node_does_not_switch_the_detector_off():
    """The denominator is the values that HAD something, not every node.

    A column where one node is blank and every other value was consumed is the
    walmart shape exactly; comparing against the full node count silently
    disabled the detector whenever a page carried one empty slot.
    """
    html = "<i class='p'></i><i class='p'>from $2.00</i><i class='p'>from $3.00</i>"
    rule = _rule(
        selector=".p", all=True,
        post_process=[PostProcess(op="regex", args=[r"^\$([\d.]+)$"])],
    )

    data, warnings = extract_fields(html, rule)

    assert data["price"] == [None, None, None]
    assert any("2 of 3 matched" in w for w in warnings), warnings


def test_a_whole_document_selector_is_still_judged():
    """Exempting `selector: "html"` looked right and was wrong.

    `youtube_video` anchors views/likes on the document and digs them out of
    `ytInitialData`; the preset documents them as parsed, so a renamed payload
    key is the walmart shape exactly. Exempting the selector silenced it. The
    discriminator would have to be "is this field expected to be empty", which
    is preset knowledge, not something the engine can read off a selector.
    """
    html = "<html><body>ytInitialData = {\"viewCountRENAMED\":\"123\"}</body></html>"
    rule = _rule(
        selector="html", attr="html",
        post_process=[PostProcess(op="regex", args=[r'"viewCount":"(\d+)"'])],
    )

    _data, warnings = extract_fields(html, rule)

    assert any("post_process" in w for w in warnings), warnings


def test_a_container_holding_only_a_script_is_not_content():
    """A container whose only payload is markup is empty, whatever the markup is.

    These four are pinned because each one defeated a regex attempt at the same
    question: a script body read as text, a `>` inside an attribute value ending
    the tag match early, a comment, and an entity that `str.strip()` cannot see.
    The implementation parses now, so they should stay easy — they are here to
    stop the next person reaching for a regex again.
    """
    assert _has_content('<div title="a>b"></div>') is False
    assert _has_content("<div><script>var x=1;</script></div>") is False
    assert _has_content("<div><!-- a > b --></div>") is False
    assert _has_content("<div>&nbsp;</div>") is False
    assert _has_content("<div>real</div>") is True
    # A zero is a value. `str(raw or "")` used to read it as absent, which would
    # have hidden a pipeline that nulled a legitimate 0.
    assert _has_content(0) is True


def test_an_empty_match_is_not_reported_as_a_pipeline_failure():
    """The node was empty to begin with, so the pipeline destroyed nothing.

    Reporting it would fire on every optional field and train the reader to
    ignore the warning.
    """
    html = "<div class='p'>   </div>"
    rule = _rule(selector=".p", post_process=[PostProcess(op="parse_price")])

    _data, warnings = extract_fields(html, rule)

    assert not any("post_process" in w for w in warnings), warnings


def test_a_pipeline_that_keeps_the_value_says_nothing():
    html = "<div class='p'>$199.00</div>"
    rule = _rule(selector=".p", post_process=[PostProcess(op="parse_price")])

    data, warnings = extract_fields(html, rule)

    assert data["price"] == 199.0
    assert warnings == []


def test_an_op_that_already_explained_itself_is_not_reported_twice():
    """An invalid regex nulls the value too, but `_apply_post_process` has
    already named the cause — a second, vaguer warning about the same field
    would just dilute it."""
    html = "<div class='p'>$199.00</div>"
    rule = _rule(selector=".p", post_process=[PostProcess(op="regex", args=["([unclosed"])])

    _data, warnings = extract_fields(html, rule)

    assert warnings == ["field 'price': invalid regex pattern"]


def test_the_dropped_text_goes_to_the_log_and_not_to_the_caller(caplog):
    """`warnings` is returned to API callers and matched by substring downstream:
    yozh-law-checker publishes a scan as `blocked` when it finds "captcha" in
    one. A sample of arbitrary page content there could publish a false verdict
    on a page that merely mentions the word, so the sample is logged instead."""
    html = "<i class='p'>solve the captcha to continue</i><i class='p'>captcha again</i>"
    rule = _rule(
        selector=".p", all=True,
        post_process=[PostProcess(op="regex", args=[r"^\$([\d.]+)$"])],
    )

    with caplog.at_level("WARNING", logger="src.extract.extractor"):
        _data, warnings = extract_fields(html, rule)

    assert warnings, "the column was nulled in full, so it must be reported"
    assert not any("captcha" in w.lower() for w in warnings), warnings
    assert any("solve the captcha" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "shape",
    [
        pytest.param("<a ", id="unclosed-bracket"),
        pytest.param("<script ", id="unclosed-script"),
    ],
)
def test_markup_with_no_closing_tag_does_not_pin_the_worker(shape):
    """Stripping markup with a regex was quadratic twice, in two different ways.

    First the tag scan: letting its any-other-character branch match `<` made an
    unclosed `<` run to end-of-string and backtrack once per following `<` —
    120 KB took 53 seconds. Then the script-body scan, which the first fix did
    not touch: `.*?</script>` with no closing tag has the same shape, and 600 KB
    took 45. Both are reachable through plain `attr="text"`, because lxml turns
    `&lt;script&gt;` back into real markup in text content.

    The cost is paid per NULL VALUE — the whole-column condition gates only the
    warning — so one snippet-less SERP result on a page that merely mentions
    `<script` burned 21 seconds and reported nothing at all.

    Both shapes are parametrised because fixing one left the other live: this
    test's original input passed against a reverted script regex.
    """
    payload = shape * 40_000
    started = time.perf_counter()
    _has_content(payload)
    assert time.perf_counter() - started < 2.0, "markup stripping went superlinear again"
