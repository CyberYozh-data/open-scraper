const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const path = require('path');

const app = express();
const PORT = 7000;

// Disable caching so edits to index.html/app.js show up immediately on refresh.
app.use((req, res, next) => {
  res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, proxy-revalidate');
  res.setHeader('Pragma', 'no-cache');
  res.setHeader('Expires', '0');
  next();
});
app.use(express.static(path.join(__dirname, 'public'), { etag: false, lastModified: false }));

// The scraper gates /api/v2/prem-proxies/*, /api/v1/proxies/resolve and every
// /api/v1/sessions route behind a shared secret. Injected HERE rather than in
// the page so the browser never holds it:
//   SERVICE_TOKEN=... node server.js
//
// TWO constraints, because the first draft of this had neither and was worse
// than the hole it closed. `/proxy` takes its upstream from the caller's
// `x-scraper-target` header and `normalizeTarget` accepts ANY url, so an
// unconditional header handed the secret to whatever host the caller named —
// and this listener is not bound to loopback. That secret is not scoped to the
// catalog: it also unlocks `/api/v1/proxies/resolve`, which returns a fully
// credentialed paid-proxy URL, and the whole sessions router.
//
//   1. only to a target on the allowlist, and
//   2. only on the paths that are actually gated.
const SERVICE_TOKEN = process.env.SERVICE_TOKEN || '';
const ALLOWED_TARGETS = new Set(
  (process.env.SCRAPER_TARGETS ||
    'http://localhost:8000,http://127.0.0.1:8000,http://web-scraper:8000,' +
    'http://localhost:18000,http://127.0.0.1:18000,' +
    'http://localhost:8001,http://127.0.0.1:8001,http://open-crawler:8001,' +
    'http://localhost:18001,http://127.0.0.1:18001')
    .split(',')
    .map((t) => t.trim().replace(/\/$/, ''))
    .filter(Boolean),
);
const GATED_PREFIXES = [
  '/api/v2/prem-proxies',
  '/api/v1/proxies/resolve',
  '/api/v1/proxies/available',
  '/api/v1/sessions',
];

function isGatedPath(url) {
  const path = String(url || '').replace(/^\/proxy/, '').split('?')[0];
  return GATED_PREFIXES.some((p) => path === p || path.startsWith(p + '/'));
}

if (!SERVICE_TOKEN) {
  console.warn(
    '[tester] SERVICE_TOKEN is unset: the prem-proxy and sessions panels will ' +
    'get 401s from a gated scraper. Set it to the value in the scraper .env.',
  );
}

// Single proxy instance per target (cached)
const proxyCache = {};
function getProxy(target) {
  if (!proxyCache[target]) {
    const tokenAllowed = SERVICE_TOKEN !== '' && ALLOWED_TARGETS.has(target);
    if (SERVICE_TOKEN !== '' && !tokenAllowed) {
      console.warn(
        `[tester] not sending the service token to ${target}: not in ` +
        'SCRAPER_TARGETS. Add it there if that host is really yours.',
      );
    }
    proxyCache[target] = createProxyMiddleware({
      target,
      changeOrigin: true,
      pathRewrite: { '^/proxy': '' },
      on: {
        proxyReq: (proxyReq, req) => {
          // Per REQUEST, not per proxy instance: the token belongs only on the
          // gated paths, so a leak through any other route is impossible even
          // if the allowlist is later widened.
          if (tokenAllowed && isGatedPath(req.url)) {
            proxyReq.setHeader('X-Service-Token', SERVICE_TOKEN);
          }
        },
        error: (err, req, res) => {
          res.status(502).json({ error: 'Proxy error', detail: err.message });
        },
      },
    });
  }
  return proxyCache[target];
}

function normalizeTarget(raw) {
  const s = String(raw || 'http://localhost:8000').trim();
  try {
    const u = new URL(s);
    // Scheme + host + port only — drop path / query / trailing slash so
    // 'http://host:8000' and 'http://host:8000/' share one cached proxy.
    return `${u.protocol}//${u.host}`;
  } catch {
    return 'http://localhost:8000';
  }
}

app.use('/proxy', (req, res, next) => {
  const target = normalizeTarget(req.headers['x-scraper-target']);
  getProxy(target)(req, res, next);
});

// Loopback by default. `/proxy` has no authentication of its own, so with a
// SERVICE_TOKEN configured a listener on 0.0.0.0 is an unauthenticated
// capability proxy: anyone who can reach port 7000 can have the tester call
// `/api/v1/proxies/resolve` (a fully credentialed proxy URL) or the sessions
// router against the allowlisted scraper, and relay the answer back. The
// target and path allowlists stop the token being EXFILTRATED; they do not
// stop it being USED on someone else's behalf.
//
// Set TESTER_HOST=0.0.0.0 to expose it deliberately — and then only on a
// network you trust, or with SERVICE_TOKEN unset.
const HOST = process.env.TESTER_HOST || '127.0.0.1';
if (HOST !== '127.0.0.1' && HOST !== 'localhost' && SERVICE_TOKEN) {
  console.warn(
    `[tester] listening on ${HOST} WITH a service token: anyone who can reach ` +
    'this port can use it against the scraper. Unset SERVICE_TOKEN or bind loopback.',
  );
}
app.listen(PORT, HOST, () => {
  console.log(`Scraper Tester running at http://${HOST}:${PORT}`);
});
