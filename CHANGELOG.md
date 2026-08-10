# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.8] - 2026-08-10

Anti-detection and reliability release: a new `fingerprint_profile` request
field pins the Camoufox OS/GPU instead of a per-launch random draw, the retry
loop now stays inside the task deadline, extraction reports a silently-nulled
column, and the queue result is a typed envelope. Two Camoufox fingerprint
tells are removed and three options are no longer silently ignored. Adds a
GitHub Actions CI pipeline. No breaking API change.

### Added

- `fingerprint_profile` on `POST /scrape/page`, `POST /scrape/pages` and the
  crawler/search `scrape_options`: pin the Camoufox fingerprint's OS and WebGL
  vendor instead of Camoufox's per-launch random draw (which claimed an OS the
  server is not two launches in three). Profiles: `auto` (default, resolves to
  `CAMOUFOX_FINGERPRINT_PROFILE`, ships `windows_on_host`), `windows_on_host`,
  `host`, `random`, and the bare names `windows` / `macos` / `linux`. The old
  `spoof_os` keeps working and equals the three bare names; a caller stating
  both must not name conflicting operating systems. `meta.applied_fingerprint`
  reports what actually ran, including when a profile degraded. New env vars
  `CAMOUFOX_FINGERPRINT_PROFILE` (default `windows_on_host`) and
  `HOST_GPU_VENDOR`.
- Extraction warns when a `post_process` pipeline nulls an entire column (every
  row) — a fourth class of silent failure the invalid/empty/length-mismatch
  selector checks did not cover. Reported only for a fully-nulled column with
  more than one row, or a scalar field; an optional value missing on a single
  row stays quiet.
- GitHub Actions CI: `pylint` and `pytest -m "not e2e"` on every push and pull
  request, the checks the repo already defined but never enforced.

### Changed

- `run_scrape` now returns a typed envelope (`ScrapeOk` / `ScrapeErr`) built as
  the same pydantic models the API validates on the way out, with `mypy` over
  the queue surface. An older worker meeting a metadata value a newer API
  introduced degrades that field and keeps the fetched page, rather than
  dropping it. Internal refactor; the public HTTP response is unchanged.
- `session_id`, `cookies` and `render` are now rejected with 422 on the
  Camoufox engine instead of being accepted and silently ignored (which
  returned a logged-out or non-rendered page that read as the site having
  changed). `/search` maps the rejection to a 400 rather than a 500.
- `WEBRTC_BLOCK` no longer deletes `RTCPeerConnection` on Camoufox: deleting the
  constructor was itself a fingerprint tell (real Firefox always has it). The
  native constructor is kept and Camoufox's `webrtc:ipv4` spoof re-enabled.
- Camoufox `geoip` is now toggleable via `CAMOUFOX_GEOIP` (default on), so the
  per-request exit-IP lookup and its credentialed cache can be turned off where
  the locale/timezone alignment is not worth the cost.

### Fixed

- The retry loop stays inside `PAGE_TASK_TIMEOUT_S`: an attempt that cannot fit
  the remaining task budget is shortened (or not started) instead of cancelled
  mid-fetch, so the block verdict, the html, the status and the retry count
  reach the caller instead of a bare `page task exceeded` timeout. A transient
  5xx is no longer promoted over a real captcha as the reported attempt.
- `parse_int` no longer fabricates numbers by stripping every non-digit and
  concatenating what is left (`4.5 out of 5` returned `455`); it takes the
  first integer with locale-correct thousands grouping (`4.5 out of 5` -> `4`,
  `12 345,67` -> `12345`).
- Camoufox window/screen geometry is now physically coherent: the screen floor
  is stated alongside the forced window, so a spoofed window is never larger
  than its own monitor (a tell in 5 of 6 launches before), and an oversized
  request no longer fails the whole scrape.

## [0.1.7] - 2026-08-04

Build fix.

### Fixed

- Pinned `mcp>=1.28.1,<2` in both `requirements.txt` and
  `yozh-crawler/requirements.txt`. `mcp` 2.0.0 made `Server.__init__`
  keyword-only, which `fastapi-mcp` 0.4.x calls positionally, so a fresh
  `docker build` shipped services that raised `TypeError` in `create_app()`
  before serving anything. The floor keeps the GHSA-vj7q-gjh5-988w fix. Build
  time only; no runtime or API change.

## [0.1.6] - 2026-07-30

Extraction and preset reliability release: repaired marketplace/SERP presets,
more robust price parsing, and a browser fix that tells a blocked page apart
from a slow one (with a session-persistence correctness fix). No API change.

### Fixed

- Preset extraction repaired for current site layouts: eBay search (new s-card
  layout, hardened price regex), Walmart (recovered price/rating; dropped
  google_shopping's dead `urls`), and Yandex search (one row per organic block,
  so titles/links/snippets no longer drift out of alignment).
- Price parsing no longer lets a label before the price swallow it: a leading
  digit is required, so `From $19.99` / `Now 19.99` parse the number, not the
  label. Currency symbols, thousands separators (US and EU), leading decimals
  and negatives still parse as before.
- Browser: a `wait_for_selector` timeout is now classified (interstitial/blocked
  vs slow-but-fine) instead of treated uniformly.
- Sessions: the storage-state persistence gate reads the fetch outcome from the
  correct result path, so cookies from a captcha page (HTTP 200 but not genuine
  content) are no longer written into a shared session and inherited later.

## [0.1.5] - 2026-07-24

Patch release: a queue retry fix plus dependency security updates.

### Fixed

- The queue no longer retries a slow page as if the proxy were bad. A
  navigation (`goto`) timeout now gets at most one proxy rotation and otherwise
  returns a timeout to the caller instead of burning the retry budget. Genuine
  proxy failures (connection reset, tunnel/auth errors, `net::ERR_*`) still
  rotate and retry as before.

### Security

- Dependency bumps clearing the outstanding advisories in auxiliary tooling:
  `examples/` (`langchain-anthropic`) and the local `scraper-tester` dev harness
  (`express`, `qs`, `http-proxy-middleware`, `follow-redirects`). No shipped
  scraper runtime or API change.

## [0.1.4] - 2026-07-22

Patch release fixing headful (Xvfb) startup after a container restart.

### Fixed

- Headful scrapes could fail silently after a `docker restart`: a stale
  `/tmp/.X99-lock` (whose recorded PID a restart made live again) made X refuse
  the display and Xvfb exit, while the old readiness check — which only tested
  that the display socket existed — still reported success. Xvfb now starts with
  `-nolock`, readiness is a real liveness signal (`-displayfd`), and startup
  fails closed with a loud warning (leaving `DISPLAY` unset) instead of silently
  handing requests a dead display. Only affects headful (`HEADLESS=false`)
  deployments.

## [0.1.3] - 2026-07-21

Security-hardening release plus a per-request launch mode and extraction/preset
reliability fixes.

### Added

- Per-request launch mode: choose headless or headful per request (`headless`),
  headless by default. Non-default modes run in a throwaway browser that is
  never pooled.
- Extraction emits a `row_alignment_mismatch` warning when parallel arrays
  (titles/prices/urls) have mismatched lengths.

### Changed

- `SERVICE_TOKEN` now gates `/proxies/resolve` (CRIT-01) and the entire
  `/sessions` surface (CRIT-02): unauthenticated callers get 401, and the
  endpoints fail closed (503) when the token is unconfigured. New required env
  var — see `.env.example`.
- Proxy resolution fails closed: a request that explicitly asks for a proxy
  which cannot be resolved now errors instead of silently falling back to a
  direct connection that would leak the real server IP (HIGH-11).
- Search market is decoupled from the proxy exit country: the Google `us`
  market can be served through a GB exit, keeping the browser fingerprint
  aligned with the exit.

### Fixed

- The `/map` SSRF guard now blocks CGNAT (`100.64.0.0/10`) and IPv4-mapped IPv6
  private addresses in addition to the standard private/reserved ranges
  (CRIT-03 A).
- The crawler treats blocked / CAPTCHA / failed scraper pages as failures
  rather than successful visits, so they are retried and no longer pollute
  crawl results or dedup (HIGH-23).
- `amazon_search` / `google_search` presets repaired so extracted rows stay
  co-indexed per result.
- Yandex: wait for the real SERP past the browser-check interstitial instead of
  capturing the interstitial page.

## [0.1.2] - 2026-07-16

Anti-bot and preset maintenance release: a hardened browser fingerprint, an
optional real Google Chrome engine, optional headful rendering via Xvfb, a GB
default for rotating residential exits, and repaired extraction selectors. The
public HTTP API contract is unchanged.

### Added

- Optional real Google Chrome engine via `channel=chrome`: launches an actual
  Chrome build (real branding/codecs, populated `navigator.plugins`) instead of
  bundled Chromium. Chromium-family only; disabled by default.
- Optional headful mode via Xvfb (`HEADLESS=false`): runs a visible browser
  under a virtual display to defeat headless-detection tells, without a physical
  screen.

### Changed

- Hardened fingerprint alignment: viewport/screen, UA version, stealth getters,
  `navigator.platform` and proxy geo are kept mutually consistent so the browser
  no longer contradicts its spoofed platform.
- Default residential rotating exit country is now `GB` instead of `US`, keeping
  the exit geo aligned with the default browser locale/timezone. Override with
  `DEFAULT_PROXY_COUNTRY`.

### Fixed

- Repaired stale extraction selectors for `google_shopping`, `youtube`,
  `linkedin`, and eBay search (`su-card` layout).
- Swapped the dead Amazon example ASIN for a live one in the README and examples.

## [0.1.1] - 2026-07-13

Maintenance release: tighter anti-bot fingerprinting, premium-proxy routing for
SERP/marketplace presets, and crawler fixes. The public HTTP API contract is
unchanged.

### Changed

- Client Hints (`Sec-CH-UA*`) and WebGL vendor/renderer are now aligned with the
  rest of the browser fingerprint, so they no longer contradict the spoofed
  platform.
- SERP and marketplace presets route through the premium proxy by default;
  presets migrated from `res_rotating` to `prem_res_rotating` (`amazon_product`,
  `amazon_search`, `bing_search`, `ebay_search`, `google_search`,
  `google_shopping`, `linkedin_profile`, `walmart_product`, `youtube_video`).
- Premium proxy v2 hardening: `provider_v2`, the username builder and the
  resolver, with schema tightening in `proxy/base` and `schemas`.
- Tester UI surfaces the applied parameters and request payload alongside the
  proxy/warmup controls.

### Fixed

- Crawler dedup and scope fixes (`dedup`, `scope`, `engine`) and `/map`.

## [0.1.0] - 2026-07-09

First tagged public release. Yozh Scraper becomes a horizontally scalable,
queue-backed service with a Firefox/Camoufox anti-bot engine, WebRTC leak
protection, and a rebuilt CyberYozh proxy integration with premium rotating
pools. The public HTTP API contract is unchanged; deployment topology and env
vars changed (see Changed / Removed).

### Added

- Camoufox (hardened Firefox) browser engine alongside Chromium, selectable
  per request via `browser_engine` with Camoufox-specific fingerprint options
  and `applied_*` fingerprint read-back. Yandex SERP routes through Camoufox
  (with `yabs` tracking-link unwrapping) to get past SmartCaptcha.
- WebRTC leak protection (`webrtc_stealth.js`): keeps the WebRTC API
  native-looking instead of deleting it, so the real IP cannot leak around the
  proxy without leaving a detectable "WebRTC removed" fingerprint. Toggle with
  `WEBRTC_BLOCK`.
- CyberYozh proxy integration v2: rebuilt client/provider with a structured
  username builder, plus premium rotating residential/mobile proxies
  (`prem_res_rotating`) with a warm-up path (`WARMUP_DWELL_MS`) that pre-loads
  pages on a cold session before the real scrape.
- Per-request `max_retries` (configurable; default 3). Proxy rotates on a
  ban / transient HTTP status, not just on CAPTCHA.
- Redis-backed session `storage_state`: logged-in sessions persist across
  container restarts and are shared across workers.
- `GET /api/v1/queue/stats` — live stream depth, in-flight count and consumers.
- Sessions API: server-side `SessionRecord` with Playwright `storage_state`
  (cookies + localStorage + sessionStorage), populated via a declarative login
  DSL (`goto`, `fill`, `click`, `wait_for_selector`, `wait_for_timeout`,
  `press_key`, `type_text`, `hover`). Endpoints under `/api/v1/sessions` for
  create / login / inspect / cookie-inject / delete, and a `session_id`
  parameter on `POST /scrape/page`, `POST /scrape/pages` and the crawler's
  `scrape_options`. Credentials are inline-only, never persisted.
- MCP exposure for all session endpoints (`create_session`, `login_session`,
  `inject_session_cookies`, `get_session`, `get_session_storage_state`,
  `delete_session`, `list_sessions`).
- Tester UI: Sessions tab (create form, visual login-script builder, cookie
  injection panel) and a session dropdown on the Scrape / Batch / Crawler tabs
  that auto-applies the session's pinned device / proxy settings.
- Example `examples/login_session_scraping.py` and an end-to-end session test.

### Changed

- Durable job queue (taskiq + Redis): the in-process job queue and browser
  worker pool are replaced by a taskiq stream on Redis. The `web-scraper`
  container only enqueues page tasks and reads results; browsers run in
  separate, horizontally scalable `scraper-worker` containers
  (`docker compose up -d --scale scraper-worker=N`). Job state, results and
  sessions live in Redis, so the API no longer loses jobs on restart, and
  same-session scrapes are serialized across workers by a distributed lock.
  Operators must now run the `redis` and `scraper-worker` services (both are in
  `docker-compose.yml`).
- `POST /scrape/page` and `/scrape/pages` now return 503 `queue_full` when the
  Redis stream depth exceeds `QUEUE_MAXSIZE` (previously unbounded).
- Workers recycle their browser after `BROWSER_MAX_PAGES` and shut Chromium
  down after idle (`BROWSER_IDLE_SHUTDOWN_S`); a reaper re-enqueues tasks
  abandoned by a dead worker (`RECLAIM_IDLE_S`). Per-container memory limits
  added in compose.
- New env vars: `REDIS_URL`, `PAGE_TASK_TIMEOUT_S`, `LOGIN_TASK_TIMEOUT_S`,
  `LOGIN_RESULT_GRACE_S`, `RECLAIM_IDLE_S`, `JOB_RESULT_TTL_S`,
  `BROWSER_MAX_PAGES`, `BROWSER_IDLE_SHUTDOWN_S`, `WARMUP_DWELL_MS`.

### Removed

- Env vars `JOB_TIMEOUT_MS` (use `PAGE_TASK_TIMEOUT_S`), `JOB_RESULT_MAX`
  (eviction is now a native Redis TTL, `JOB_RESULT_TTL_S`) and `JOBS_ENABLED`
  (the queue is always on). They are ignored with a startup warning if set.
- The in-memory worker pool and jobs module (superseded by the Redis queue).

### Fixed

- Authenticated SOCKS5 proxies (CyberYozh residential / mobile) now work on the
  login path too, via a shared HTTP-to-SOCKS5 bridge (`resolve_proxy()`).
- `storageState.cookies[].expires` is dropped when Playwright emits
  `-1` / `NaN` / `inf`, instead of being written as `null` and failing the
  next scrape against the session.
- LoginRunner redacts substituted credential values from `result.error`, so a
  Playwright error can no longer echo the resolved password to the client.
- Preset & SERP fixes: Yandex `lr` localisation, stealth disabled on
  `google_shopping` to stop the CAPTCHA, `walmart_product` / `youtube_video`
  fixed for available proxies, `/showcaptcha` SmartCaptcha detection, and a
  time-bounded map seed render so a stall cannot block `/map`.
