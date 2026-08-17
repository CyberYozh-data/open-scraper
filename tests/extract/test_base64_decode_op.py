"""`base64_decode`: read a URL out of a click-tracking wrapper.

Bing wraps every organic href as
`https://www.bing.com/ck/a?...&u=a1<base64url>`, so `links` extracted from that
href points at bing.com on every row — a field that is 100% populated and 100%
useless. The destination is recoverable, but only with a base64 step, which the
post_process vocabulary did not have.
"""
from __future__ import annotations

import base64

import pytest

from src.extract.extractor import extract_fields
from src.extract.models import ExtractRule, FieldRule, PostProcess

# The real shape, taken from a live SERP: url-safe alphabet, padding stripped.
_TARGET = "https://www.cnet.com/tech/computing/best-laptop/"
_ENCODED = base64.urlsafe_b64encode(_TARGET.encode()).decode().rstrip("=")
_WRAPPER = (
    f"https://www.bing.com/ck/a?!&amp;&amp;p=006aef73adffc80d&amp;ptn=3"
    f"&amp;u=a1{_ENCODED}&amp;ntb=1"
)
HTML = f'<li class="b_algo"><h2><a href="{_WRAPPER}">CNET</a></h2></li>'

_UNWRAP = [
    PostProcess(op="regex", args=[r"[?&]u=a1([A-Za-z0-9_-]+)"]),
    PostProcess(op="base64_decode"),
]


def _run(html, ops, selector="li.b_algo h2 a", attr="href"):
    rule = ExtractRule(
        type="css",
        fields={"link": FieldRule(selector=selector, attr=attr, post_process=ops)},
    )
    return extract_fields(html, rule)


class TestBase64Decode:
    def test_the_whole_bing_wrapper_round_trip(self):
        """What the preset will actually do: pull `u=a1…` out, then decode."""
        data, warnings = _run(HTML, _UNWRAP)

        assert data["link"] == _TARGET
        assert warnings == []

    def test_the_url_safe_alphabet_is_handled(self):
        """`-` and `_` stand in for `+` and `/`; the standard decoder rejects them."""
        target = "https://example.com/a?b=c&d=~e/f+g??"
        encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        assert "-" in encoded or "_" in encoded, "pick a target that exercises the alphabet"

        data, warnings = _run(
            f'<li class="b_algo"><h2><a href="x?u=a1{encoded}">t</a></h2></li>', _UNWRAP
        )

        assert data["link"] == target
        assert warnings == []

    def test_undecodable_input_warns_instead_of_taking_the_row_down(self):
        """A wrapper whose shape Bing changes must degrade to a null cell."""
        data, warnings = _run(
            '<li class="b_algo"><h2><a href="not-a-wrapper">t</a></h2></li>',
            [PostProcess(op="base64_decode")],
        )

        assert data["link"] is None
        assert any("base64_decode" in w for w in warnings), warnings

    def test_an_href_that_is_not_a_wrapper_yields_null_not_a_bogus_value(self):
        """Bing serves unwrapped hrefs too; they must not be mangled."""
        data, _ = _run(
            '<li class="b_algo"><h2><a href="https://www.cnet.com/direct">t</a></h2></li>',
            _UNWRAP,
        )

        assert data["link"] is None

    def test_bytes_that_are_not_utf8_do_not_crash(self):
        encoded = base64.urlsafe_b64encode(b"\xff\xfe\x00binary").decode().rstrip("=")

        data, warnings = _run(
            f'<li class="b_algo"><h2><a href="x?u=a1{encoded}">t</a></h2></li>', _UNWRAP
        )

        assert data["link"] is not None or warnings


def test_the_op_is_declared_so_a_preset_can_use_it():
    assert PostProcess(op="base64_decode").op == "base64_decode"


def test_input_outside_the_alphabet_is_refused_not_silently_salvaged():
    """The reason for validate=True, with an input that actually distinguishes.

    Without validation the decoder DISCARDS characters outside the alphabet, so
    a corrupted wrapper does not fail — it yields a shorter, entirely plausible
    URL. Measured: `aHR0cHM6Ly9!!!leGFtcGxlLmNvbQ` decodes to
    `https://example.com` when the stray `!!!` are dropped. A field that ships a
    wrong URL is worse than one that ships null, because nothing downstream can
    tell. (An earlier version of this test used input that failed the UTF-8
    decode either way, so it passed with the validation removed.)
    """
    corrupted = "aHR0cHM6Ly9!!!leGFtcGxlLmNvbQ"

    data, warnings = _run(
        f'<li class="b_algo"><h2><a href="{corrupted}">t</a></h2></li>',
        [PostProcess(op="base64_decode")],
    )

    assert data["link"] is None, "silently salvaged into a plausible wrong URL"
    assert any("base64_decode" in w for w in warnings), warnings
