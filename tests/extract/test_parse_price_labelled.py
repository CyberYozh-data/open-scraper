"""A label before the number must not swallow the price.

`_parse_price` searched for `-?[\\d., ]+`, and that character class contains a
SPACE. On any string where a space precedes the number and is not itself
followed by a digit — "Now $199.00", "From $12.50", a leading non-breaking
space — the search matched the space alone, `replace(" ", "")` reduced it to
the empty string, and the `if not raw` guard returned None.

Found on walmart_product, whose live price node renders as "Now $199.00": the
selector always matched, the field was not `required`, and the preset returned
`price: null` on every run with no warning. The same shape reaches any preset
whose price node carries a label, so the fix belongs here rather than in a
per-preset regex.

The space stays in the class deliberately — it is a thousands separator in
several locales ("1 234,56"). Requiring the match to START with a digit is what
distinguishes a separator inside a number from a gap before one.
"""
from __future__ import annotations

import pytest

from src.extract.extractor import _parse_price


class TestLabelledPrices:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("Now $199.00", 199.0),
            ("From $12.50", 12.5),
            ("Price when purchased online $199.00", 199.0),
            ("\xa0Now $199.00", 199.0),
            ("Sale price $1,299.99", 1299.99),
            ("Now $199", 199.0),
        ],
    )
    def test_label_before_the_number_does_not_swallow_it(self, value, expected):
        assert _parse_price(value) == expected


class TestSpaceThousandsSeparatorStillWorks:
    """The reason the space is in the character class at all."""

    def test_space_grouped_thousands_us(self):
        assert _parse_price("1 234.56") == 1234.56

    def test_space_grouped_thousands_eu(self):
        assert _parse_price("1 234,56", "eu") == 1234.56

    def test_space_grouped_millions(self):
        assert _parse_price("12 345 678.90") == 12345678.90

    def test_label_and_space_grouping_together(self):
        assert _parse_price("Now 1 234,56 €", "eu") == 1234.56


class TestBehaviourNotRegressed:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("$19.99", 19.99),
            # Unchanged and intentional: under the default us locale a lone
            # comma is a thousands separator. materializer.py injects "eu"
            # for the locales where it is the decimal mark.
            ("899,00 €", 89900.0),
            ("1.234,56", 1234.56),
            # Newly correct, not unchanged: the sign used to ride inside the
            # match, so "-$5.00" matched the bare "-", reduced to "" and
            # returned None. It is now read from the text before the digits.
            ("-$5.00", -5.0),
            ("no digits here", None),
            ("", None),
            (None, None),
        ],
    )
    def test_shapes_that_must_keep_working(self, value, expected):
        assert _parse_price(value) == expected

    def test_eu_locale_comma_decimal(self):
        assert _parse_price("899,00 €", "eu") == 899.0

    def test_leading_minus_is_still_a_sign_not_a_separator(self):
        assert _parse_price("Now -$5.00") == -5.0


class TestSignMustBeAdjacent:
    """A dash that is not attached to the number is not a minus sign.

    The first attempt read an unbounded prefix, so "Sale - $19.99" parsed as
    -19.99 and "XL-$19.99" — which main got right — flipped to negative. A
    wrong sign is invisible downstream; the null it replaced was not.
    """

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("Sale - $19.99", 19.99),
            ("Now - $19.99", 19.99),
            ("Rollback - $12.50", 12.5),
            ("Deal of the Day - $24.99", 24.99),
            ("XL-$19.99", 19.99),
        ],
    )
    def test_separator_dash_is_not_a_minus(self, value, expected):
        assert _parse_price(value) == expected

    @pytest.mark.parametrize("value", ["-$5.00", "Now -$5.00", "-5.00", "-€5,00"])
    def test_adjacent_minus_is_still_a_minus(self, value):
        assert _parse_price(value, "eu" if "€" in value else "us") == -5.0


class TestLeadingSeparator:
    """"$.99" is ordinary sub-dollar retail typography, and a `regex` step can
    hand this function a leading-dot slice directly. Requiring a bare leading
    digit moved the match onto the fraction and multiplied the value by 100."""

    @pytest.mark.parametrize(
        "value,locale,expected",
        [
            (".99", "us", 0.99),
            ("$.99", "us", 0.99),
            ("$.99 each", "us", 0.99),
            (".5", "us", 0.5),
            (",99", "eu", 0.99),
            ("-.5", "us", -0.5),
        ],
    )
    def test_leading_separator_keeps_the_magnitude(self, value, locale, expected):
        assert _parse_price(value, locale) == expected
