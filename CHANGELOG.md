# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
