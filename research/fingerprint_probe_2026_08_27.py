"""What each engine actually emits, measured at BrowserLeaks.

Run before the preset audit. A preset that failed is only interpretable next to
the fingerprint its engine was presenting at the time, and re-fetching later
gets a different exit and a different answer.

## MAX_TEXTURE_SIZE: corrected after review (fix round 1)

The first version of this docstring read 8192-vs-16384 as a rasteriser
measurement -- SwiftShader capping at 8192 where llvmpipe reports 16384. That
was also wrong, and it was corrected against the vendored corpus, not another
guess: `/usr/local/lib/python3.10/dist-packages/camoufox/webgl/webgl_data.db`
inside the running worker holds 33 rows; `MAX_TEXTURE_SIZE` (key `'3379'` under
`webGl:parameters`) distributes `{8192: 13, 16384: 18, 32768: 2}` across them,
and the row `windows_on_host` pins -- `Google Inc. (AMD)` / `ANGLE (AMD,
Radeon R9 200 Series ...)` -- carries 16384 for both `webGl` and `webGl2`.

Camoufox never measures this parameter. It serves the *entire* WebGL parameter
block from that corpus row, keyed on the GPU identity that is pinned. The
proof that it is a row lookup and not a measurement is in the corpus itself:
the *same* renderer string, `Radeon R9 200 Series`, carries **8192** under
vendor `ATI Technologies Inc.` and **16384** under `Google Inc. (AMD)` --
verified directly (`webGl:parameters` and `webGl2:parameters` key `'3379'`
both agree, for both rows). Same card name, two different ceilings, decided
purely by which row is selected. The number tracks the claimed identity, not
the hardware and not the rasteriser.

(An earlier draft of this paragraph illustrated the point with "pin the Intel
row instead and the same container reports 8192". That was false, and it is
recorded here rather than silently dropped because it is the third unchecked
claim this one measurement produced: the `windows`/`intel` row this repo would
actually pin -- `ANGLE (Intel, Intel(R) HD Graphics Direct3D11 vs_5_0
ps_5_0)`, vendor `Google Inc. (Intel)` -- carries 16384 too, independently
re-verified against the corpus. Only the `vs_4_x` Intel rows are 8192, and
`windows_on_host` cannot resolve to them.)

That inverts what the two numbers suggest at a glance:

- **Camoufox is internally consistent.** It claims an R9 200 and reports the
  R9 200's parameters -- both come from the one corpus row.
- **Chromium carries the detectable contradiction.** It claims this host's
  real 780M through `chromium_webgl_identity()` in
  `src/browser/fingerprint_profile.py`, but nothing overrides
  `MAX_TEXTURE_SIZE`, so it reports SwiftShader's actual ceiling, 8192 --
  where a real 780M reports 16384.

So `data.max_texture_size` for `camoufox` is **a corpus value, not a
capability measurement** -- it says which row `windows_on_host` pinned, and
nothing about what this container can actually rasterise. Do not read it as
"Camoufox has more headroom than Chromium." The engine naming the true
hardware (Chromium) is the one a site can catch; the engine that is
internally coherent (Camoufox) is coherent about a 2013 card it does not have.
Full argument: `docs/superpowers/specs/2026-08-27-dual-engine-presets-design.md`,
"Known limit: the GPU claim is a name over software rendering".

## webgl_report_hash: Camoufox's WebGL readback produces no pixels at all

`#gl-report-hash` stays a genuinely empty `<span>` for `camoufox` on every
attempt (verified with two dedicated waits, up to 55s, each ending in
`selector_not_found`). This is not a hash that failed to converge -- the two
neighbouring rows that would feed it are equally empty: `#gl-image-hash` (a
second, independent hash of the rendered test image) and `#gl-image-src` (a
`<canvas>` element holding that image) are both present as empty `<td>`s with
no `<canvas>` tag inside them at all, where Chromium's capture has a real
`<canvas width="256" height="128">` and a populated hash next to it. Camoufox's
WebGL context is producing no drawable output for BrowserLeaks' test scene,
not merely a value that will not settle. `webgl_report_hash` is the one field
left `required: false` for exactly this reason -- it is expected to come back
empty for `camoufox`, and that emptiness is itself the recorded result, not a
probe defect.

## WebRTC: a stale launch-time IP, not a tunnel leak (fix round 1 correction)

The first version of this docstring described Camoufox's `webrtc_public`
differing from `ip_address` as a WebRTC leak around the proxy tunnel. That
diagnosis was wrong. `webrtc:ipv4` is not read live from the page's own
connection -- Camoufox's launcher calls `public_ip()` through the proxy
*once, at browser launch* (`camoufox/utils.py:549-558` in the vendored
package) and pins that single value into the WebGL/WebRTC config for the
whole browser lifetime. With `proxy_type=prem_res_rotating`, the exit used for
that one launch-time call and the exit used for the actual page load are two
different draws from the same rotating pool, so `webrtc_public` is simply
stale by the time the page reports it -- which is exactly why it landed in two
different /24s across two separate runs of this probe despite both being
Camoufox/`windows_on_host` requests against the same rotating pool. (The exact
addresses are not repeated here -- see "IP addresses are truncated" below for
why.)

The consequential part is not the stale IP by itself: that same launch-time
`geoip` value also feeds `get_geolocation(geoip)` at `camoufox/utils.py:562`,
which is where Camoufox's spoofed **timezone and locale** come from. Every run
so far has drawn a GB exit at both the launch call and the page load, so exit
and geolocation always agreed and nothing looked wrong. Nothing here rules out
a rotating pool crossing a country boundary between those two draws -- if it
does, a Camoufox request would present a timezone/locale for one country while
BrowserLeaks (and the target site) sees an exit in another. This probe does
not attempt to reproduce that (it is not a fix for this task, per review), but
`data.webrtc_public` next to `data.ip_address` on the `camoufox`/`ip` record is
the signal to watch for it in a later run.

## IP addresses are truncated to /24 before they are ever written

`ip_address`, `webrtc_local` and `webrtc_public` are real residential proxy
exits -- third parties' addresses, not this project's. `_truncate_ip` below
zeroes an IPv4 address's last octet (e.g. `203.0.113.200` -> `203.0.113.0/24`,
a documentation-range example per RFC 5737 -- not a real exit this probe ever
saw) or an IPv6 address's low 80 bits (down to a /48) before a record is
appended, so the committed JSON never carries a full address. This preserves
every comparison this probe makes (Chromium vs Camoufox `ip_address`; whether
`webrtc_public` matches `ip_address` at all), which only need the values to be
visibly the same or different, not exact. The *captured* HTML in
`research/fingerprint_probe_captures/` (git-ignored, transient, never
committed) is untruncated -- redacting a saved page is out of scope for a
directory that is not written to git in the first place.

## Selectors and wait strategy

Selectors were discovered by capturing all six pages once (raw_html=true, no
extract) and reading the saved HTML for real ids -- see
research/fingerprint_probe_captures/ (git-ignored, transient) and
task-3-report.md for exactly where each id was found. Every field below
anchors on a stable `id` attribute; none needed an XPath row-label fallback.

Wait strategy, changed from the plan's `wait_until: networkidle`: BrowserLeaks
fills its tables from client-side JS *after* the network goes idle (some
values -- Canvas Uniqueness, the WebRTC IP rows -- depend on an async call
that starts after the visible fields already have text). `networkidle`
resolved in ~8.5s for Camoufox and returned a `<head>`-only document with no
`<body>` at all: fetch_ok=true, status_code=200, no warnings, nothing wrong by
the metrics the API can see, just a page that never got its JS. Chromium's
first `networkidle` attempt happened to work, but nothing here guarantees it
always will, so both engines now use the same, verified strategy:
`wait_until: domcontentloaded` (cheap, fires immediately) plus a per-page
`wait_for_selector` naming a field that only appears once BrowserLeaks' JS has
actually populated the table, with `timeout_ms` raised to 50000 for that wait.
This was verified live against both engines for every page below.

## required=True is what makes a silent selector failure visible

`FieldRule.required` defaults to False (`src/extract/models.py`), and the
extractor (`src/extract/extractor.py`) only appends a warning when a required
selector matches zero nodes. The first version of this script set `required`
nowhere, so a page that rendered but a selector that stopped matching (a
BrowserLeaks markup change, a mistyped id) would produce a record
byte-for-byte shaped like a healthy one: `warnings: []`, `data.<field>: None`.
Every field that must populate now sets `required: True`, so Task 4's
unattended re-runs get an actual warning instead of a null a human has to
notice. The sole exception is `webgl_report_hash`, which is `required: False`
on purpose -- see above.

**`required: True` is not a completeness guarantee.** It fires only when the
selector matches zero nodes -- a page that renders a matching element with
empty text (BrowserLeaks' own "still loading" state, e.g. `canvas-ratio`
briefly carrying `class="load-td"` and no text before its async lookup
lands -- see "Selectors and wait strategy" above) produces `data.<field>: ""`
with `warnings: []`, not a warning. `warnings: []` on a record means no
selector went fully missing; it does not mean every field is non-empty. Read
`data` itself, not just `warnings`, before trusting a record as complete.

  python3 research/fingerprint_probe_2026_08_27.py
"""
from __future__ import annotations

import ipaddress
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = os.environ.get("BASE", "http://localhost:18000/api/v1")
OUT = Path(__file__).with_suffix(".json")
PROXY_TYPE = "prem_res_rotating"

ENGINES = {
    "chromium": {"browser_engine": "chromium", "stealth": True},
    "camoufox": {"browser_engine": "camoufox", "fingerprint_profile": "windows_on_host"},
}

# Selectors recorded from the Step 2 captures (research/fingerprint_probe_captures/,
# not committed). Every one of these was read off a real rendered page for BOTH
# engines; none is guessed. All are plain CSS ids -- no XPath fallback was needed.
#
# The `!` BrowserLeaks prefixes onto WebGL vendor/renderer (a `<span
# class="exclaim">!</span>` sibling text node inside the same <td>) is stripped
# with a post_process regex rather than dropped from the record -- it renders
# whether or not the underlying value is masked and is not itself evidence of
# anything, so keeping the strip inline (instead of a separate raw field) keeps
# the record readable without losing information a human would need to re-derive
# it from raw_html.
_STRIP_EXCLAIM = [{"op": "regex", "args": [r"^\s*!?\s*(.*)$"]}]

PAGES = {
    "webgl": {
        "url": "https://browserleaks.com/webgl",
        # Fires once the whole GL parameter table (further down the DOM than
        # vendor/renderer) is in the document; never waits on gl-report-hash,
        # which does not reliably populate for camoufox (see module docstring).
        "wait_for_selector": "#MAX_TEXTURE_SIZE",
        "fields": {
            "unmasked_vendor": {
                "selector": "#UNMASKED_VENDOR_WEBGL", "attr": "text",
                "post_process": _STRIP_EXCLAIM, "required": True,
            },
            "unmasked_renderer": {
                "selector": "#UNMASKED_RENDERER_WEBGL", "attr": "text",
                "post_process": _STRIP_EXCLAIM, "required": True,
            },
            "max_texture_size": {
                "selector": "#MAX_TEXTURE_SIZE", "attr": "text", "required": True,
            },
            # NOT required: genuinely, reproducibly empty for camoufox (no
            # WebGL readback at all -- see module docstring). A missing value
            # here is the expected result, not a probe failure.
            "webgl_report_hash": {
                "selector": "#gl-report-hash", "attr": "text", "required": False,
            },
        },
    },
    "canvas": {
        "url": "https://browserleaks.com/canvas",
        # #canvas-hash (the signature itself) appears before the async lookup
        # that fills in #canvas-ratio's uniqueness percentage; waiting on the
        # hash alone raced that lookup and captured canvas-ratio's loading
        # state (class="load-td", empty) for camoufox. Wait on the ratio cell
        # actually finishing instead.
        "wait_for_selector": "#canvas-ratio:not(.load-td)",
        "fields": {
            "canvas_signature": {
                "selector": "#canvas-hash", "attr": "text", "required": True,
            },
            "canvas_uniqueness": {
                "selector": "#canvas-ratio", "attr": "text", "required": True,
            },
        },
    },
    "ip": {
        "url": "https://browserleaks.com/ip",
        # #rtc-local resolves (to "n/a" or a real value) at least as late as
        # #rtc-public and #client-ipv4 in practice; waiting on it covers all
        # three fields below without racing the WebRTC candidate-gathering step.
        "wait_for_selector": "#rtc-local:not(:empty)",
        "fields": {
            "ip_address": {
                "selector": "#client-ipv4", "attr": "data-ip", "required": True,
            },
            "country": {
                "selector": "#client-ipv4", "attr": "data-iso_code", "required": True,
            },
            # The plan's placeholder field was singular ("webrtc"); the real
            # page exposes two independent rows (Local IP Address / Public IP
            # Address) and FieldRule is one selector -> one scalar, so this
            # splits into two output keys rather than inventing a nested shape
            # the extractor does not support.
            "webrtc_local": {
                "selector": "#rtc-local", "attr": "text", "required": True,
            },
            "webrtc_public": {
                "selector": "#rtc-public", "attr": "text", "required": True,
            },
        },
    },
}

# Fields on the "ip" page whose values are addresses and must be truncated
# before a record is ever written -- see module docstring, "IP addresses are
# truncated to /24".
_IP_FIELDS = ("ip_address", "webrtc_local", "webrtc_public")


def _truncate_ip(value):
    """Truncate a real IP address; pass through anything that isn't one.

    IPv4 loses its last octet: `203.0.113.200` -> `203.0.113.0/24` (a
    documentation-range example per RFC 5737, not a real exit this probe ever
    saw). IPv6 loses its low 80 bits, truncated to a /48 -- Camoufox's own
    `webrtc:ipv6` branch (`camoufox/utils.py`) means a v6 WebRTC address is a
    real possibility here even though every exit observed so far has been
    IPv4. `ipaddress.ip_address` is what decides "is this actually an IP" --
    anything it rejects (e.g. Chromium's honest `"n/a"` WebRTC answer, or an
    empty string) passes through unchanged, which a hand-rolled IPv4-only
    regex would not have caught for a v6 value. Keeps every analytical use
    this probe makes of an address (same/different-country,
    same/different-exit) while never writing a third party's full address
    into a committed file.
    """
    if not isinstance(value, str):
        return value
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return value
    prefix = 24 if ip.version == 4 else 48
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))


def _post(path, payload):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.load(r)


def probe(engine: str, page: str) -> dict:
    spec = PAGES[page]
    engine_config = ENGINES[engine]
    payload = {
        "url": spec["url"],
        "proxy_type": PROXY_TYPE,
        "wait_until": "domcontentloaded",
        "wait_for_selector": spec["wait_for_selector"],
        "timeout_ms": 50000,
        "block_assets": False,
        "extract": {
            "type": "css",
            "fields": spec["fields"],
        },
        **engine_config,
    }
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    job_id = _post("/scrape/page", payload)["job_id"]
    deadline = time.time() + 300
    while time.time() < deadline:
        if _get(f"/scrape/{job_id}").get("status") in (
            "done", "failed", "cancelled"
        ):
            break
        time.sleep(3)
    envelope = _get(f"/scrape/{job_id}/results")
    results = envelope.get("results") or []
    first = results[0] if results else {}
    meta = (first or {}).get("meta") or {}
    data = dict((first or {}).get("data") or {})
    if page == "ip":
        for key in _IP_FIELDS:
            if key in data:
                data[key] = _truncate_ip(data[key])
    rec = {
        "engine": engine,
        "page": page,
        "data": data,
        "meta": {
            "status_code": meta.get("status_code"),
            "fetch_ok": bool(meta.get("fetch_ok")),
            "applied_fingerprint": meta.get("applied_fingerprint"),
            "applied_user_agent": meta.get("applied_user_agent"),
            "applied_timezone": meta.get("applied_timezone"),
            "applied_locale": meta.get("applied_locale"),
        },
        "warnings": ((first or {}).get("warnings") or [])[:3],
        # elapsed_s is wall-clock around the whole request, quantised to this
        # script's 3s poll interval -- every value observed so far is a
        # multiple of 3. took_ms is the worker's own per-request timing and is
        # the one to use for anything that needs real precision.
        "elapsed_s": round(time.perf_counter() - started, 1),
        "took_ms": (first or {}).get("took_ms"),
        # Lets a record reconstruct the request that produced it without
        # cross-referencing this file's history.
        "config": {
            "started_at": started_at,
            "proxy_type": PROXY_TYPE,
            **engine_config,
        },
    }
    print(f"{engine}/{page}: {rec['data']}", flush=True)
    if rec["warnings"]:
        print(f"  warnings: {rec['warnings']}", flush=True)
    return rec


def main() -> None:
    records = [probe(e, p) for e in ENGINES for p in PAGES]
    OUT.write_text(
        json.dumps(records, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
