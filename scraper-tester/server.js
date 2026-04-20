const express = require('express');
const { createProxyMiddleware } = require('http-proxy-middleware');
const path = require('path');

const app = express();
const PORT = 7000;

app.use(express.static(path.join(__dirname, 'public')));

// Single proxy instance per target (cached)
const proxyCache = {};
function getProxy(target) {
  if (!proxyCache[target]) {
    proxyCache[target] = createProxyMiddleware({
      target,
      changeOrigin: true,
      pathRewrite: { '^/proxy': '' },
      on: {
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

app.listen(PORT, () => {
  console.log(`Scraper Tester running at http://localhost:${PORT}`);
});
