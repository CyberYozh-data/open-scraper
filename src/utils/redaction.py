"""Credential stripping for anything that might be logged, raised or returned.

Lives here rather than beside one provider because four modules need it —
`socks_bridge` is engine-generic, `app` logs a Redis URL, and the CyberYozh
client and provider both handle credentialed URLs. A copy per module is how the
rule ("never embed the raw credential string in errors or logs — these messages
travel into HTTP 502 details and service logs") ended up written in one file and
broken in the file next to it.
"""
from __future__ import annotations

from urllib.parse import urlsplit

_UNPARSEABLE = "<redacted>"


def redact_url(url: str) -> str:
    """`scheme://host:port` — userinfo, path and query removed.

    FAILS CLOSED. A URL with no scheme returns the placeholder rather than
    itself, which matters because that is exactly the input that reaches a
    redactor: a URL `urlsplit` cannot make sense of is the one that made a
    caller raise or warn in the first place. An earlier version returned such
    input unchanged and leaked `user:pass@host` at WARNING from the branch it
    was added to guard.

    Path and query go too: a rotate endpoint carrying `?apikey=` is the same
    class of secret as the userinfo, and no caller here logs a URL for its path.
    """
    if not url:
        return _UNPARSEABLE
    # `urlsplit` only accepts [A-Za-z][A-Za-z0-9+.-]* as a scheme, so the
    # provider's own `socks5_http://` parses as a path and this function fell
    # back to the placeholder — safe, but it cost the host:port that made the
    # log line worth having. Parse a sanitized copy, print the real spelling.
    separator = url.find("://")
    scheme = url[:separator] if separator > 0 else ""
    parseable = url.replace("_", "-", 1) if "_" in scheme else url
    parts = urlsplit(parseable)
    if not parts.scheme or not parts.hostname:
        return _UNPARSEABLE
    port = f":{parts.port}" if parts.port else ""
    # hostname lowercases and strips IPv6 brackets; put them back.
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"{scheme or parts.scheme}://{host}{port}"


def redact_mapping(payload: dict, *, secret_keys: frozenset[str]) -> dict:
    """A copy of `payload` with the named keys masked.

    For request bodies that get interpolated into exception messages — those
    messages become HTTP 502 details and land in a job's caller-visible `error`
    and `warnings`, so the body cannot travel with them intact.
    """
    return {
        key: ("<redacted>" if key in secret_keys else value)
        for key, value in payload.items()
    }
