# Open Crawler

A companion service to [`open-scraper`](../src/README.md) that walks a site from
a seed URL and streams discovered pages over SSE. The crawler handles
*discovery* — frontier, dedup, scope, link extraction, retries, politeness —
while every page fetch still goes through the scraper over HTTP. No Playwright
duplication.

**Base URL:** `http://localhost:8001`
**OpenAPI docs:** `http://localhost:8001/docs`
**MCP endpoint:** `http://localhost:8001/mcp`

## Features

- **Scope predicates**: `same-domain` / `subdomains` / `all` / `regex` with
  include & exclude patterns, plus `max_depth` and `max_pages` caps.
  `same-domain` treats `www.X` and `X` as the same site (www-canonical sites
  301 one spelling to the other); the alias pair shares one rate-limit budget
  and one dedup identity, so a page listed under both spellings is fetched
  once.
- **URL canonicalization + fingerprint dedup** (SHA1 over normalized URL) so
  different query orderings / trailing-slash variants collapse to one visit.
- **Per-domain round-robin frontier** — one slow host can't starve others.
- **Global per-domain rate limiter** (token bucket with jitter) shared across
  concurrent crawl jobs; adaptive throttle halves RPS on 429 for a cool-off
  window.
- **Session health** à la Crawlee: `401/403/429` → retire, `5xx`/timeout → +1
  to error score, success → −0.5, rotate on `error_score ≥ 3` or after 50 uses.
- **Retry**: 3 attempts on `[408, 429, 500, 502, 503, 504]` + connection
  errors, exponential backoff with jitter (2s → 30s max).
- **Two proxy configs per job**: `crawl_proxy` is used when
  `enable_scraping=false` (cheap discovery-only mode), `scrape_options.proxy_*`
  is used when `enable_scraping=true` (full-payload mode). No cross-pollination.
- **In-memory job store** — matches the scraper's design; partial results are
  queryable while the crawl is running.
- **SSE streaming** — every job exposes `GET /api/v1/crawl/{id}/events`
  emitting `stats` (periodic progress), `page` (each visited URL with optional
  `ScrapeResponse`), `page_error`, and terminal `done` / `cancelled`.
- **Graceful cancel**: `DELETE /api/v1/crawl/{id}` stops scheduling new work,
  in-flight requests drain; `?hard=true` cancels asyncio tasks.

## Quick start

Wired into the root [`docker-compose.yml`](../docker-compose.yml) — brought up
together with the scraper:

```bash
docker compose up --build
# scraper  → http://localhost:8000
# crawler  → http://localhost:8001
```

```bash
# Submit a crawl
curl -X POST http://localhost:8001/api/v1/crawl \
  -H "Content-Type: application/json" \
  -d '{
    "seed_url": "https://example.com",
    "scope": {"mode": "same-domain", "max_depth": 2, "max_pages": 50,
              "per_domain_rps": 1.0, "per_domain_concurrency": 1},
    "scrape_options": {"proxy_type": "none"},
    "crawl_proxy": null,
    "enable_scraping": false
  }'
# {"job_id":"crawl_..."}

# Poll status
curl http://localhost:8001/api/v1/crawl/crawl_abc123

# Full results (all visited pages + stats)
curl http://localhost:8001/api/v1/crawl/crawl_abc123/results

# Live stream (SSE)
curl -N http://localhost:8001/api/v1/crawl/crawl_abc123/events

# Cancel (soft — drains in-flight)
curl -X DELETE "http://localhost:8001/api/v1/crawl/crawl_abc123?hard=false"

# Cancel (hard — aborts in-flight tasks immediately)
curl -X DELETE "http://localhost:8001/api/v1/crawl/crawl_abc123?hard=true"
```

## enable_scraping — the single toggle

- `enable_scraping=false` — lightweight pass: scraper is still invoked per page
  (needed for JS-rendered HTML to extract links) but the crawler keeps only
  `url / parent_url / depth / status / took_ms`. Uses `crawl_proxy`.
- `enable_scraping=true` — every page is kept with its full `ScrapeResponse`
  (raw_html / screenshot / `data` from extract rules). Uses
  `scrape_options.proxy_*`.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/crawl` | Create a job. Returns `{"job_id":"..."}`. |
| `GET`  | `/api/v1/crawl/{id}` | Job record (status, stats, pages so far). |
| `GET`  | `/api/v1/crawl/{id}/results` | Same as above — alias for symmetry with the scraper. |
| `GET`  | `/api/v1/crawl/{id}/events` | SSE stream of events (`stats`/`page`/`page_error`/`done`/`cancelled`). |
| `DELETE` | `/api/v1/crawl/{id}?hard=bool` | Cancel the job. |
| `POST` | `/api/v1/map` | **Fast URL discovery** (sitemap + seed links). Synchronous; returns a URL list. |
| `GET`  | `/api/v1/health` | Health + scraper reachability. |

OpenAPI schema with all request/response models: `http://localhost:8001/docs`.

### `/map` — fast URL discovery

`/crawl` visits and processes every page (recursive, job-based, slow). `/map`
just answers *"which URLs exist on this site"* in one quick **synchronous**
call: it reads robots.txt + sitemap.xml (following `<sitemapindex>`, bounded)
and the links on a single seed-page fetch, then scope-filters, canonicalizes
and dedupes. No per-page rendering, no recursion. Typical flow: `/map` to
discover → pick URLs → feed them to `/scrape` or `/crawl`.

```bash
curl -s localhost:8001/api/v1/map \
  -H 'content-type: application/json' \
  -d '{"seed_url": "https://example.com", "search": "/blog/", "sort": "newest", "limit": 100}'
# -> {"seed_url","count","urls":[...],"lastmod":{url:"<lastmod>"},
#     "stats":{from_sitemap,from_page,unique_in_scope,with_lastmod},"took_ms","warnings"}
```

Request fields: `seed_url` (required), `scope` (reuses the crawl scope —
`mode` + include/exclude patterns; depth/rps ignored), `include_sitemap`,
`include_page_links`, `render` (fetch the seed via the scraper for SPA links),
`search` (case-insensitive substring filter — also applied *during* sitemap
collection, so the `MAP_MAX_URLS` cap counts matches rather than starving on a
huge sitemap), `limit`, and proxy:
`proxy_type` / `proxy_pool_id` / `proxy_geo` (default `none`). When a proxy is
set the scraper resolves the upstream URL (`GET /api/v1/proxies/resolve`, reusing
its CyberYozh integration) and the crawler routes the robots/sitemap/seed
fetches through it — useful for sites that block datacenter IPs or geo-restrict.

**Recency filter / sort** (sitemap `<lastmod>`): `published_after` (`YYYY-MM-DD`,
keep URLs modified on/after that date), `recent_days` (shorthand: within the last
N days; ignored if `published_after` is set), and `sort: "newest"` (order by
`<lastmod>` descending, undated URLs last). Only sitemap-sourced URLs carry a
date — `<a>`-link URLs and entries without `<lastmod>` are dropped by a date
filter and sort last. The returned `lastmod` map echoes the raw `<lastmod>` for
each returned URL that has one; if a date filter is requested but the sitemap
exposes no dates, that's surfaced in `warnings`.

Direct (no-proxy) fetches pass through an SSRF guard that refuses private /
loopback / link-local / metadata addresses and re-validates every redirect hop;
when proxied, egress is via the proxy so that guard is skipped.

## Configuration (env)

| Var | Default | Notes |
|---|---|---|
| `SCRAPER_URL` | `http://web-scraper:8000` | Upstream scraper (use docker service name inside the compose network). |
| `WORKERS` | `2` | Number of parallel crawl jobs. |
| `QUEUE_MAXSIZE` | `200` | Pending-jobs queue depth. |
| `JOB_TIMEOUT_MS` | `3_600_000` | Wall-clock cap on one crawl job. |
| `SCRAPER_JOB_POLL_INTERVAL_MS` | `250` | Poll cadence against the scraper's job endpoint. |
| `SCRAPER_JOB_TIMEOUT_MS` | `120_000` | Per-page scraper request timeout. |
| `MAX_RETRIES` | `3` | Retries per Request on transient failure. |
| `RETRY_HTTP_CODES` | `[408,429,500,502,503,504]` | Retry on these codes. |
| `RETRY_BACKOFF_BASE` | `2.0` | `min(BASE^attempt, MAX)` seconds + jitter. |
| `RETRY_BACKOFF_MAX` | `30.0` | Upper bound for the backoff. |
| `SESSION_MAX_ERROR_SCORE` | `3.0` | Session retire threshold. |
| `SESSION_MAX_USAGE` | `50` | Rotate a session after N uses. |
| `SESSION_BLOCKED_CODES` | `[401,403,429]` | Codes that retire the session instantly. |
| `CORS_ALLOW_ORIGINS` | `["*"]` | Crawler is reached directly from the browser (SSE). |
| `MAP_HTTP_TIMEOUT_MS` | `10_000` | Per-request timeout for `/map` robots/sitemap/seed fetches. |
| `MAP_MAX_URLS` | `5_000` | Cap on URLs collected from sitemaps in `/map`. |
| `MAP_MAX_SITEMAPS` | `50` | Cap on sitemap documents fetched per `/map` call. |

See [`.env.example`](.env.example) for the full list.

## MCP

`fastapi-mcp` is mounted at `/mcp`. The Streamable HTTP endpoint exposes:

- `health`
- `create_crawl` (full JSON-schema for `CrawlRequest`)
- `get_crawl` / `get_crawl_results`
- `cancel_crawl`
- `create_map` (fast URL discovery)

The SSE `stream_crawl_events` endpoint is deliberately excluded — streaming
responses don't translate well to a request/response MCP tool.

Quick check:

```bash
curl -X POST http://localhost:8001/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2024-11-05","capabilities":{},
                 "clientInfo":{"name":"test","version":"1"}}}'
```

The Web tester under [`../scraper-tester/`](../scraper-tester/README.md) has an
MCP tab with a target dropdown (Scraper / Crawler) for interactive tool calls.

## Architecture

```
yozh-crawler/src/
├── app.py            # FastAPI factory + lifespan (ScraperClient, JobStore, JobRunner)
├── main.py           # uvicorn entry
├── settings.py       # Pydantic BaseSettings (env)
├── schemas.py        # CrawlRequest / CrawlJobRecord / SSE event models
├── jobs.py           # JobStore (pub/sub) + JobRunner (N asyncio workers)
├── engine.py         # CrawlEngine per job — main loop
├── frontier.py       # In-memory priority queue with per-domain round-robin
├── dedup.py          # canonicalize_url + SHA1 fingerprint + DedupSet
├── scope.py          # CompositeScope (same-domain / subdomains / all / regex)
├── limiter.py        # Global DomainRateLimiter (token bucket + jitter)
├── session.py        # SessionPool (Crawlee-inspired health tracking)
├── linkextract.py    # lxml-based <a href> extractor
├── fetcher.py        # ScraperClient — httpx to POST /scrape/page + poll
└── api/
    ├── crawl.py      # POST/GET/DELETE + SSE
    └── health.py
```

Fetch flow per page:
1. Worker pops `Request` from `Frontier` (per-domain round-robin).
2. `DomainRateLimiter.acquire(domain)` — token bucket gate.
3. `SessionPool.acquire()` yields a session with current proxy identity + health.
4. `ScraperClient.fetch(url, scrape_options)` — POST to scraper, poll until
   done, return the `ScrapeResponse` dict.
5. Session is released with ok/status_code → health score updated, session
   retired on `401/403/429`.
6. `extract_links(raw_html)` → canonicalize → dedup → scope check → push to
   frontier.
7. Page record emitted as SSE `page` event (full payload when
   `enable_scraping=true`, thin metadata otherwise).

## Running locally without Docker

```bash
cd yozh-crawler
pip install -r requirements.txt
cp .env.example .env         # edit SCRAPER_URL if your scraper is elsewhere
python -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8001
```

## Tests

```bash
pip install pytest
pytest -q
```

Unit tests cover pure modules: `dedup`, `scope`, `frontier`, `linkextract`.
Engine / jobs / fetcher are integration-tested via docker-compose runs; no
network mocks in the test suite.

## Known limitations (v1)

- **No authentication** on any endpoint. Designed for internal / trusted
  networks. Do **not** expose the crawler directly to the public internet —
  anyone reachable can POST a crawl with an arbitrary seed URL and turn the
  service into an SSRF proxy.
- **No persistence** — jobs live in memory, reset on container restart
  (symmetric with the scraper). `JobStore` accumulates all finished jobs too;
  a long-lived process should be restarted periodically.
- **Authenticated crawls.** Pass `scrape_options.session_id` referencing a
  session created via the scraper's `POST /sessions` +
  `POST /sessions/{id}/login`. The scraper round-trips cookies + localStorage
  per request, so every page in the crawl sees the authenticated state. See
  [`../src/README.md`](../src/README.md#sessions) for the session lifecycle
  and DSL.
- **Naive `subdomains` scope** — uses "last two labels" as the registrable
  domain. Over-matches on Public Suffix List hosts like `github.io` / `co.uk`.
  Use `same-domain` or `regex` for those.
- **robots.txt is not consulted.** The crawler walks every in-scope URL
  regardless of `/robots.txt`.
- **Scraper-side job orphaning on hard cancel** — the crawler's `?hard=true`
  drops its in-flight httpx request, but the scraper has no way to learn this
  and its page render continues to completion on the scraper side.
