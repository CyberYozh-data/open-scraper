# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Sessions API (Phase 1)**: server-side `SessionRecord` with Playwright
  `storage_state` (cookies + localStorage + sessionStorage), populated via a
  declarative login DSL (`goto`, `fill`, `click`, `wait_for_selector`,
  `wait_for_timeout`, `press_key`, `type_text`, `hover`). New endpoints under
  `/api/v1/sessions` for create / login / inspect / cookie-inject / delete.
  New `session_id` parameter on `POST /scrape/page`, `POST /scrape/pages`, and
  the crawler's `scrape_options`. Credentials are inline-only, never persisted.
  In-memory store with 24 h TTL, LRU eviction, and 2 MB storage_state cap.
- Tester UI: new **Sessions** tab with create form, visual step-row login
  script builder, and session dropdown on Scrape Page / Batch / Crawler tabs.
- MCP exposure for all session endpoints (`create_session`, `login_session`,
  `inject_session_cookies`, `get_session`, `get_session_storage_state`,
  `delete_session`, `list_sessions`).
- Example: `examples/login_session_scraping.py` (end-to-end against
  `the-internet.herokuapp.com/login`).
- End-to-end integration test `tests/test_e2e_session.py` (skipped when
  Playwright / `playwright_stealth` not installed locally).
- **Inject Cookies** panel in the Sessions tab — paste a Cookie-Editor /
  EditThisCookie JSON export, the tester converts `expirationDate` (float)
  to `expires` (int), normalizes `sameSite` casing, drops extension-only
  fields, and POSTs to `/api/v1/sessions/{id}/cookies`. Escape hatch for
  CAPTCHA / 2FA / MFA flows that the Phase 1 declarative DSL cannot solve.
- Picking a session on the Scrape / Batch / Crawler tabs auto-applies the
  session's pinned `device`, `proxy_type`, `proxy_pool_id`, and `proxy_geo`
  to the form, with a confirm dialog when current values would be overwritten.
- Sessions tab Pool ID is populated from `/api/v1/proxies/available` (same
  pattern as the Scrape / Batch / Crawler tabs), instead of asking the user
  to type a UUID by hand.
- Login-failure screenshots render inline as an `<img>` in the Run Login
  Script result panel rather than disappearing into a 50KB base64 blob.

### Fixed (post-PR-open follow-ups)

- `Browser.new_context: Browser does not support socks5 proxy authentication`
  on `POST /sessions/{id}/login` when the session was pinned to an
  authenticated SOCKS5 proxy (CyberYozh residential / mobile). Extracted the
  HTTP-to-SOCKS5 bridge from `runner.fetch` into a shared
  `PlaywrightRunner.resolve_proxy()` helper that the login worker now uses.
  Any proxy type that scrape supports also works for login.
- `Browser.new_context: storageState.cookies[].expires: expected float, got
  object` on the second scrape against a session. Drop the `expires` key when
  Playwright emits `-1` / `NaN` / `inf`, instead of writing it as `null`.
- `docker-compose.override.yml` was mounting `./open-crawler/src`; renamed to
  `./yozh-crawler/src` so `docker compose up` works after the folder rename.
- LoginRunner redacts substituted credential values from `result.error`
  before returning — Playwright errors that echo a fill-value can no longer
  leak the resolved password back to the API client.
- Tester UI: credential inputs (`sess-creds-*`) skipped from `localStorage`
  persistence and cleared on successful login (an earlier review caught the
  leak); session status badge compares to `'ready'` not the never-emitted
  `'logged_in'` so the green badge actually shows.

### Out of scope (Phase 2 / 3)

- Automatic re-login on 401 / cookie expiry.
- Credential vault with at-rest encryption.
- CAPTCHA solver integration; MFA (TOTP, SMS, email).
- Session persistence across container restarts.
- IndexedDB / Service Worker state capture (Playwright limitation).
