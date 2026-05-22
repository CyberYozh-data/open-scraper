from __future__ import annotations

import re
from typing import Any, Dict, Tuple

from lxml import html as lxml_html

from src.extract.models import ExtractRule, PostProcess


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


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    digits = re.sub(r"[^\d-]", "", str(value))
    if not digits or digits in ("-",):
        return None
    try:
        return int(digits)
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
    match = re.search(r"-?[\d., ]+", str(value))
    if match is None:
        return None
    raw = match.group(0).replace(" ", "")
    sign = -1.0 if raw.startswith("-") else 1.0
    raw = raw.lstrip("-")
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
        else:
            processed = raw_values

        data[name] = processed if field_rule.all else processed[0]

    return data, warnings
