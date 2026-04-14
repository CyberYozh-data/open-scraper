# Open Scraper

Web Scraping API built on **Playwright**. Renders any URL in a real browser and returns:
- extracted fields via CSS or XPath (optional)
- raw HTML (optional)
- screenshot (optional, base64)

Supports async job queuing, batch scraping, device emulation, and CyberYozh proxy integration.

**Base URL:** `http://localhost:8000`  
**OpenAPI docs:** `http://localhost:8000/docs`  
**MCP endpoint:** `http://localhost:8000/mcp`

## Quick start (Docker)

```bash
cp .env.example .env
docker-compose up --build
```

Service: `http://localhost:8000`

```bash
curl http://localhost:8000/api/v1/health
# {"status":"ok"}
```

Full test example:

```bash
python3 scripts/e2e_smoke.py
```

## Proxy Support

**For reliable web scraping, using proxies is essential.** Most modern websites (especially search engines, e-commerce, social media) have anti-bot protection that blocks direct scraping attempts. Proxies help you:

- Avoid IP bans and CAPTCHAs
- Bypass geo-restrictions
- Scale scraping operations
- Appear as real users from different locations

### CyberYozh Proxy Integration

This scraper integrates with **CyberYozh Proxy Service** which provides residential, mobile (LTE), and datacenter proxies.

**Get your API key:** https://app.cyberyozh.com/api-access/

**Proxy documentation:** https://docs.cyberyozh.com/proxies

Set `CYBERYOZH_API_KEY` in `.env` file to enable proxy support.

### Available Proxy Types

- `res_rotating` - Residential rotating (recommended for most scraping)
- `res_static` - Residential static
- `mobile` - Mobile (LTE) with IP rotation
- `dc_static` - Datacenter static
- `none` - No proxy (direct connection)

## API

## Scrape (async jobs)

Scrape endpoints always create a background job and return `job_id`.
Then you poll job status and fetch results.

Routes:

* `POST /api/v1/scrape/page`
* `POST /api/v1/scrape/pages`
* `GET  /api/v1/scrape/{job_id}`
* `GET  /api/v1/scrape/{job_id}/results`

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

Fetch results (available for both `done` and `failed` jobs):

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
  if [ "$STATUS" = "failed" ]; then
    curl -s "http://localhost:8000/api/v1/scrape/$JOB_ID" | jq .
    exit 1
  fi
  sleep 0.5
done
```

---

### 3) Batch scrape (multiple pages)

Create job:

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

Then poll status + fetch results using the same `GET /api/v1/scrape/{job_id}` and `/results`.

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

Result will contain:

```json
{
  "data": {
    "title": "Example Domain"
  }
}
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
      "fields": {
        "title": {"selector": "h1", "attr": "text"}
      }
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

### 13) Proxies (CyberYozh) with GEO targeting

`proxy_geo` accepts the following optional fields:

| Field          | Type   | Description                        |
|----------------|--------|------------------------------------|
| `country_code` | string | ISO 3166-1 alpha-2 (e.g. `"US"`, `"GB"`) |
| `region`       | string | Region / state name                |
| `city`         | string | City name (e.g. `"London"`)        |

GEO targeting is supported only for `res_rotating` proxies. For `res_static`, `mobile`, and `dc_static` the field is accepted but ignored (a warning is logged).

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

Request through a London residential proxy and verify the exit IP is actually in London:

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
{
  "ip": "185.x.x.x",
  "city": "London",
  "country_code": "GB"
}
```

For a ready-to-run Python script that tests multiple cities see [`examples/geo_scraping.py`](examples/geo_scraping.py):

```bash
cd examples
python geo_scraping.py
```

## Request Schema

### POST /api/v1/scrape/page

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | required | Target URL |
| `render` | boolean | `true` | Run full JS rendering in browser |
| `wait_until` | `domcontentloaded` \| `networkidle` | `domcontentloaded` | When to consider page loaded |
| `wait_for_selector` | string | - | Wait for CSS selector before extracting |
| `timeout_ms` | integer | - | Per-page timeout (ms), overrides global |
| `device` | `desktop` \| `mobile` | `desktop` | Device emulation |
| `headers` | object | - | Custom HTTP headers |
| `cookies` | array | - | Cookies to inject |
| `proxy_type` | string | `none` | See proxy types table |
| `proxy_geo` | object | - | `{ country_code, region, city }` — `res_rotating` only |
| `block_assets` | boolean | - | Block images/fonts/media (faster scraping) |
| `raw_html` | boolean | `false` | Include raw HTML in response |
| `screenshot` | boolean | `false` | Include full-page screenshot as base64 |
| `extract` | object | - | Structured extraction rules |

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
| `meta.retries` | integer | Number of retries |
| `data` | object | Extracted fields (if `extract` was set) |
| `raw_html` | string | Raw HTML (if `raw_html: true`) |
| `screenshot_base64` | string | Base64 PNG (if `screenshot: true`) |
| `warnings` | array | Non-fatal warnings |

---

## MCP Integration

Open Scraper exposes all its API tools via the [Model Context Protocol](https://modelcontextprotocol.io/) at `/mcp`. This lets AI assistants use the scraper directly as a tool — no extra code required.

### Available MCP tools

| Tool | Description |
|------|-------------|
| `run_scrape_page` | Scrape a single page, returns `job_id` |
| `run_scrape_pages` | Scrape multiple pages in batch, returns `job_id` |
| `get_job_status` | Poll job status by `job_id` |
| `get_job_result` | Fetch results for a completed job |
| `health` | Health check |

### Claude Code

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "open-scraper": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Restart Claude Code — scraper tools appear automatically. Then just ask in chat:
```
Scrape https://example.com and tell me what's on the page
```

### LangChain Agent

```bash
pip install langchain-anthropic langchain-mcp-adapters langgraph
export ANTHROPIC_API_KEY=sk-ant-...
python examples/agent.py
```

See [`examples/agent.py`](examples/agent.py) — a chat agent that handles the full job lifecycle: submit -> poll -> fetch -> summarize.

### MCP Inspector (debug UI)

```bash
npx @modelcontextprotocol/inspector http://localhost:8000/mcp
# Opens at http://localhost:6274
```

---

## Tests

```bash
pip install -e ".[test]"
pytest -q
```

## Proxy types (MVP mapping)

This MVP maps `proxy_type` to CyberYozh `category` for `GET /api/v1/proxies/history/`:

- mobile_shared, mobile -> lte
- res_static -> residential
- res_rotating -> residential_rotating (also calls POST /api/v1/proxies/rotating-credentials/)
- dc_static -> datacenter

If your categories differ, update `PROXY_TYPE_TO_CATEGORY` in `app/proxy/resolver.py`.
