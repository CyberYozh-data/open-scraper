from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any, Dict, Tuple

from lxml import etree, html as lxml_html

from src.extract.models import ExtractRule, PostProcess

log = logging.getLogger(__name__)


def _text(node) -> str:
    return " ".join((node.text_content() or "").split())


def _outer_html(node) -> str:
    return lxml_html.tostring(node, encoding="unicode", with_tail=False)


def _select(doc, selector: str, kind: str):
    if kind == "css":
        return doc.cssselect(selector)
    return doc.xpath(selector)


def _pick(node, attr: str):
    # XPath may return strings, numbers, or attributes directly — propagate as-is.
    if isinstance(node, str):
        return node.strip() if attr == "text" else node
    if attr == "text":
        return _text(node)
    if attr == "html":
        return _outer_html(node)
    return node.get(attr)


def _apply_regex(value: Any, args: list[Any]) -> Any:
    if value is None:
        return None
    pattern = args[0] if args else ""
    group = args[1] if len(args) > 1 else None
    match = re.search(pattern, str(value))
    if match is None:
        return None
    if group is not None:
        try:
            return match.group(group)
        except IndexError:
            return None
    try:
        return match.group(1)
    except IndexError:
        return match.group(0)


def _strip_tags(value: Any) -> str | None:
    """Render an HTML fragment down to its text content.

    Same normalisation as `_text` (tags dropped, entities decoded, whitespace
    collapsed), so `attr='html'` + `regex` + `strip_tags` yields exactly what
    `attr='text'` on the same node would have — which is what lets a field
    anchor to an always-present container instead of an optional inner node
    without changing the value it returns.
    """
    if value is None:
        return None
    text = str(value)
    if "<" not in text and "&" not in text:
        return " ".join(text.split())
    # create_parent wraps loose/unbalanced fragments (a regex slice of a
    # container's html is rarely a single well-formed element). A parser
    # failure propagates to _apply_post_process, which warns and yields None
    # rather than silently substituting cruder output.
    frag = lxml_html.fragment_fromstring(text, create_parent="div")
    return " ".join((frag.text_content() or "").split())


# One integer, and only one. The grouped spelling must consume the WHOLE digit
# run — `(?!\d)` is what makes "exactly three digits" true rather than merely
# intended; without it a longer run was partially eaten and glued, so
# "+1 800 555 0199" came back as 1800555019, a plausible number with a digit
# silently missing.
#
# The backreference is what tells a grouped number from two numbers that merely
# look like one. A genuinely grouped number uses ONE separator throughout, and
# whatever follows it must not reuse that separator: a decimal tail always
# switches ("12 345,67"), a phone number's next group does not. `(?!\1\d)` is
# therefore the whole locale question answered without knowing the locale.
#
# The space stays in the class in both spellings. Localised pages emit U+00A0,
# but `_text` collapses it to an ASCII space before this function ever sees it,
# so refusing the ASCII one would break every grouped Russian and French count on
# the ordinary attr="text" path.
_INT_RE = re.compile(r"\d{1,3}(?:([.,  ])\d{3})(?:\1\d{3})*(?!\d)(?!\1\d)|\d+")
_INT_GROUPING = str.maketrans("", "", ".,  ")


def _parse_int(value: Any) -> int | None:
    """First integer in the text, not every digit in it concatenated.

    Stripping all non-digits reads a rating beside a count, a date, or a price
    as part of the number: "4.5 out of 5" became 455. That is worse than the
    None it returns for text with no digits — a null gets noticed, a plausible
    integer does not.
    """
    if value is None:
        return None
    text = str(value)
    match = _INT_RE.search(text)
    if match is None:
        return None
    # The sign must be ATTACHED to the number and must itself open the string or
    # follow whitespace: a hyphen inside a token ("XL-5") is punctuation and a
    # dash used as a separator ("Sale - 19") is not a sign. Same rule as
    # `_parse_price`, which learned it the same way.
    sign = 1
    head = text[: match.start()]
    if head.endswith("-") and (len(head) == 1 or head[-2].isspace()):
        sign = -1
    try:
        return sign * int(match.group(0).translate(_INT_GROUPING))
    except ValueError:
        return None


def _parse_price(value: Any, locale: str = "us") -> float | None:
    """Parse a numeric string with currency and thousands/decimal separators.

    `locale` resolves single-separator ambiguity:
      - "us" (default): `.` is decimal, `,` is thousands.
      - "eu": `,` is decimal, `.` is thousands.

    When both `.` and `,` appear, the last-position-wins rule is used and the
    locale arg is ignored — that case is unambiguous regardless of locale.
    """
    if value is None:
        return None
    text = str(value)
    # The class keeps the space because it is a thousands separator in several
    # locales ("1 234,56"). Requiring the match to START with a digit is what
    # separates that from a space merely sitting in front of the number: with a
    # leading `[\d., ]`, "Now $199.00" matched the single space at index 3,
    # which `replace(" ", "")` then reduced to "" and the guard below rejected.
    # Every labelled price ("From $12.50", "Sale price $9.99") parsed as None.
    # A separator may open the number ("$.99" is ordinary sub-dollar retail
    # typography) but only when a digit follows it, which is what keeps the
    # bare-space match out.
    match = re.search(r"(?:\d|[.,]\d)[\d., ]*", text)
    if match is None:
        return None
    raw = match.group(0).replace(" ", "")
    # The sign no longer rides along in the match, so read it from whatever sits
    # between the minus and the digits — a currency symbol, typically.
    # The sign must be ADJACENT: only currency symbols may sit between the minus
    # and the number, and the minus itself must open the string or follow
    # whitespace. Anything looser reads a separator dash ("Sale - $19.99") or a
    # hyphenated name ("XL-$19.99") as a negative price — silently, which is
    # worse than the null this function used to return.
    prefix = text[: match.start()].rstrip("$€£¥₽")
    sign = 1.0
    if prefix.endswith("-"):
        head = prefix[:-1]
        if not head or head[-1].isspace():
            sign = -1.0
    if not raw:
        return None

    last_dot = raw.rfind(".")
    last_comma = raw.rfind(",")
    decimal: str | None
    if last_dot > -1 and last_comma > -1:
        decimal = "." if last_dot > last_comma else ","
    elif last_dot > -1:
        if locale == "us":
            tail = raw.rsplit(".", 1)[-1]
            decimal = "." if (raw.count(".") == 1 and len(tail) <= 2) else None
        else:
            decimal = None
    elif last_comma > -1:
        if locale == "eu":
            tail = raw.rsplit(",", 1)[-1]
            decimal = "," if (raw.count(",") == 1 and len(tail) <= 2) else None
        else:
            decimal = None
    else:
        decimal = None

    if decimal == ".":
        clean = raw.replace(",", "")
    elif decimal == ",":
        clean = raw.replace(".", "").replace(",", ".")
    else:
        clean = raw.replace(",", "").replace(".", "")

    try:
        return sign * float(clean)
    except ValueError:
        return None


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return _parse_price(value)


def _apply_post_process(
    value: Any,
    ops: list[PostProcess],
    field_name: str,
    warnings: list[str],
    seen_warnings: set[tuple[str, str, str]],
) -> Any:
    """Apply post_process pipeline to a single value.

    `seen_warnings` is a per-extract_fields-call set of (field, op, kind)
    tuples; we only emit one warning per combination across all items in a
    list field (otherwise a 40-item list with a bad regex produces 40
    identical log lines).
    """

    def _warn(op: str, kind: str, message: str) -> None:
        key = (field_name, op, kind)
        if key in seen_warnings:
            return
        seen_warnings.add(key)
        warnings.append(message)

    current = value
    for step in ops:
        if current is None:
            return None
        op = step.op
        args = step.args or []
        try:
            if op == "regex":
                current = _apply_regex(current, args)
            elif op == "strip":
                chars = args[0] if args else None
                current = str(current).strip(chars) if chars is not None else str(current).strip()
            elif op == "strip_tags":
                current = _strip_tags(current)
            elif op == "parse_int":
                current = _parse_int(current)
            elif op == "parse_float":
                current = _parse_float(current)
            elif op == "parse_price":
                locale = args[0] if args else "us"
                current = _parse_price(current, locale=locale)
            elif op == "lowercase":
                current = str(current).lower()
            elif op == "uppercase":
                current = str(current).upper()
            elif op == "replace":
                current = str(current).replace(str(args[0]), str(args[1]))
            else:  # pragma: no cover — guarded by Literal at validation
                _warn(op, "unknown", f"field '{field_name}': unknown op '{op}'")
                return None
        except re.error:
            _warn(op, "regex", f"field '{field_name}': invalid regex pattern")
            return None
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _warn(op, "exception", f"field '{field_name}': {op} failed: {exc}")
            return None
    return current


# How much of the dropped text to log. Sliced BEFORE normalising: whitespace-
# collapsing a 10 MB `attr="html"` value first costs ~0.1s and ~200 MB of peak
# RSS, twice per page on the presets that anchor two fields on the document.
_DROPPED_SAMPLE_CHARS = 120


# A leftover regex for the unparseable case only. `[^<>]` rather than `[^>]`:
# letting the class match `<` makes an unclosed bracket scan to end-of-string and
# backtrack once per following `<`, which is quadratic on input the page controls.
_TAG_RE = re.compile(r"""<(?:[^<>"']|"[^"]*"|'[^']*')*>""")


def _has_content(raw: Any) -> bool:
    """True when the raw value carries text a pipeline could have used.

    `attr="html"` is the pattern this repo mandates for row-aligned fields, and
    the markup of an EMPTY container is a non-empty string — so testing the raw
    string makes this vacuous exactly where it matters most.

    Parsed rather than regexed, because "strip the markup" is not a regular
    language and two attempts at it were quadratic: a `<script>` with no closing
    tag makes any `.*?</script>` formulation scan to end-of-string and retry at
    every following `<script`, which cost 45 s on 600 KB — and the cost was paid
    per NULL VALUE, so an ordinary snippet-less SERP result on a page that merely
    mentions `<script` burned 21 s and reported nothing. An unrolled-loop regex
    measured 3x worse; the matching problem itself is quadratic when the close
    tag is absent, so no regex fixes it. lxml is linear, already relied on in
    this module for the same job, and correct on the cases a regex only
    approximated — script bodies, comments, entity-only text.
    """
    if raw is None:
        return False
    text = str(raw)
    if "<" not in text:
        return bool(unescape(text).strip() if "&" in text else text.strip())
    try:
        frag = lxml_html.fragment_fromstring(text, create_parent="div")
    except Exception:  # pylint: disable=broad-exception-caught
        # Unparseable fragment: answer "there is something here" from the cheap
        # pass rather than claiming empty. Claiming empty suppresses a real
        # warning, which is the failure direction this whole change removes.
        return bool(unescape(_TAG_RE.sub(" ", text)).strip())
    # Markup, not the value a pipeline was reaching for. `with_tail=False` keeps
    # the text that follows the element.
    etree.strip_elements(frag, "script", "style", etree.Comment, with_tail=False)
    return bool((frag.text_content() or "").strip())


def _warn_on_silent_nulls(
    field_name: str,
    ops: list[PostProcess],
    raw_values: list[Any],
    processed: list[Any],
    warnings: list[str],
    seen_warnings: set[tuple[str, str, str]],
    *,
    is_column: bool,
) -> None:
    """Report values the pipeline consumed without any op complaining.

    The three existing warnings all describe the *selector*: invalid, matched
    nothing, or matched a different number of nodes than its neighbours. Nothing
    covers "the selector matched, the node had text, and the pipeline returned
    None" — which is not an exotic case but the ordinary shape of markup drift: a
    regex anchored to a label that moved, a price format that gained a currency
    word, a separator that changed. It is also invisible to every check a caller
    can make, because the request succeeds and the field is simply null. That
    silence is why walmart_product shipped `price: null` on every live run until
    someone read the values instead of the counts.

    Not exempted: a field whose selector is the whole document. That was tried and
    reverted — it silenced `youtube_video.views`/`.likes`, whose preset documents
    them as parsed from `ytInitialData`, so a renamed payload key produced exactly
    the walmart shape with no warning at all. The discriminator would have to be
    "is this field expected to be empty", which is preset knowledge the engine does
    not have. `linkedin_profile` therefore warns on every logged-out run — and that
    warning is TRUE: its own description says to expect self-heal to carry those
    fields, i.e. the deterministic pipeline really did produce nothing.

    Only a column nulled in FULL is reported. A single missing value is ordinary
    — a SERP result without a snippet, an out-of-stock row without a price — and
    warning on it would fire on every optional field and train the reader to
    ignore the warning. Every value the selector matched being consumed is the
    unambiguous shape, and the one walmart_product shipped: it is the same
    reasoning `_missing_required` uses when it counts an all-null column as
    missing.

    Deliberately not raised: the pipeline may be right and the page may have
    changed, so this is a signal to go and look, not an error. Suppressed when an
    op already warned for this field, since that warning names the actual cause.
    """
    if any(key[0] == field_name for key in seen_warnings):
        return
    if is_column and len(raw_values) < 2:
        # A one-row page cannot tell "this result has no snippet" from "the
        # snippet pipeline is dead", and single-result SERPs are common enough
        # that guessing would make the warning untrustworthy. A scalar field has
        # no such ambiguity: its null IS the observation the caller wants
        # explained, which is exactly what walmart_product's price was.
        return
    # The denominator is the values that HAD something. Counting against every
    # node instead switched the detector off whenever one slot on the page
    # happened to be blank — common on real pages, and the walmart column would
    # have gone unreported again.
    dropped = [
        raw for raw, out in zip(raw_values, processed)
        if out is None and _has_content(raw)
    ]
    if not dropped:
        return
    had_content = [raw for raw in raw_values if _has_content(raw)]
    if len(dropped) != len(had_content):
        return
    pipeline = " -> ".join(step.op for step in ops)
    warnings.append(
        f"field '{field_name}': post_process ({pipeline}) returned null for "
        f"every non-empty value ({len(dropped)} of {len(raw_values)} matched)"
    )
    # The dropped text goes to the log, never into `warnings`. Warnings are
    # returned to API callers and scanned by substring downstream — yozh-law-
    # checker publishes a scan as blocked when it sees "captcha" in one — so a
    # sample of arbitrary page content there could publish a false verdict on a
    # page that merely mentions the word. `!r` because the sample is untrusted:
    # it escapes newlines and control characters out of the log line.
    log.warning(
        "post_process nulled every value of field=%r pipeline=%r sample=%r",
        field_name, pipeline,
        " ".join(str(dropped[0])[: _DROPPED_SAMPLE_CHARS * 2].split())[:_DROPPED_SAMPLE_CHARS],
    )


def extract_fields(page_html: str, rule: ExtractRule) -> Tuple[Dict[str, Any], list[str]]:
    doc = lxml_html.fromstring(page_html)
    data: Dict[str, Any] = {}
    warnings: list[str] = []
    seen_warnings: set[tuple[str, str, str]] = set()

    for name, field_rule in rule.fields.items():
        selector_type = field_rule.type or rule.type
        try:
            nodes = _select(doc, field_rule.selector, selector_type)
        except Exception:  # pylint: disable=broad-exception-caught
            warnings.append(f"field '{name}': invalid selector")
            data[name] = [] if field_rule.all else None
            continue

        if not nodes:
            if field_rule.required:
                warnings.append(f"field '{name}': required selector not found")
            data[name] = [] if field_rule.all else None
            continue

        raw_values = (
            [_pick(node, field_rule.attr) for node in nodes]
            if field_rule.all
            else [_pick(nodes[0], field_rule.attr)]
        )

        if field_rule.post_process:
            processed = [
                _apply_post_process(
                    value, field_rule.post_process, name, warnings, seen_warnings
                )
                for value in raw_values
            ]
            _warn_on_silent_nulls(
                name, field_rule.post_process,
                raw_values, processed, warnings, seen_warnings,
                is_column=bool(field_rule.all),
            )
        else:
            processed = raw_values

        data[name] = processed if field_rule.all else processed[0]

    # Every `all=true` field is matched against the whole document
    # independently — there is no notion of a "row" in this engine. Callers
    # nonetheless treat the resulting lists as parallel columns and zip them
    # by index (prices[i] belongs to titles[i]), an assumption nothing here
    # has ever verified. A length mismatch — including one field coming back
    # unexpectedly empty — deserves a loud warning even though the request
    # itself should still succeed.
    #
    # This is a LENGTH check, not an alignment check: compensating errors
    # inside a single field (e.g. one card missing a rating while another
    # renders an extra node) can leave all lengths equal while the rows are
    # still misaligned. An empty `warnings` list here is not proof the rows
    # line up.
    all_field_lengths = {
        name: len(data[name]) for name, field_rule in rule.fields.items() if field_rule.all
    }
    if len(all_field_lengths) >= 2 and len(set(all_field_lengths.values())) > 1:
        summary = ", ".join(f"'{name}'={length}" for name, length in all_field_lengths.items())
        warnings.append(
            "row_alignment_mismatch: all=true fields have different lengths "
            f"({summary}) — consumers that zip these lists by index may pair "
            "values with the wrong row. If these fields are genuinely "
            "independent and not meant to align row-by-row, this warning can "
            "be ignored."
        )

    return data, warnings
