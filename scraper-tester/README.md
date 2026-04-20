# Scraper Tester

Lightweight web UI for manually testing the Open Scraper API. A thin Node.js
(Express) server serves a single-page frontend on `http://localhost:7000` and
proxies requests to a running Open Scraper instance, bypassing CORS so you can
point it at any reachable deployment.

## Features

- **Scrape Page** — full parameter set: URL, device, `wait_until`, selectors,
  timeout, custom headers, cookies, proxy type + geo, block assets, raw HTML,
  screenshots, CSS/XPath extraction.
- **Batch Scrape** — submit multiple URLs with per-page proxy type / screenshot
  / raw HTML toggles.
- **Jobs** — look up any job by id, view status and results, history of recent
  jobs in the session.
- **MCP** — initialize a Streamable HTTP session, list available tools, call
  any tool with JSON arguments.
- **Header presets** — dropdown with Chrome/Firefox/Safari/Mobile/RU presets
  to fill common request headers in one click.
- **Result rendering** — proxy/status/retries badge, raw HTML shown in a
  collapsible block, screenshot previewed inline.

## Requirements

- Node.js 18+
- A running Open Scraper instance (default `http://localhost:8000`)

## Usage

```bash
cd scraper-tester
npm install
node server.js
```

Open [http://localhost:7000](http://localhost:7000) in a browser. Change the
"Scraper URL" field in the header to target a different deployment.
