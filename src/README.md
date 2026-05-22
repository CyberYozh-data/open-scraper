# Yozh Scraper — Service Docs

Web-scraping API built on Playwright. Renders any URL in a real browser and
returns structured data (CSS/XPath extraction), raw HTML, and/or a full-page
screenshot. Async job queue for single-page and batch scrapes, device
emulation, rich proxy integration via CyberYozh.

**Base URL:** `http://localhost:8000`
**OpenAPI docs:** `http://localhost:8000/docs`
**MCP endpoint:** `http://localhost:8000/mcp`

For the one-command start-up see the [root README](../README.md).

## Proxy Support

**For reliable web scraping, using proxies is essential.** Most modern websites
(especially search engines, e-commerce, social media) have anti-bot protection
that blocks direct scraping attempts. Proxies help you:

- Avoid IP bans and CAPTCHAs
- Bypass geo-restrictions
- Scale scraping operations
- Appear as real users from different locations

### CyberYozh Proxy Integration

This scraper integrates with **CyberYozh Proxy Service** which provides
residential, mobile (LTE), and datacenter proxies.

- **Get your API key:** https://app.cyberyozh.com/api-access/
- **Proxy documentation:** https://apidoc.cyberyozh.com/

Set `CYBERYOZH_API_KEY` in `.env` to enable proxy support.

### Available proxy types

- `res_rotating` — Residential rotating (recommended for most scraping)
- `res_static` — Residential static (dedicated)
- `mobile` — Mobile / LTE, dedicated
- `mobile_shared` — Mobile / LTE, shared
- `dc_static` — Datacenter static
- `none` — No proxy (direct connection)

### Proxy discovery endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/proxies/available?proxy_type=...` | Lists the current user's purchased proxies of a given type (filtered by `access_type` for shared vs dedicated) |
| `GET /api/v1/proxies/countries` | Lists the ~250 countries supported by CyberYozh's rotating residential proxies (mirrors the CyberYozh static list) |

### Proxy type → CyberYozh mapping

`proxy_type` is mapped to the CyberYozh `category` used against
`GET /api/v1/proxies/history/` and filtered by `access_type`:

| `proxy_type`     | CyberYozh category     | `access_type` filter |
|------------------|------------------------|----------------------|
| `res_rotating`   | `residential_rotating` | — (single tier)      |
| `res_static`     | `residential_static`   | `private`            |
| `mobile`         | `lte`                  | `private`            |
| `mobile_shared`  | `lte`                  | `shared`             |
| `dc_static`      | `datacenter`           | `private`            |

`res_rotating` additionally calls `POST /api/v1/proxies/rotating-credentials/`
to obtain per-session credentials (optionally with `proxy_geo`).
The mapping lives in [`proxy/cyberyozh/provider.py`](proxy/cyberyozh/provider.py).

## API

### Scrape (async jobs)

Every scrape endpoint creates a background job and returns `job_id`. Poll job
status, then fetch results.

Routes:
* `POST   /api/v1/scrape/page`
* `POST   /api/v1/scrape/pages`
* `GET    /api/v1/scrape/{job_id}`
* `GET    /api/v1/scrape/{job_id}/results`
* `DELETE /api/v1/scrape/{job_id}`  — soft-cancel. In-flight pages finish;
  remaining pages are skipped with `warnings: ["cancelled"]`; job transitions
  to `status="cancelled"`.

---

### 1) Scrape single page (no proxy)

Create job:

```bash
curl -s -X POST http://localhost:8000/api/v1/scrape/page \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "proxy_type": "none"
  }'
```

Example response:

```json
{"job_id":"req_..."}
```

Check status:

```bash
curl -s http://localhost:8000/api/v1/scrape/req_...
```

Fetch results (available for `done`, `failed`, and `cancelled` jobs):

```bash
curl -s http://localhost:8000/api/v1/scrape/req_.../results
```

Example response:

```json
{
  "job_id": "req_...",
  "status": "done",
  "total": 1,
  "done": 1,
  "pages": [{"url": "https://example.com", "proxy_type": "none", "...": "..."}],
  "error": null,
  "results": [
    {
      "request_id": "req_...",
      "took_ms": 1234,
      "meta": {
        "url": "https://example.com",
        "final_url": "https://example.com/",
        "status_code": 200,
        "device": "desktop",
        "proxy_type": "none",
        "retries": 0
      },
      "data": null,
      "raw_html": null,
      "screenshot_base64": null,
      "warnings": []
    }
  ]
}
```

For a failed job `results` is `null` and `error` contains the reason.

---

### 2) One-liner: create job → wait → print results (requires jq)

```bash
JOB_ID=$(
  curl -s -X POST http://localhost:8000/api/v1/scrape/page \
    -H "Content-Type: application/json" \
    -d '{"url":"https://example.com","proxy_type":"none"}' \
  | jq -r .job_id
)

echo "job_id=$JOB_ID"

while true; do
  STATUS=$(curl -s "http://localhost:8000/api/v1/scrape/$JOB_ID" | jq -r .status)
  echo "status=$STATUS"
  if [ "$STATUS" = "done" ]; then
    curl -s "http://localhost:8000/api/v1/scrape/$JOB_ID/results" | jq .
    break
  fi
  if [ "$STATUS" = "failed" ] || [ "$STATUS" = "cancelled" ]; then
    curl -s "http://localhost:8000/api/v1/scrape/$JOB_ID" | jq .
    exit 1
  fi
  sleep 0.5
done
```

---

### 3) Batch scrape (multiple pages)

```bash
curl -s -X POST http://localhost:8000/api/v1/scrape/pages \
  -H "Content-Type: application/json" \
  -d '{
    "pages": [
      {"url":"https://example.com","proxy_type":"none"},
      {"url":"https://example.org","proxy_type":"none"}
    ]
  }'
```

Poll status + fetch results via the same `GET /api/v1/scrape/{job_id}` and `/results`.

---

### 4) Extract data with CSS

```bash
curl -s -X POST http://localhost:8000/api/v1/scrape/page \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "proxy_type": "none",
    "extract": {
      "type": "css",
      "fields": {
        "title": {"selector": "h1", "attr": "text", "required": true}
      }
    }
  }'
```

Result payload contains:

```json
{ "data": { "title": "Example Domain" } }
```

---

### 5) Extract data with XPath

```bash
curl -s -X POST http://localhost:8000/api/v1/scrape/page \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "proxy_type": "none",
    "extract": {
      "type": "xpath",
      "fields": {
        "title": {"selector": "//h1", "attr": "text", "required": true}
      }
    }
  }'
```

---

### 6) Return raw HTML

```bash
curl -s -X POST http://localhost:8000/api/v1/scrape/page \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "proxy_type": "none",
    "raw_html": true
  }'
```

---

### 7) Screenshot (base64)

```bash
curl -s -X POST http://localhost:8000/api/v1/scrape/page \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "proxy_type": "none",
    "screenshot": true
  }'
```

---

### 8) Device emulation (mobile)

```bash
curl -s -X POST http://localhost:8000/api/v1/scrape/page \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "proxy_type": "none",
    "device": "mobile",
    "extract": {
      "type": "css",
      "fields": {"title": {"selector": "h1", "attr": "text"}}
    }
  }'
```

---

### 9) Custom headers

```bash
curl -s -X POST http://localhost:8000/api/v1/scrape/page \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://httpbin.org/headers",
    "proxy_type": "none",
    "headers": {
      "User-Agent": "open-scraper/1.0",
      "Accept-Language": "en-US,en;q=0.9"
    },
    "raw_html": true
  }'
```

---

### 10) Cookies

```bash
curl -s -X POST http://localhost:8000/api/v1/scrape/page \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "proxy_type": "none",
    "cookies": [
      {"name":"session","value":"abc123","domain":"example.com","path":"/"}
    ],
    "raw_html": true
  }'
```

---

### 11) Wait strategy / selector / timeout

```bash
curl -s -X POST http://localhost:8000/api/v1/scrape/page \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "proxy_type": "none",
    "wait_until": "networkidle",
    "wait_for_selector": "h1",
    "timeout_ms": 45000
  }'
```

---

### 12) Proxies (CyberYozh)

Set `CYBERYOZH_API_KEY` in `.env`, then:

```bash
curl -s -X POST http://localhost:8000/api/v1/scrape/page \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://httpbin.org/ip",
    "proxy_type": "res_static"
  }'
```

---

### 13) Proxies (CyberYozh) with GEO targeting

`proxy_geo` accepts:

| Field          | Type   | Description                        |
|----------------|--------|------------------------------------|
| `country_code` | string | ISO 3166-1 alpha-2 (e.g. `"US"`, `"GB"`) |
| `region`       | string | Region / state name                |
| `city`         | string | City name (e.g. `"London"`)        |

GEO targeting at the **proxy** level is supported only for `res_rotating`. For
`res_static`, `mobile`, `mobile_shared` and `dc_static` the proxy already has
a fixed location; `proxy_geo` in those requests is accepted but does not
change the exit IP (a warning is logged).

Regardless of the proxy type, when `proxy_geo.country_code` is provided the
browser context is automatically aligned with that country — `locale`,
`timezone_id` and `Accept-Language` are set accordingly so the fingerprint
matches the IP. For US, CA, RU, AU and BR a `city` hint further refines the
timezone.

#### Country only

```bash
curl -s -X POST http://localhost:8000/api/v1/scrape/page \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://httpbin.org/ip",
    "proxy_type": "res_rotating",
    "proxy_geo": {"country_code": "US"}
  }'
```

#### 14) Proxies (CyberYozh) with country and city + verification

Request through a London residential proxy and verify the exit IP is actually
in London:

```bash
# Step 1 – create job
JOB_ID=$(
  curl -s -X POST http://localhost:8000/api/v1/scrape/page \
    -H "Content-Type: application/json" \
    -d '{
      "url": "https://httpbin.org/ip",
      "proxy_type": "res_rotating",
      "proxy_geo": {"country_code": "GB", "city": "London"},
      "raw_html": true
    }' \
  | jq -r .job_id
)

# Step 2 – wait for completion
while true; do
  STATUS=$(curl -s "http://localhost:8000/api/v1/scrape/$JOB_ID" | jq -r .status)
  [ "$STATUS" = "done" ] && break
  [ "$STATUS" = "failed" ] && echo "failed" && exit 1
  sleep 0.5
done

# Step 3 – extract IP from result
IP=$(
  curl -s "http://localhost:8000/api/v1/scrape/$JOB_ID/results" \
  | jq -r '.results[0].raw_html' \
  | python3 -c "
import sys, re, json
html = sys.stdin.read()
m = re.search(r'<pre[^>]*>(.*?)</pre>', html, re.DOTALL)
data = json.loads(m.group(1) if m else html)
print(data['origin'].split(',')[0].strip())
"
)
echo "Proxy IP: $IP"

# Step 4 – verify geolocation via ipapi.co
curl -s "https://ipapi.co/$IP/json/" | jq '{ip, city, country_code}'
```

Expected output:

```json
{"ip": "185.x.x.x", "city": "London", "country_code": "GB"}
```

For a ready-to-run Python script that tests multiple cities see
[`../examples/geo_scraping.py`](../examples/geo_scraping.py):

```bash
cd ../examples
python geo_scraping.py
```

## Request Schema

### POST /api/v1/scrape/page

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | required | Target URL |
| `render` | boolean | `true` | Run full JS rendering in browser |
| `wait_until` | `domcontentloaded` \| `networkidle` | `domcontentloaded` | When to consider page loaded |
| `wait_for_selector` | string | — | Wait for CSS selector before extracting |
| `timeout_ms` | integer | — | Per-page timeout (ms), overrides global |
| `device` | `desktop` \| `mobile` | `desktop` | Device emulation |
| `headers` | object | — | Custom HTTP headers |
| `cookies` | array | — | Cookies to inject |
| `proxy_type` | string | `none` | See proxy types above |
| `proxy_pool_id` | string | — | Pin request to a specific purchased proxy id (required when `proxy_type != none` from the tester UI) |
| `proxy_geo` | object | — | `{ country_code, region, city }` — targets the exit IP for `res_rotating`; also drives browser locale/timezone alignment for every type |
| `block_assets` | boolean | env | Block images/fonts/media to speed up load; falls back to the `BLOCK_ASSETS` env var when unset |
| `raw_html` | boolean | `false` | Include raw HTML in response |
| `screenshot` | boolean | `false` | Include full-page screenshot as base64 (triggers a scroll pass for lazy images when assets are not blocked) |
| `stealth` | boolean | `true` | Apply `playwright-stealth` patches (navigator.webdriver, WebGL, Canvas, etc.) |
| `extract` | object | — | Structured extraction rules |

### ExtractRule

```json
{
  "type": "css",
  "fields": {
    "title": {
      "selector": "h1",
      "attr": "text",
      "all": false,
      "required": false
    }
  }
}
```

| Field | Values | Description |
|-------|--------|-------------|
| `type` | `css` \| `xpath` | Selector type |
| `selector` | string | CSS selector or XPath expression |
| `attr` | `text` (default) \| `html` \| attribute name | What to extract |
| `all` | boolean | Return all matches as array instead of first match |
| `required` | boolean | Log warning if field not found |

### Response

Each result in `results[]` contains:

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Job ID |
| `took_ms` | integer | Render time in milliseconds |
| `meta.url` | string | Original URL |
| `meta.final_url` | string | Final URL after redirects |
| `meta.status_code` | integer | HTTP status code |
| `meta.device` | string | Device used |
| `meta.proxy_type` | string | Proxy used |
| `meta.proxy_pool_id` | string | Specific proxy id used (if any) |
| `meta.retries` | integer | Number of retries |
| `meta.applied_user_agent` | string | UA that was actually sent |
| `meta.applied_locale` | string | Browser locale (`es-ES`, `en-US`, …) |
| `meta.applied_timezone` | string | Browser timezone id |
| `meta.applied_accept_language` | string | Effective `Accept-Language` |
| `data` | object | Extracted fields (if `extract` was set) |
| `raw_html` | string | Raw HTML (if `raw_html: true`) |
| `screenshot_base64` | string | Base64 PNG (if `screenshot: true`) |
| `warnings` | array | Non-fatal warnings (`["cancelled"]` for pages skipped by a cancel) |

## Presets

A **preset** is a named bundle of three things so you don't hand-assemble a
`ScrapeRequest` per target site: a request profile (proxy/device/wait), a
per-locale URL template, and a parsing recipe (deterministic CSS/XPath with
optional LLM self-heal / AI extraction). Built-ins ship read-only;
user-defined presets persist under `data/presets/` (mount the volume — see
`docker-compose.yml`).

### Built-in presets

| name | source | params | locales |
|------|--------|--------|---------|
| `amazon_product` | amazon | `asin` | us, uk, de, fr, jp |
| `amazon_search` | amazon | `query` | us, uk, de |
| `google_search` | google | `query` | us, uk, de, fr, ru, jp |
| `google_shopping` | google | `query` | us, uk, de |
| `ebay_search` | ebay | `query` | us, uk, de |
| `walmart_product` | walmart | `product_id` | us |
| `youtube_video` | youtube | `video_id` | global |
| `linkedin_profile` | linkedin | `username` | global (needs auth session) |

### Scrape with a preset

```bash
curl -X POST http://localhost:8000/api/v1/scrape/preset/page \
  -H 'Content-Type: application/json' \
  -d '{
    "source": "amazon_product",
    "preset_params": {"asin": "B08N5WRWNW"},
    "locale": "us",
    "llm": {"model": "openai/gpt-5.4-mini"}
  }'
# -> {"job_id": "..."}  then GET /api/v1/scrape/<job_id>/results
```

`llm` is optional. Without it the deterministic parser runs alone. With it,
self-heal regenerates selectors when markup drifts — but only when a
**required** field comes back empty (that's the trigger). Every shipped
built-in marks its schema-critical field required, so a drift that empties
it engages self-heal; non-required fields just return null. Healed
selectors are persisted back for *user* presets (built-ins are read-only,
regenerated per-request). Batch: `POST /api/v1/scrape/preset/pages` with
`{"pages": [<PresetScrapeRequest>...]}`.

`preset_meta`/`parser_plan` are materializer-internal — setting them on the
raw `/api/v1/scrape/page` is rejected with 422.

### Preset CRUD & generation

| Endpoint | Purpose |
|----------|---------|
| `GET /api/v1/presets?kind=&source=` | List built-in + user presets |
| `GET /api/v1/presets/{name}` | Full preset |
| `POST /api/v1/presets` | Create a user preset (name must start `user_`) |
| `PUT /api/v1/presets/{name}` | Replace a user preset |
| `DELETE /api/v1/presets/{name}` | Delete a user preset |
| `POST /api/v1/presets/generate` | `manual` \| `from_schema` \| `from_prompt` |
| `POST /api/v1/presets/{name}/test` | Dry-run on `sample_url` or `sample_html` |
| `GET /api/v1/presets/llm-models` | Models the configured keys can call |

`generate` modes:

```bash
# AI builds selectors from a schema you supply
curl -X POST .../api/v1/presets/generate -d '{
  "name":"user_books","mode":"from_schema","source":"custom",
  "sample_url":"https://books.toscrape.com/",
  "schema":{"title":"string","price":"number"}}'

# AI infers the schema from a natural-language description, then selectors
curl -X POST .../api/v1/presets/generate -d '{
  "name":"user_books","mode":"from_prompt","source":"custom",
  "sample_url":"https://books.toscrape.com/",
  "description":"list each book title and price"}'
```

LLM keys are server-side in `.env` (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`, `OPENROUTER_API_KEY`, or a custom OpenAI-compatible
endpoint via `CUSTOM_LLM_BASE_URL`/`CUSTOM_LLM_API_KEY`). Callers pick only
the model string; the default is `DEFAULT_LLM_MODEL` (`openai/gpt-5.4-mini`).
`sample_url` fetches are SSRF-guarded (no private/loopback/metadata targets).

### post_process

`FieldRule.post_process` is an ordered transform chain applied to matched
values (per-item when `all: true`): `regex` (args `[pattern, group?]`),
`parse_int`, `parse_float`, `parse_price` (args `["us"|"eu"]` for separator
disambiguation), `strip`, `lowercase`, `uppercase`, `replace` (args
`[old, new]`). A field may also override the rule's `type` (css/xpath).

## MCP Integration

Yozh Scraper exposes all its API tools via the
[Model Context Protocol](https://modelcontextprotocol.io/) at `/mcp`. This
lets AI assistants use the scraper directly as a tool — no extra code
required.

### Available MCP tools

| Tool | Description |
|------|-------------|
| `run_scrape_page` | Scrape a single page, returns `job_id` |
| `run_scrape_pages` | Scrape multiple pages in batch, returns `job_id` |
| `get_job_status` | Poll job status by `job_id` |
| `get_job_result` | Fetch results for a completed job |
| `cancel_scrape_job` | Soft-cancel a running job |
| `health` | Health check |

### Claude Code

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "yozh-scraper": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Restart Claude Code — scraper tools appear automatically. Then just ask in
chat:

```
Scrape https://example.com and tell me what's on the page
```

### LangChain Agent

```bash
pip install langchain-anthropic langchain-mcp-adapters langgraph
export ANTHROPIC_API_KEY=sk-ant-...
python ../examples/agent.py
```

See [`../examples/agent.py`](../examples/agent.py) — a chat agent that handles
the full job lifecycle: submit → poll → fetch → summarize.

### MCP Inspector (debug UI)

```bash
npx @modelcontextprotocol/inspector http://localhost:8000/mcp
# Opens at http://localhost:6274
```

## Sessions

Server-managed sessions let you authenticate once, then reuse cookies +
localStorage across subsequent scrape requests. Lifetime is in-memory only —
sessions evaporate on process restart.

### Lifecycle

1. `POST /api/v1/sessions` — create. Body pins `device`, `proxy_type`,
   `proxy_pool_id`, `proxy_geo`, `ttl_seconds` (default 86400, clamped
   `[300, 7×86400]`). Returns `{session_id, expires_at}`.
2. `POST /api/v1/sessions/{id}/login` — replay a declarative login script
   with `creds` in the request body. Body: `{script: LoginScript, creds: dict}`.
   Credentials live in RAM only — never persisted.
3. `POST /api/v1/scrape/page` with `session_id` set — scrape an authenticated
   page using the session's cookie jar.
4. `DELETE /api/v1/sessions/{id}` — clean up.

### Login DSL

The script is a list of steps, each one of:

| op                  | required fields                      | maps to                             |
| ------------------- | ------------------------------------ | ----------------------------------- |
| `goto`              | `url`                                | `page.goto(url)`                    |
| `fill`              | `selector`, `value`                  | `page.fill(selector, value)`        |
| `click`             | `selector`                           | `page.click(selector)`              |
| `wait_for_selector` | `selector` (optional `timeout_ms`)   | `page.wait_for_selector(...)`       |
| `wait_for_timeout`  | `ms`                                 | `page.wait_for_timeout(ms)`         |
| `press_key`         | `selector`, `key`                    | `page.press(selector, key)`         |
| `type_text`         | `selector`, `value`                  | `page.type(selector, value)`        |
| `hover`             | `selector`                           | `page.hover(selector)`              |

`value` and `url` support `$creds_<key>` substitution from the `creds` dict
(`$creds_email`, `$creds_password`, etc.). Unknown tokens stay literal.

### Error rules

- `session_id` + `cookies` together → **422 cannot pass both**.
- `session_id` + mismatched `device` / `proxy_type` / `proxy_pool_id` /
  `proxy_geo` → **422**.
- `proxy_type=res_rotating` without `proxy_pool_id` on create → **422**
  (rotating exit IPs invalidate the server-side session).
- TTL expired → **410 Gone**.
- Login script step failure → response carries `failed_step_index` +
  `failed_step` + base64 screenshot (always-on-failure, never-on-success).

### Example

```bash
# 1. Create
curl -X POST http://localhost:8000/api/v1/sessions \
  -H 'content-type: application/json' \
  -d '{"device":"desktop","proxy_type":"none","ttl_seconds":3600}'
# {"session_id":"sess_...","expires_at":...}

# 2. Login (creds in body)
curl -X POST http://localhost:8000/api/v1/sessions/$ID/login \
  -H 'content-type: application/json' \
  -d '{
    "creds":{"email":"tomsmith","password":"SuperSecretPassword!"},
    "script":{
      "steps":[
        {"op":"goto","url":"https://the-internet.herokuapp.com/login"},
        {"op":"fill","selector":"#username","value":"$creds_email"},
        {"op":"fill","selector":"#password","value":"$creds_password"},
        {"op":"click","selector":"button[type=submit]"},
        {"op":"wait_for_selector","selector":".flash.success"}
      ],
      "success_selector":".flash.success"
    }
  }'

# 3. Authenticated scrape
curl -X POST http://localhost:8000/api/v1/scrape/page \
  -d '{"url":"https://the-internet.herokuapp.com/secure","session_id":"'$ID'","raw_html":true}'
```

End-to-end runnable example: [`../examples/login_session_scraping.py`](../examples/login_session_scraping.py).

### Cookie injection — escape hatch for CAPTCHA / 2FA / MFA

When the login flow hits a CAPTCHA, device-verification challenge, or 2FA
prompt that the Phase 1 DSL can't solve, skip the login script and inject
cookies you exported from your own logged-in browser:

```bash
# 1. Log in manually in your real Chrome on the target site.
# 2. Export cookies via an extension like Cookie-Editor or EditThisCookie
#    (right-click → Export → JSON copies an array to the clipboard).
# 3. Create the session as usual:
curl -X POST http://localhost:8000/api/v1/sessions \
  -d '{"device":"desktop","proxy_type":"none","ttl_seconds":86400}'
# 4. Paste the cookie array into the session:
curl -X POST http://localhost:8000/api/v1/sessions/$ID/cookies \
  -H 'content-type: application/json' \
  -d '{"cookies":[{"name":"sessionid","value":"abc","domain":".example.com","path":"/","expires":1800000000,"httpOnly":true,"secure":true,"sameSite":"Lax"}, ...]}'
# 5. Scrape with session_id; the session's storage_state carries your
#    authenticated cookies into every fetch.
```

The Tester UI has the same flow under **Sessions → Inject Cookies** —
it accepts the raw extension-export JSON and converts `expirationDate` (float)
to `expires` (int), normalizes `sameSite` casing, and drops extension-only
keys (`hostOnly`, `storeId`, `session`).

The cookie endpoint is also useful when you already have a long-lived API
session cookie (CI service account, partner-supplied token) and want to skip
the login flow entirely.

### Tester UX niceties

- **Pool ID** in the Sessions tab is populated from `/api/v1/proxies/available`
  just like the Scrape / Batch / Crawler tabs do — pick the pool from the
  dropdown instead of typing a UUID. Visible only when `proxy_type != none`.
- **Picking a session** on Scrape Page / Batch / Crawler auto-copies its
  pinned `device`, `proxy_type`, `proxy_pool_id`, and `proxy_geo` into the
  form. If the form already has non-default values that differ from the pin,
  a confirm dialog asks before overwriting (the scraper would 422 a mismatched
  submit anyway).
- **Authenticated SOCKS5 proxies** — the login worker shares the same
  HTTP-to-SOCKS5 bridge that the scrape path uses, so CyberYozh residential /
  mobile pools that present as `socks5://user:pass@host:port` work for login
  as well as scrape.

### Known limitations (Phase 1)

- In-memory only — sessions are lost on container restart. Persistence is a
  future operational-pack feature.
- IndexedDB / Service Worker state is invisible to Playwright's
  `storage_state` — sites that store JWT in IndexedDB (Dexie, Firebase Auth)
  will appear logged-in mid-session but lose state on restore.
- Per-session storage_state cap is 2 MB; SPA-heavy targets may hit this.
- `res_rotating` requires `proxy_pool_id` (see above).
- No auto re-login on 401 / cookie expiry — Phase 2.
- No CAPTCHA / MFA hooks — Phase 3.

## Tests

```bash
pip install -e ".[test]"
pytest -q
```

Tests live in [`../tests/`](../tests/) and cover the API, job queue, and
proxy resolver. Browser-dependent tests assume a Playwright image is
available.
