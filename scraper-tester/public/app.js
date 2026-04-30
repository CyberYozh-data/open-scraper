// ─── State ───────────────────────────────────────────────────────────────────
const JOBS_STORAGE_KEY = 'scraper-tester:recent-jobs';
let recentJobs = [];
try {
  const saved = localStorage.getItem(JOBS_STORAGE_KEY);
  if (saved) recentJobs = JSON.parse(saved);
} catch {}

function saveRecentJobs() {
  try { localStorage.setItem(JOBS_STORAGE_KEY, JSON.stringify(recentJobs)); }
  catch {}
}

function normalizeUrl(raw) {
  const s = String(raw || '').trim();
  if (!s) return '';
  try {
    const u = new URL(s);
    if (u.protocol === 'http:' || u.protocol === 'https:') return s;
  } catch {}
  return 'https://' + s.replace(/^\/+/, '');
}

let cachedWorkers = 2;
async function loadServerConfig() {
  try {
    const { ok, data } = await apiCall('/api/v1/health');
    if (ok && data?.workers) cachedWorkers = Number(data.workers) || 2;
  } catch {}
}

async function loadCountries() {
  const selects = ['s-geo-country', 'b-geo-country', 'c-geo-country', 'cp-geo-country']
    .map(id => document.getElementById(id)).filter(Boolean);
  if (!selects.length) return;

  const { ok, data } = await apiCall('/api/v1/proxies/countries');
  if (!ok || !data?.countries) return;

  const sorted = [...data.countries].sort((a, b) => a.name.localeCompare(b.name));
  const optionsHtml = '<option value="">— any —</option>' +
    sorted.map(c => `<option value="${escapeHtml(c.code)}">${escapeHtml(c.name)} (${escapeHtml(c.code)})</option>`).join('');

  selects.forEach(sel => {
    const prev = sel.value;
    sel.innerHTML = optionsHtml;
    if (prev) sel.value = prev;
  });
}
loadCountries();

let mcpSessionId = null;

// ─── Helpers ─────────────────────────────────────────────────────────────────
function scraperUrl() {
  return document.getElementById('scraperUrl').value.replace(/\/$/, '');
}

async function apiCall(path, options = {}) {
  const url = `/proxy${path}`;
  const res = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'x-scraper-target': scraperUrl(),
      ...(options.headers || {}),
    },
  });
  const text = await res.text();
  try { return { ok: res.ok, status: res.status, data: JSON.parse(text) }; }
  catch { return { ok: res.ok, status: res.status, data: text }; }
}

function syntaxHighlight(obj) {
  const json = JSON.stringify(obj, null, 2);
  return json.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
    (match) => {
      let cls = 'json-num';
      if (/^"/.test(match)) {
        cls = /:$/.test(match) ? 'json-key' : 'json-str';
      } else if (/true|false/.test(match)) {
        cls = 'json-bool';
      } else if (/null/.test(match)) {
        cls = 'json-null';
      }
      return `<span class="${cls}">${match}</span>`;
    }
  );
}

function showResult(elId, data) {
  const el = document.getElementById(elId);

  // Preserve open/close state of <details> across re-renders by their
  // data-key attribute. Also remember scroll position.
  const openKeys = new Set(
    Array.from(el.querySelectorAll('details[open][data-key]')).map(d => d.dataset.key)
  );
  const prevScroll = el.scrollTop;

  el.innerHTML = '';

  if (typeof data === 'string') {
    el.innerHTML = `<span style="color:var(--text)">${escapeHtml(data)}</span>`;
    return;
  }

  // For scrape job results — show summary card + separate content blocks
  if (data?.results && Array.isArray(data.results)) {
    // Summary block (without raw_html/screenshot/data payloads)
    const summary = {
      job_id: data.job_id,
      status: data.status,
      total: data.total,
      done: data.done,
      error: data.error,
      results: data.results.map(r => ({
        request_id: r.request_id,
        took_ms: r.took_ms,
        meta: r.meta,
        data: r.data ? '[see below]' : null,
        warnings: r.warnings,
        raw_html: r.raw_html ? `[${r.raw_html.length} chars — see below]` : null,
        screenshot_base64: r.screenshot_base64 ? '[see below]' : null,
      })),
    };

    const pre = document.createElement('pre');
    pre.innerHTML = syntaxHighlight(summary);
    el.appendChild(pre);

    // Per-result content blocks
    data.results.forEach((r, i) => {
      // Proxy info badge
      const meta = r.meta || {};
      const proxyBadge = document.createElement('div');
      proxyBadge.style.cssText = 'margin:0.75rem 0 0.4rem;padding:0.5rem 0.75rem;background:var(--bg-secondary);border:1px solid var(--border);border-radius:var(--radius-sm);font-size:12px;color:var(--text-secondary)';
      proxyBadge.innerHTML = `<b style="color:var(--text-primary)">Result #${i+1}</b> &nbsp;|&nbsp; proxy: <b style="color:var(--color-blue)">${escapeHtml(meta.proxy_type||'—')}</b>${meta.proxy_pool_id ? ` / pool: ${escapeHtml(meta.proxy_pool_id)}` : ''} &nbsp;|&nbsp; status: <b style="color:var(--color-green)">${meta.status_code||'—'}</b> &nbsp;|&nbsp; retries: ${meta.retries??'—'} &nbsp;|&nbsp; took: ${r.took_ms}ms`;
      el.appendChild(proxyBadge);

      // Extracted data
      if (r.data && typeof r.data === 'object' && Object.keys(r.data).length > 0) {
        const wrap = document.createElement('details');
        wrap.dataset.key = `data-${r.request_id}`;
        wrap.setAttribute('open', '');
        wrap.style.cssText = 'margin-bottom:0.5rem';
        const fieldCount = Object.keys(r.data).length;
        wrap.innerHTML = `<summary class="details-summary" style="cursor:pointer;padding:0.5rem 0.75rem;background:var(--bg-secondary);border:1px solid var(--border);border-radius:var(--radius-sm);font-size:12px;color:var(--text-secondary);display:flex;align-items:center;gap:0.5rem;list-style:none"><span class="details-chevron" style="display:inline-block;transition:transform 0.15s;font-size:10px">▶</span><span>Extracted Data (${fieldCount} field${fieldCount === 1 ? '' : 's'})</span></summary>`;
        const pre = document.createElement('pre');
        pre.style.cssText = 'max-height:400px;overflow:auto;background:var(--bg-primary);border:1px solid var(--border);border-top:none;padding:0.75rem;font-size:12px;border-radius:0 0 var(--radius-sm) var(--radius-sm);color:var(--text-primary);white-space:pre-wrap;word-break:break-word';
        pre.innerHTML = syntaxHighlight(r.data);
        wrap.appendChild(pre);
        el.appendChild(wrap);
      }

      // Raw HTML
      if (r.raw_html) {
        const wrap = document.createElement('details');
        wrap.dataset.key = `html-${r.request_id}`;
        if (openKeys.has(wrap.dataset.key)) wrap.setAttribute('open', '');
        wrap.style.cssText = 'margin-bottom:0.5rem';

        const summary = document.createElement('summary');
        summary.className = 'details-summary';
        summary.style.cssText = 'cursor:pointer;padding:0.5rem 0.75rem;background:var(--bg-secondary);border:1px solid var(--border);border-radius:var(--radius-sm);font-size:12px;color:var(--text-secondary);display:flex;align-items:center;gap:0.5rem;list-style:none';
        summary.innerHTML = `<span class="details-chevron" style="display:inline-block;transition:transform 0.15s;font-size:10px">▶</span><span style="flex:1">Raw HTML (${r.raw_html.length} chars)</span>`;

        const btnCopy = document.createElement('button');
        btnCopy.textContent = 'Copy';
        btnCopy.className = 'btn-secondary btn-sm';
        btnCopy.addEventListener('click', async (e) => {
          e.preventDefault();
          e.stopPropagation();
          try {
            await navigator.clipboard.writeText(r.raw_html);
            const orig = btnCopy.textContent;
            btnCopy.textContent = 'Copied!';
            setTimeout(() => { btnCopy.textContent = orig; }, 1200);
          } catch (err) {
            btnCopy.textContent = 'Failed';
          }
        });

        const btnDownload = document.createElement('button');
        btnDownload.textContent = 'Download';
        btnDownload.className = 'btn-secondary btn-sm';
        btnDownload.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          const blob = new Blob([r.raw_html], { type: 'text/html;charset=utf-8' });
          const href = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = href;
          const safeUrl = (meta.final_url || meta.url || 'page').replace(/[^a-z0-9]+/gi, '_').slice(0, 60);
          a.download = `${safeUrl}_${r.request_id}.html`;
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          URL.revokeObjectURL(href);
        });

        summary.appendChild(btnCopy);
        summary.appendChild(btnDownload);
        wrap.appendChild(summary);

        const code = document.createElement('pre');
        code.style.cssText = 'max-height:400px;overflow:auto;background:var(--bg-primary);border:1px solid var(--border);border-top:none;padding:0.75rem;font-size:11px;border-radius:0 0 var(--radius-sm) var(--radius-sm);color:var(--text-primary)';
        code.textContent = r.raw_html;
        wrap.appendChild(code);
        el.appendChild(wrap);
      }

      // Screenshot
      if (r.screenshot_base64) {
        const wrap = document.createElement('details');
        wrap.dataset.key = `shot-${r.request_id}`;
        // Default open on first render; otherwise preserve user's state
        if (openKeys.size === 0 || openKeys.has(wrap.dataset.key)) {
          wrap.setAttribute('open', '');
        }
        wrap.style.cssText = 'margin-bottom:0.5rem';
        wrap.innerHTML = `<summary class="details-summary" style="cursor:pointer;padding:0.5rem 0.75rem;background:var(--bg-secondary);border:1px solid var(--border);border-radius:var(--radius-sm);font-size:12px;color:var(--text-secondary);display:flex;align-items:center;gap:0.5rem;list-style:none"><span class="details-chevron" style="display:inline-block;transition:transform 0.15s;font-size:10px">▶</span><span>Screenshot</span></summary>`;
        const img = document.createElement('img');
        img.src = `data:image/png;base64,${r.screenshot_base64}`;
        img.style.cssText = 'width:100%;display:block;border:1px solid var(--border);border-top:none;border-radius:0 0 var(--radius-sm) var(--radius-sm)';
        wrap.appendChild(img);
        el.appendChild(wrap);
      }
    });
    el.scrollTop = prevScroll;
    return;
  }

  // Default JSON display
  const pre = document.createElement('pre');
  pre.innerHTML = syntaxHighlight(data);
  el.appendChild(pre);
  el.scrollTop = prevScroll;
}

function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function setStatus(elId, status, message) {
  const el = document.getElementById(elId);
  el.className = `status-bar ${status}`;
  const spinner = (status === 'running' || status === 'queued')
    ? '<div class="spinner"></div>' : '';
  el.innerHTML = `${spinner}<span>${escapeHtml(message)}</span>`;
}

function addRecentJob(jobId, status) {
  const existing = recentJobs.find(j => j.id === jobId);
  const ts = Date.now();
  if (existing) {
    existing.status = status;
    existing.ts = ts;
  } else {
    recentJobs.unshift({ id: jobId, status, ts });
  }
  if (recentJobs.length > 20) recentJobs.pop();
  saveRecentJobs();
  renderRecentJobs();
}

function renderRecentJobs() {
  const el = document.getElementById('recent-jobs');
  el.innerHTML = recentJobs.map(j => `
    <span class="job-chip" data-id="${escapeHtml(j.id)}">
      <span class="dot dot-${j.status}"></span>
      ${escapeHtml(j.id)}
    </span>`).join('');
  el.querySelectorAll('.job-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      document.getElementById('j-job-id').value = chip.dataset.id;
    });
  });
}

async function pollJobBatch(jobId, statusElId, resultElId, totalExpected, cancelBtnId) {
  setStatus(statusElId, 'queued', `Job queued: ${jobId}`);
  addRecentJob(jobId, 'queued');

  const el = document.getElementById(statusElId);
  setScrapeCancelButton(cancelBtnId, 'armed', jobId);

  while (true) {
    await new Promise(r => setTimeout(r, 1000));
    const { data } = await apiCall(`/api/v1/scrape/${jobId}`);

    if (!data || data.detail) {
      setStatus(statusElId, 'failed', `Error polling job`);
      addRecentJob(jobId, 'failed');
      showResult(resultElId, data);
      setScrapeCancelButton(cancelBtnId, 'done');
      return;
    }

    addRecentJob(jobId, data.status);

    if (data.status === 'queued' || data.status === 'running') {
      const total = data.total ?? totalExpected ?? 0;
      const done = data.done ?? 0;
      const remaining = Math.max(0, total - done);
      const running = data.status === 'running' ? Math.min(cachedWorkers, remaining) : 0;
      const queued = Math.max(0, remaining - running);
      const pct = total > 0 ? Math.round((done / total) * 100) : 0;

      el.className = `status-bar ${data.status}`;
      el.innerHTML = `
        <div class="spinner"></div>
        <div style="flex:1;display:flex;flex-direction:column;gap:4px">
          <div style="display:flex;gap:0.75rem;align-items:center;font-size:13px">
            <span><b>${done}</b> done</span>
            <span>·</span>
            <span><b>${running}</b> running</span>
            <span>·</span>
            <span><b>${queued}</b> queued</span>
            <span style="margin-left:auto;color:var(--text-secondary);font-size:11px">${pct}%</span>
          </div>
          <div style="height:6px;background:var(--neutral-200);border-radius:3px;overflow:hidden">
            <div style="height:100%;width:${pct}%;background:currentColor;transition:width 0.3s"></div>
          </div>
        </div>`;

      // Show partial results as they complete
      if (done > 0) {
        const res = await apiCall(`/api/v1/scrape/${jobId}/results`);
        if (res.ok && res.data?.results) {
          showResult(resultElId, res.data);
        }
      }
      continue;
    }

    if (data.status === 'done') {
      setStatus(statusElId, 'done', `Done in ${data.done} pages`);
      const res = await apiCall(`/api/v1/scrape/${jobId}/results`);
      showResult(resultElId, res.data);
      setScrapeCancelButton(cancelBtnId, 'done');
      return;
    }

    if (data.status === 'cancelled') {
      setStatus(statusElId, 'failed', `Cancelled (${data.done}/${data.total} completed)`);
      const res = await apiCall(`/api/v1/scrape/${jobId}/results`);
      showResult(resultElId, res.data);
      setScrapeCancelButton(cancelBtnId, 'done');
      return;
    }

    if (data.status === 'failed') {
      setStatus(statusElId, 'failed', `Failed: ${data.error || 'unknown error'}`);
      showResult(resultElId, data);
      setScrapeCancelButton(cancelBtnId, 'done');
      return;
    }
  }
}

// ─── Scrape/Batch/Job cancel button state machine ────────────────────────────
// State: ready (hidden) | armed (shown, first click = soft cancel) | done (hidden)
const _scrapeJobIdByBtn = new Map();
function setScrapeCancelButton(btnId, state, jobId) {
  if (!btnId) return;
  const btn = document.getElementById(btnId);
  if (!btn) return;
  if (state === 'armed') {
    _scrapeJobIdByBtn.set(btnId, jobId);
    btn.style.display = '';
    btn.disabled = false;
    btn.textContent = 'Cancel';
    btn.classList.remove('btn-primary');
    btn.classList.add('btn-secondary');
  } else if (state === 'cancelling') {
    btn.disabled = true;
    btn.textContent = 'Cancelling…';
  } else {
    _scrapeJobIdByBtn.delete(btnId);
    btn.style.display = 'none';
    btn.disabled = true;
  }
}
async function handleScrapeCancelClick(btnId) {
  const jobId = _scrapeJobIdByBtn.get(btnId);
  if (!jobId) return;
  setScrapeCancelButton(btnId, 'cancelling');
  await apiCall(`/api/v1/scrape/${jobId}`, { method: 'DELETE' });
  // poller picks up the cancelled status on its next tick and hides the button.
}
['btnCancelScrape', 'btnCancelBatch', 'btnCancelJob'].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener('click', () => handleScrapeCancelClick(id));
});

// Legacy alias — all flows use the batch-style poller which supports partial
// results and preserves expand/collapse state across polls.
const pollJob = pollJobBatch;

// ─── Header Presets ───────────────────────────────────────────────────────────
const HEADER_PRESETS = {
  chrome_win: {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'sec-ch-ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    'sec-ch-ua-platform': '"Windows"',
    'sec-ch-ua-mobile': '?0',
  },
  chrome_mac: {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'sec-ch-ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    'sec-ch-ua-platform': '"macOS"',
    'sec-ch-ua-mobile': '?0',
  },
  firefox_win: {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'DNT': '1',
  },
  mobile_chrome: {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'sec-ch-ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    'sec-ch-ua-platform': '"Android"',
    'sec-ch-ua-mobile': '?1',
  },
  safari_ios: {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
  },
  ru_locale: {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
  },
  antibot: {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'sec-ch-ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    'sec-ch-ua-platform': '"Windows"',
    'sec-ch-ua-mobile': '?0',
    'sec-fetch-dest': 'document',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-site': 'none',
    'sec-fetch-user': '?1',
    'upgrade-insecure-requests': '1',
    'DNT': '1',
  },
};

const MOBILE_PRESETS = new Set(['mobile_chrome', 'safari_ios']);

function applyHeaderPreset(presetKey) {
  const headers = HEADER_PRESETS[presetKey];
  if (!headers) return;
  document.getElementById('headers-list').innerHTML = '';
  Object.entries(headers).forEach(([k, v]) => {
    addDynamicRow('headers-list', ['Header name', 'Value']);
    const rows = document.querySelectorAll('#headers-list .dynamic-row');
    const last = rows[rows.length - 1];
    const inputs = last.querySelectorAll('input');
    inputs[0].value = k;
    inputs[1].value = v;
  });
  // Keep device in sync with preset's form factor to avoid UA/viewport mismatch
  const deviceSel = document.getElementById('s-device');
  if (deviceSel) deviceSel.value = MOBILE_PRESETS.has(presetKey) ? 'mobile' : 'desktop';
}

document.getElementById('btnApplyPreset').addEventListener('click', () => {
  const val = document.getElementById('header-preset').value;
  if (val) applyHeaderPreset(val);
});

document.getElementById('btnClearHeaders').addEventListener('click', () => {
  document.getElementById('headers-list').innerHTML = '';
  document.getElementById('header-preset').value = '';
});

// Batch variants
function applyHeaderPresetBatch(presetKey) {
  const headers = HEADER_PRESETS[presetKey];
  if (!headers) return;
  document.getElementById('b-headers-list').innerHTML = '';
  Object.entries(headers).forEach(([k, v]) => {
    addDynamicRow('b-headers-list', ['Header name', 'Value']);
    const rows = document.querySelectorAll('#b-headers-list .dynamic-row');
    const last = rows[rows.length - 1];
    const inputs = last.querySelectorAll('input');
    inputs[0].value = k;
    inputs[1].value = v;
  });
  const deviceSel = document.getElementById('b-device');
  if (deviceSel) deviceSel.value = MOBILE_PRESETS.has(presetKey) ? 'mobile' : 'desktop';
}

document.getElementById('btnApplyPresetBatch').addEventListener('click', () => {
  const val = document.getElementById('b-header-preset').value;
  if (val) applyHeaderPresetBatch(val);
});

document.getElementById('btnClearHeadersBatch').addEventListener('click', () => {
  document.getElementById('b-headers-list').innerHTML = '';
  document.getElementById('b-header-preset').value = '';
});

document.getElementById('btnAddHeaderBatch').addEventListener('click', () =>
  addDynamicRow('b-headers-list', ['Header name', 'Value']));

document.getElementById('btnAddCookieBatch').addEventListener('click', () =>
  addDynamicRow('b-cookies-list', ['name', 'value', 'domain', 'path']));

// ─── Tabs ─────────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
  });
});

// ─── Health Check ─────────────────────────────────────────────────────────────
document.getElementById('btnHealth').addEventListener('click', async () => {
  const badge = document.getElementById('healthStatus');
  badge.className = 'badge';
  badge.textContent = 'checking…';
  try {
    const { data, ok } = await apiCall('/api/v1/health');
    if (ok && data?.status === 'ok') {
      badge.className = 'badge ok';
      badge.textContent = 'ok';
    } else {
      badge.className = 'badge error';
      badge.textContent = 'error';
    }
  } catch {
    badge.className = 'badge error';
    badge.textContent = 'unreachable';
  }
});

// ─── Dynamic rows ─────────────────────────────────────────────────────────────
function addDynamicRow(containerId, placeholders) {
  const container = document.getElementById(containerId);
  const row = document.createElement('div');
  row.className = 'dynamic-row';
  row.innerHTML = placeholders.map(p =>
    `<input type="text" placeholder="${escapeHtml(p)}" />`
  ).join('') +
  `<button class="btn-remove" title="Remove">×</button>`;
  row.querySelector('.btn-remove').addEventListener('click', () => row.remove());
  container.appendChild(row);
}

function getRowValues(containerId) {
  const rows = document.querySelectorAll(`#${containerId} .dynamic-row`);
  return Array.from(rows).map(row =>
    Array.from(row.querySelectorAll('input')).map(i => i.value.trim())
  );
}

// Headers
document.getElementById('btnAddHeader').addEventListener('click', () =>
  addDynamicRow('headers-list', ['Header name', 'Value']));

// Cookies
document.getElementById('btnAddCookie').addEventListener('click', () =>
  addDynamicRow('cookies-list', ['name', 'value', 'domain', 'path']));

// Extract fields
document.getElementById('btnAddField').addEventListener('click', () =>
  addExtractField('extract-fields'));
document.getElementById('btnAddFieldBatch').addEventListener('click', () =>
  addExtractField('b-extract-fields'));

function addExtractField(containerId = 'extract-fields') {
  const container = document.getElementById(containerId);
  const row = document.createElement('div');
  row.className = 'dynamic-row extract-row';
  row.innerHTML = `
    <input type="text" placeholder="field name" style="flex:0 0 120px" />
    <input type="text" placeholder="selector" />
    <select style="flex:0 0 100px">
      <option value="text">text</option>
      <option value="html">html</option>
      <option value="href">href</option>
      <option value="src">src</option>
    </select>
    <label class="all-toggle" title="Return every match as a list instead of the first one">
      <input type="checkbox" />
      <span>all</span>
    </label>
    <button class="btn-remove" title="Remove row">×</button>`;
  row.querySelector('.btn-remove').addEventListener('click', () => row.remove());
  container.appendChild(row);
}

// ─── Build scrape payload ─────────────────────────────────────────────────────
function buildScrapePayload() {
  const url = normalizeUrl(document.getElementById('s-url').value);
  if (!url) { alert('URL is required'); return null; }

  const payload = {
    url,
    render: document.getElementById('s-render').checked,
    wait_until: document.getElementById('s-wait-until').value,
    device: document.getElementById('s-device').value,
    timeout_ms: Number(document.getElementById('s-timeout').value) || 30000,
    block_assets: document.getElementById('s-block-assets').checked,
    raw_html: document.getElementById('s-raw-html').checked,
    screenshot: document.getElementById('s-screenshot').checked,
    stealth: document.getElementById('s-stealth').checked,
    proxy_type: document.getElementById('s-proxy-type').value,
  };

  const waitSelector = document.getElementById('s-wait-selector').value.trim();
  if (waitSelector) payload.wait_for_selector = waitSelector;

  const poolId = document.getElementById('s-proxy-pool').value.trim();
  if (poolId) payload.proxy_pool_id = poolId;

  if (payload.proxy_type !== 'none' && !poolId) {
    alert(`Select a Pool ID for proxy type "${payload.proxy_type}". If none available, use the "Buy" button to purchase one on CyberYozh.`);
    return null;
  }

  // Geo (only for res_rotating)
  if (payload.proxy_type === 'res_rotating') {
    const cc = document.getElementById('s-geo-country').value.trim();
    const region = document.getElementById('s-geo-region').value.trim();
    const city = document.getElementById('s-geo-city').value.trim();
    if (cc || region || city) {
      payload.proxy_geo = {};
      if (cc) payload.proxy_geo.country_code = cc.toUpperCase();
      if (region) payload.proxy_geo.region = region;
      if (city) payload.proxy_geo.city = city;
    }
  }

  // Headers
  const headerRows = getRowValues('headers-list');
  if (headerRows.length) {
    payload.headers = {};
    headerRows.forEach(([k, v]) => { if (k) payload.headers[k] = v; });
  }

  // Cookies
  const cookieRows = getRowValues('cookies-list');
  if (cookieRows.length) {
    payload.cookies = cookieRows
      .filter(([n]) => n)
      .map(([name, value, domain, path]) => ({
        name, value, domain: domain || undefined, path: path || undefined
      }));
  }

  // Extract
  const extractType = document.getElementById('s-extract-type').value;
  if (extractType) {
    const fieldRows = document.querySelectorAll('#extract-fields .dynamic-row');
    const fields = {};
    let totalRows = 0;
    fieldRows.forEach(row => {
      totalRows++;
      const inputs = row.querySelectorAll('input[type="text"]');
      const name = inputs[0].value.trim();
      const selector = inputs[1].value.trim();
      const attr = row.querySelector('select').value;
      const all = row.querySelector('input[type="checkbox"]').checked;
      if (name && selector) {
        fields[name] = { selector, attr, all };
      }
    });
    if (totalRows > 0 && Object.keys(fields).length === 0) {
      alert('Extraction is enabled but no fields have both a name and a selector set. Please fill them in or disable extraction.');
      return null;
    }
    if (Object.keys(fields).length) {
      payload.extract = { type: extractType, fields };
    }
  }

  return payload;
}

// ─── Scrape Page ──────────────────────────────────────────────────────────────
document.getElementById('btnScrape').addEventListener('click', async () => {
  const payload = buildScrapePayload();
  if (!payload) return;

  document.getElementById('scrape-result').innerHTML = '';
  setStatus('scrape-status', 'queued', 'Submitting...');

  const { data, ok } = await apiCall('/api/v1/scrape/page', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

  if (!ok || !data?.job_id) {
    setStatus('scrape-status', 'failed', 'Failed to submit job');
    showResult('scrape-result', data);
    return;
  }

  await loadServerConfig();
  pollJob(data.job_id, 'scrape-status', 'scrape-result', 1, 'btnCancelScrape');
});

// ─── Batch Scrape ─────────────────────────────────────────────────────────────
function buildBatchSharedPayload() {
  const payload = {
    render: document.getElementById('b-render').checked,
    wait_until: document.getElementById('b-wait-until').value,
    device: document.getElementById('b-device').value,
    timeout_ms: Number(document.getElementById('b-timeout').value) || 30000,
    block_assets: document.getElementById('b-block-assets').checked,
    raw_html: document.getElementById('b-raw-html').checked,
    screenshot: document.getElementById('b-screenshot').checked,
    stealth: document.getElementById('b-stealth').checked,
    proxy_type: document.getElementById('b-proxy-type').value,
  };

  const waitSelector = document.getElementById('b-wait-selector').value.trim();
  if (waitSelector) payload.wait_for_selector = waitSelector;

  const poolId = document.getElementById('b-proxy-pool').value.trim();
  if (poolId) payload.proxy_pool_id = poolId;

  if (payload.proxy_type !== 'none' && !poolId) {
    alert(`Select a Pool ID for proxy type "${payload.proxy_type}". If none available, use the "Buy" button to purchase one on CyberYozh.`);
    return null;
  }

  if (payload.proxy_type === 'res_rotating') {
    const cc = document.getElementById('b-geo-country').value.trim();
    const region = document.getElementById('b-geo-region').value.trim();
    const city = document.getElementById('b-geo-city').value.trim();
    if (cc || region || city) {
      payload.proxy_geo = {};
      if (cc) payload.proxy_geo.country_code = cc.toUpperCase();
      if (region) payload.proxy_geo.region = region;
      if (city) payload.proxy_geo.city = city;
    }
  }

  // Shared extract
  const bExtractType = document.getElementById('b-extract-type').value;
  if (bExtractType) {
    const fieldRows = document.querySelectorAll('#b-extract-fields .dynamic-row');
    const fields = {};
    let totalRows = 0;
    fieldRows.forEach(row => {
      totalRows++;
      const inputs = row.querySelectorAll('input[type="text"]');
      const name = inputs[0].value.trim();
      const selector = inputs[1].value.trim();
      const attr = row.querySelector('select').value;
      const all = row.querySelector('input[type="checkbox"]').checked;
      if (name && selector) {
        fields[name] = { selector, attr, all };
      }
    });
    if (totalRows > 0 && Object.keys(fields).length === 0) {
      alert('Extraction is enabled but no fields have both a name and a selector set. Please fill them in or disable extraction.');
      return null;
    }
    if (Object.keys(fields).length) {
      payload.extract = { type: bExtractType, fields };
    }
  }

  // Shared headers
  const headerRows = getRowValues('b-headers-list');
  if (headerRows.length) {
    payload.headers = {};
    headerRows.forEach(([k, v]) => { if (k) payload.headers[k] = v; });
  }

  // Shared cookies
  const cookieRows = getRowValues('b-cookies-list');
  if (cookieRows.length) {
    payload.cookies = cookieRows
      .filter(([n]) => n)
      .map(([name, value, domain, path]) => ({
        name, value, domain: domain || undefined, path: path || undefined
      }));
  }

  return payload;
}

async function refreshBatchProxyPool() {
  const type = document.getElementById('b-proxy-type').value;
  const geoFields = document.getElementById('b-geo-fields');
  const poolField = document.getElementById('b-pool-id-field');
  const poolSelect = document.getElementById('b-proxy-pool-select');
  const poolInput = document.getElementById('b-proxy-pool');
  const hint = document.getElementById('b-pool-id-hint');
  const buyEl = ensureBuyButtonEl(poolField, 'b-buy-proxy');

  if (type === 'none') {
    geoFields.style.display = 'none';
    poolField.style.display = 'none';
    buyEl.innerHTML = '';
    return;
  }
  poolField.style.display = '';
  geoFields.style.display = type === 'res_rotating' ? '' : 'none';

  hint.textContent = 'loading...';
  poolSelect.style.display = 'none';
  poolInput.style.display = '';
  poolSelect.innerHTML = '';
  buyEl.innerHTML = '';

  const { ok, data } = await apiCall(`/api/v1/proxies/available?proxy_type=${encodeURIComponent(type)}`);
  if (!ok || !data) { hint.textContent = '(failed to load)'; return; }
  if (!data.configured) { hint.textContent = '(CyberYozh API key not set)'; return; }
  if (!data.items?.length) {
    hint.textContent = `(no purchased ${type.replace(/_/g, ' ')} proxies)`;
    poolSelect.style.display = 'none';
    poolInput.style.display = 'none';
    buyEl.innerHTML = renderBuyButton(type);
    return;
  }

  hint.textContent = `(${data.items.length} available)`;
  poolSelect.innerHTML = '<option value="">— select one —</option>' +
    data.items.map(p =>
      `<option value="${escapeHtml(p.id)}">${escapeHtml(p.id)} — ${escapeHtml(p.url || '(no url)')} ${p.expired ? '[expired]' : ''}</option>`
    ).join('');
  poolSelect.style.display = '';
  poolInput.style.display = 'none';
  poolInput.value = '';
}

document.getElementById('b-proxy-type').addEventListener('change', refreshBatchProxyPool);
document.getElementById('b-proxy-pool-select').addEventListener('change', () => {
  document.getElementById('b-proxy-pool').value = document.getElementById('b-proxy-pool-select').value;
});
refreshBatchProxyPool();

document.getElementById('btnCopyFromSingle').addEventListener('click', () => {
  const pairs = [
    ['s-render', 'b-render'],
    ['s-raw-html', 'b-raw-html'],
    ['s-screenshot', 'b-screenshot'],
    ['s-block-assets', 'b-block-assets'],
    ['s-stealth', 'b-stealth'],
    ['s-device', 'b-device'],
    ['s-wait-until', 'b-wait-until'],
    ['s-timeout', 'b-timeout'],
    ['s-wait-selector', 'b-wait-selector'],
    ['s-proxy-type', 'b-proxy-type'],
    ['s-proxy-pool', 'b-proxy-pool'],
    ['s-geo-country', 'b-geo-country'],
    ['s-geo-region', 'b-geo-region'],
    ['s-geo-city', 'b-geo-city'],
    ['s-extract-type', 'b-extract-type'],
  ];
  for (const [from, to] of pairs) {
    const src = document.getElementById(from);
    const dst = document.getElementById(to);
    if (!src || !dst) continue;
    if (src.type === 'checkbox') dst.checked = src.checked;
    else dst.value = src.value;
  }

  // Copy the extract field rows verbatim (name, selector, attr, all)
  const srcRows = document.querySelectorAll('#extract-fields .dynamic-row');
  const dstContainer = document.getElementById('b-extract-fields');
  dstContainer.innerHTML = '';
  srcRows.forEach(srcRow => {
    addExtractField('b-extract-fields');
    const newRow = dstContainer.querySelector('.dynamic-row:last-child');
    const srcInputs = srcRow.querySelectorAll('input[type="text"]');
    const dstInputs = newRow.querySelectorAll('input[type="text"]');
    dstInputs[0].value = srcInputs[0].value;
    dstInputs[1].value = srcInputs[1].value;
    newRow.querySelector('select').value = srcRow.querySelector('select').value;
    newRow.querySelector('input[type="checkbox"]').checked =
      srcRow.querySelector('input[type="checkbox"]').checked;
  });

  refreshBatchProxyPool();
});

document.getElementById('btnBatch').addEventListener('click', async () => {
  const raw = document.getElementById('b-urls').value;
  const urls = raw.split(/\r?\n/).map(normalizeUrl).filter(Boolean);

  if (!urls.length) { alert('Add at least one URL (one per line)'); return; }

  const shared = buildBatchSharedPayload();
  const pages = urls.map(url => ({ ...shared, url }));

  document.getElementById('batch-result').innerHTML = '';
  setStatus('batch-status', 'queued', `Submitting ${pages.length} pages...`);

  const { data, ok } = await apiCall('/api/v1/scrape/pages', {
    method: 'POST',
    body: JSON.stringify({ pages }),
  });

  if (!ok || !data?.job_id) {
    setStatus('batch-status', 'failed', 'Failed to submit batch');
    showResult('batch-result', data);
    return;
  }

  await loadServerConfig();
  pollJobBatch(data.job_id, 'batch-status', 'batch-result', pages.length, 'btnCancelBatch');
});

// ─── Jobs ─────────────────────────────────────────────────────────────────────
function updateJobCancelButton(jobId, status) {
  if (!jobId || !status) return;
  if (status === 'queued' || status === 'running') {
    setScrapeCancelButton('btnCancelJob', 'armed', jobId);
  } else {
    setScrapeCancelButton('btnCancelJob', 'done');
  }
}

document.getElementById('btnJobStatus').addEventListener('click', async () => {
  const jobId = document.getElementById('j-job-id').value.trim();
  if (!jobId) return;
  const { data } = await apiCall(`/api/v1/scrape/${jobId}`);
  showResult('jobs-result', data);
  if (data?.status) {
    addRecentJob(jobId, data.status);
    updateJobCancelButton(jobId, data.status);
  }
});

document.getElementById('btnJobResults').addEventListener('click', async () => {
  const jobId = document.getElementById('j-job-id').value.trim();
  if (!jobId) return;
  const { data } = await apiCall(`/api/v1/scrape/${jobId}/results`);
  showResult('jobs-result', data);
  if (data?.status) {
    addRecentJob(jobId, data.status);
    updateJobCancelButton(jobId, data.status);
  }
});

document.getElementById('btnClearJobs').addEventListener('click', () => {
  recentJobs = [];
  saveRecentJobs();
  renderRecentJobs();
});

// Render persisted jobs on load
renderRecentJobs();

// Reset MCP session whenever the target or its URL changes — sessions don't
// port between the two backends.
document.getElementById('scraperUrl').addEventListener('change', () => {
  mcpSessionId = null;
});
document.getElementById('crawlerUrl').addEventListener('change', () => {
  mcpSessionId = null;
});
document.getElementById('mcp-target').addEventListener('change', () => {
  mcpSessionId = null;
  const tools = document.getElementById('mcp-tools-list');
  if (tools) tools.innerHTML = '<span style="color:var(--text-muted)">Target changed — reload tools</span>';
});

// ─── Info tooltips (detached, fixed-positioned) ──────────────────────────────
document.querySelectorAll('.info-icon').forEach(icon => {
  const tooltip = icon.querySelector('.info-tooltip');
  if (!tooltip) return;
  document.body.appendChild(tooltip);

  const show = () => {
    const rect = icon.getBoundingClientRect();
    tooltip.classList.add('visible');
    // Measure, then position
    const tipRect = tooltip.getBoundingClientRect();
    let top = rect.bottom + 8;
    let left = rect.right - tipRect.width;
    // Keep within viewport
    if (left < 8) left = 8;
    if (left + tipRect.width > window.innerWidth - 8) left = window.innerWidth - tipRect.width - 8;
    if (top + tipRect.height > window.innerHeight - 8) top = rect.top - tipRect.height - 8;
    tooltip.style.top = `${top}px`;
    tooltip.style.left = `${left}px`;
  };
  const hide = () => tooltip.classList.remove('visible');

  icon.addEventListener('mouseenter', show);
  icon.addEventListener('mouseleave', hide);
  icon.addEventListener('focus', show);
  icon.addEventListener('blur', hide);
});

// ─── Proxy type dependent UI ──────────────────────────────────────────────────
const STATIC_PROXY_TYPES = new Set(['res_static', 'mobile', 'mobile_shared', 'dc_static']);

const BUY_PROXY_URLS = {
  res_rotating:  'https://app.cyberyozh.com/my-account/add-proxy/residential-rotating/',
  res_static:    'https://app.cyberyozh.com/my-account/add-proxy/residential/',
  mobile:        'https://app.cyberyozh.com/my-account/add-proxy/mobile/',
  mobile_shared: 'https://app.cyberyozh.com/my-account/add-proxy/mobile-shared/',
  dc_static:     'https://app.cyberyozh.com/my-account/add-proxy/datacenter/',
};

function renderBuyButton(proxyType) {
  const url = BUY_PROXY_URLS[proxyType];
  if (!url) return '';
  const label = proxyType.replace(/_/g, ' ');
  return `<a href="${url}" target="_blank" rel="noopener" style="display:inline-flex;align-items:center;gap:0.35rem;background:var(--neutral-900);color:var(--text-light);padding:0.5rem 0.9rem;border-radius:var(--radius-sm);text-decoration:none;font-size:13px;font-weight:500">Buy ${label} proxy ↗</a>`;
}

function ensureBuyButtonEl(fieldEl, id) {
  let el = document.getElementById(id);
  if (!el) {
    el = document.createElement('div');
    el.id = id;
    el.style.marginTop = '0.35rem';
    fieldEl.appendChild(el);
  }
  return el;
}

async function refreshProxyPool() {
  const type = document.getElementById('s-proxy-type').value;
  const geoFields = document.getElementById('geo-fields');
  const poolField = document.getElementById('pool-id-field');
  const poolSelect = document.getElementById('s-proxy-pool-select');
  const poolInput = document.getElementById('s-proxy-pool');
  const hint = document.getElementById('pool-id-hint');
  const buyEl = ensureBuyButtonEl(poolField, 's-buy-proxy');

  if (type === 'none') {
    geoFields.style.display = 'none';
    poolField.style.display = 'none';
    buyEl.innerHTML = '';
    return;
  }
  poolField.style.display = '';
  geoFields.style.display = type === 'res_rotating' ? '' : 'none';

  hint.textContent = 'loading...';
  poolSelect.style.display = 'none';
  poolInput.style.display = '';
  poolSelect.innerHTML = '';
  buyEl.innerHTML = '';

  const { ok, data } = await apiCall(`/api/v1/proxies/available?proxy_type=${encodeURIComponent(type)}`);
  if (!ok || !data) {
    hint.textContent = '(failed to load)';
    return;
  }
  if (!data.configured) {
    hint.textContent = '(CyberYozh API key not set — manual entry only)';
    return;
  }
  if (!data.items?.length) {
    hint.textContent = `(no purchased ${type.replace(/_/g, ' ')} proxies)`;
    poolSelect.style.display = 'none';
    poolInput.style.display = 'none';
    buyEl.innerHTML = renderBuyButton(type);
    return;
  }

  hint.textContent = `(${data.items.length} available)`;
  poolSelect.innerHTML = '<option value="">— select one —</option>' +
    data.items.map(p =>
      `<option value="${escapeHtml(p.id)}">${escapeHtml(p.id)} — ${escapeHtml(p.url || '(no url)')} ${p.expired ? '[expired]' : ''}</option>`
    ).join('');
  poolSelect.style.display = '';
  poolInput.style.display = 'none';
  poolInput.value = '';
}

document.getElementById('s-proxy-type').addEventListener('change', refreshProxyPool);
// Keep input and select in sync
document.getElementById('s-proxy-pool-select').addEventListener('change', () => {
  document.getElementById('s-proxy-pool').value = document.getElementById('s-proxy-pool-select').value;
});
refreshProxyPool();

// ─── MCP helpers ─────────────────────────────────────────────────────────────
function mcpTarget() {
  return document.getElementById('mcp-target')?.value || 'scraper';
}

async function mcpPost(body, sessionId) {
  const headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/event-stream',
  };
  if (sessionId) headers['mcp-session-id'] = sessionId;

  // Crawler has CORS enabled — call directly. Scraper goes through the tester
  // proxy via x-scraper-target header (established pattern).
  let url;
  if (mcpTarget() === 'crawler') {
    url = `${crawlerUrl()}/mcp`;
  } else {
    url = '/proxy/mcp';
    headers['x-scraper-target'] = scraperUrl();
  }

  const res = await fetch(url, { method: 'POST', headers, body: JSON.stringify(body) });
  const newSession = res.headers.get('mcp-session-id');
  const text = await res.text();

  let parsed = null;
  if (text.startsWith('data:')) {
    const lines = text.split('\n').filter(l => l.startsWith('data:'));
    for (const line of lines) {
      try { parsed = JSON.parse(line.slice(5).trim()); if (parsed?.result !== undefined || parsed?.error) break; }
      catch {}
    }
  } else {
    try { parsed = JSON.parse(text); } catch { parsed = text; }
  }

  return { parsed, newSession, rawText: text };
}

async function mcpInitSession() {
  const { parsed, newSession } = await mcpPost({
    jsonrpc: '2.0', id: 1,
    method: 'initialize',
    params: {
      protocolVersion: '2024-11-05',
      capabilities: {},
      clientInfo: { name: 'scraper-tester', version: '1.0' },
    },
  }, null);

  const sid = newSession;
  if (sid) {
    // Send initialized notification
    await mcpPost({ jsonrpc: '2.0', method: 'notifications/initialized', params: {} }, sid);
  }
  return { sid, serverInfo: parsed };
}

// ─── MCP ──────────────────────────────────────────────────────────────────────
function renderToolSchema(tool) {
  const el = document.getElementById('mcp-tool-schema');
  if (!el) return;
  const rootSchema = tool.inputSchema;
  if (!rootSchema || !rootSchema.properties || !Object.keys(rootSchema.properties).length) {
    el.style.display = 'none';
    el.innerHTML = '';
    return;
  }
  const defs = rootSchema.$defs || rootSchema.definitions || {};

  const resolveRef = (ref) => {
    if (!ref) return null;
    const parts = ref.split('/').filter(Boolean);
    // Expect something like #/$defs/ScrapeRequest or #/definitions/ScrapeRequest
    const name = parts[parts.length - 1];
    return defs[name] || null;
  };

  const typeLabel = (p) => {
    if (!p) return 'any';
    if (p.$ref) return p.$ref.split('/').pop();
    if (Array.isArray(p.type)) return p.type.join('|');
    if (p.type === 'array') {
      const item = p.items || {};
      return `array<${typeLabel(item)}>`;
    }
    if (p.type) return p.type;
    if (p.anyOf) return p.anyOf.map(typeLabel).join(' | ');
    return 'any';
  };

  const renderProps = (schema, required, depth = 0) => {
    if (schema.$ref) schema = resolveRef(schema.$ref) || {};
    const props = schema.properties || {};
    const req = new Set(required || schema.required || []);
    return Object.entries(props).map(([name, p]) => {
      const resolved = p.$ref ? resolveRef(p.$ref) : null;
      const effective = resolved || p;
      const isRequired = req.has(name);
      const type = typeLabel(p);
      const dflt = p.default !== undefined
        ? ` <span style="color:var(--text-tertiary)">= ${escapeHtml(JSON.stringify(p.default))}</span>`
        : '';
      const desc = p.description || effective.description;
      const descHtml = desc
        ? `<div style="color:var(--text-secondary);margin-top:0.2rem">${escapeHtml(desc)}</div>`
        : '';
      const indent = depth * 16;

      // Recurse into objects (one level via $ref or inline) and into array items
      let nested = '';
      const objSchema = resolved || (effective.type === 'object' ? effective : null);
      if (objSchema && objSchema.properties) {
        nested = `<div style="margin-left:1rem;margin-top:0.3rem;padding-left:0.6rem;border-left:2px solid var(--border)">${renderProps(objSchema, objSchema.required, depth + 1)}</div>`;
      } else if (effective.type === 'array' && effective.items) {
        const item = effective.items.$ref ? resolveRef(effective.items.$ref) : effective.items;
        if (item && item.properties) {
          nested = `<div style="margin-left:1rem;margin-top:0.3rem;padding-left:0.6rem;border-left:2px solid var(--border)"><div style="color:var(--text-tertiary);font-size:11px;text-transform:uppercase;letter-spacing:0.05em">each item</div>${renderProps(item, item.required, depth + 1)}</div>`;
        }
      }

      return `
        <div style="padding:0.5rem 0;border-bottom:1px solid var(--border);margin-left:${indent}px">
          <div>
            <b>${escapeHtml(name)}</b>${isRequired ? '<span style="color:var(--color-red);font-weight:600">*</span>' : ''}
            <span style="color:var(--color-blue);font-family:var(--mono);font-size:12px">${escapeHtml(type)}</span>${dflt}
          </div>
          ${descHtml}
          ${nested}
        </div>`;
    }).join('');
  };

  const rows = renderProps(rootSchema, rootSchema.required);
  const count = Object.keys(rootSchema.properties).length;

  el.style.display = 'block';
  el.innerHTML = `
    <details open style="background:var(--bg-secondary);border:1px solid var(--border);border-radius:12px;padding:0.6rem 0.85rem;margin:0.4rem 0">
      <summary class="details-summary" style="cursor:pointer;list-style:none;display:flex;align-items:center;gap:0.5rem;font-size:12px;color:var(--text-secondary)">
        <span class="details-chevron" style="display:inline-block;transition:transform 0.15s;font-size:10px">▶</span>
        <span>Schema (${count} top-level field${count === 1 ? '' : 's'})</span>
      </summary>
      <div style="margin-top:0.4rem;font-size:12px">${rows}</div>
    </details>`;
}

document.getElementById('btnListTools').addEventListener('click', async () => {
  const container = document.getElementById('mcp-tools-list');
  container.innerHTML = '<span style="color:var(--text-muted)">Initializing session...</span>';

  try {
    const { sid, serverInfo } = await mcpInitSession();
    mcpSessionId = sid;

    container.innerHTML = '<span style="color:var(--text-muted)">Loading tools...</span>';

    const { parsed } = await mcpPost({
      jsonrpc: '2.0', id: 2,
      method: 'tools/list',
      params: {},
    }, mcpSessionId);

    const tools = parsed?.result?.tools || [];

    if (!tools.length) {
      container.innerHTML = '<span style="color:var(--text-muted)">No tools found</span>';
      showResult('mcp-result', parsed);
      return;
    }

    container.innerHTML = `<span style="color:var(--success);font-size:12px">Session: ${escapeHtml(mcpSessionId || 'stateless')} — ${tools.length} tools</span>`;
    tools.forEach(tool => {
      const div = document.createElement('div');
      div.className = 'mcp-tool';
      div.innerHTML = `
        <div class="mcp-tool-name">${escapeHtml(tool.name)}</div>
        <div class="mcp-tool-desc">${escapeHtml(tool.description || '')}</div>`;
      div.addEventListener('click', () => {
        document.getElementById('mcp-tool-name').value = tool.name;
        renderToolSchema(tool);
        if (tool.inputSchema?.properties) {
          const template = {};
          Object.keys(tool.inputSchema.properties).forEach(k => {
            template[k] = tool.inputSchema.properties[k].default ?? '';
          });
          document.getElementById('mcp-tool-args').value = JSON.stringify(template, null, 2);
        }
      });
      container.appendChild(div);
    });
  } catch (e) {
    container.innerHTML = `<span style="color:var(--error)">${escapeHtml(e.message)}</span>`;
  }
});

document.getElementById('btnCallTool').addEventListener('click', async () => {
  const toolName = document.getElementById('mcp-tool-name').value.trim();
  if (!toolName) { alert('Enter tool name'); return; }

  let args = {};
  try {
    const raw = document.getElementById('mcp-tool-args').value.trim();
    if (raw) args = JSON.parse(raw);
  } catch { alert('Invalid JSON in arguments'); return; }

  showResult('mcp-result', '');
  document.getElementById('mcp-result').innerHTML =
    '<span style="color:var(--text-muted)">Calling tool...</span>';

  try {
    // Init session if not exists
    if (!mcpSessionId) {
      const { sid } = await mcpInitSession();
      mcpSessionId = sid;
    }

    const { parsed, rawText } = await mcpPost({
      jsonrpc: '2.0',
      id: Date.now(),
      method: 'tools/call',
      params: { name: toolName, arguments: args },
    }, mcpSessionId);

    showResult('mcp-result', parsed || rawText);
  } catch (e) {
    showResult('mcp-result', { error: e.message });
  }
});

// ═══════════════════════════════════════════════════════════════════════════
// CRAWLER TAB
// ═══════════════════════════════════════════════════════════════════════════
function crawlerUrl() {
  return document.getElementById('crawlerUrl').value.replace(/\/$/, '');
}

async function crawlerCall(path, options = {}) {
  // Direct call to the crawler service (CORS-enabled). Bypasses the tester proxy
  // because EventSource can't send custom headers (we'd need x-crawler-target).
  const res = await fetch(`${crawlerUrl()}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  });
  const text = await res.text();
  try { return { ok: res.ok, status: res.status, data: JSON.parse(text) }; }
  catch { return { ok: res.ok, status: res.status, data: text }; }
}

// ─── Crawler health check ────────────────────────────────────────────────────
document.getElementById('btnCrawlerHealth').addEventListener('click', async () => {
  const badge = document.getElementById('crawlerHealthStatus');
  badge.className = 'badge';
  badge.textContent = 'checking…';
  try {
    const { data, ok } = await crawlerCall('/api/v1/health');
    if (ok && data?.status === 'ok') {
      badge.className = 'badge ok';
      badge.textContent = data.scraper_reachable ? 'ok' : 'no scraper';
    } else {
      badge.className = 'badge error';
      badge.textContent = 'error';
    }
  } catch {
    badge.className = 'badge error';
    badge.textContent = 'unreachable';
  }
});

// ─── Crawler dynamic rows ────────────────────────────────────────────────────
document.getElementById('btnAddHeaderCrawler').addEventListener('click', () =>
  addDynamicRow('c-headers-list', ['Header name', 'Value']));

document.getElementById('btnAddCookieCrawler').addEventListener('click', () =>
  addDynamicRow('c-cookies-list', ['name', 'value', 'domain', 'path']));

document.getElementById('btnAddFieldCrawler').addEventListener('click', () =>
  addExtractField('c-extract-fields'));

// ─── Crawler header presets ──────────────────────────────────────────────────
function applyHeaderPresetCrawler(presetKey) {
  const headers = HEADER_PRESETS[presetKey];
  if (!headers) return;
  document.getElementById('c-headers-list').innerHTML = '';
  Object.entries(headers).forEach(([k, v]) => {
    addDynamicRow('c-headers-list', ['Header name', 'Value']);
    const rows = document.querySelectorAll('#c-headers-list .dynamic-row');
    const last = rows[rows.length - 1];
    const inputs = last.querySelectorAll('input');
    inputs[0].value = k;
    inputs[1].value = v;
  });
  const deviceSel = document.getElementById('c-device');
  if (deviceSel) deviceSel.value = MOBILE_PRESETS.has(presetKey) ? 'mobile' : 'desktop';
}

document.getElementById('btnApplyPresetCrawler').addEventListener('click', () => {
  const val = document.getElementById('c-header-preset').value;
  if (val) applyHeaderPresetCrawler(val);
});

document.getElementById('btnClearHeadersCrawler').addEventListener('click', () => {
  document.getElementById('c-headers-list').innerHTML = '';
  document.getElementById('c-header-preset').value = '';
});

// ─── Crawler proxy pool (copy of refreshBatchProxyPool with c-* prefix) ──────
async function refreshCrawlerProxyPool() {
  const type = document.getElementById('c-proxy-type').value;
  const geoFields = document.getElementById('c-geo-fields');
  const poolField = document.getElementById('c-pool-id-field');
  const poolSelect = document.getElementById('c-proxy-pool-select');
  const poolInput = document.getElementById('c-proxy-pool');
  const hint = document.getElementById('c-pool-id-hint');
  const buyEl = ensureBuyButtonEl(poolField, 'c-buy-proxy');

  if (type === 'none') {
    geoFields.style.display = 'none';
    poolField.style.display = 'none';
    buyEl.innerHTML = '';
    return;
  }
  poolField.style.display = '';
  geoFields.style.display = type === 'res_rotating' ? '' : 'none';

  hint.textContent = 'loading...';
  poolSelect.style.display = 'none';
  poolInput.style.display = '';
  poolSelect.innerHTML = '';
  buyEl.innerHTML = '';

  const { ok, data } = await apiCall(`/api/v1/proxies/available?proxy_type=${encodeURIComponent(type)}`);
  if (!ok || !data) { hint.textContent = '(failed to load)'; return; }
  if (!data.configured) { hint.textContent = '(CyberYozh API key not set)'; return; }
  if (!data.items?.length) {
    hint.textContent = `(no purchased ${type.replace(/_/g, ' ')} proxies)`;
    poolSelect.style.display = 'none';
    poolInput.style.display = 'none';
    buyEl.innerHTML = renderBuyButton(type);
    return;
  }
  hint.textContent = `(${data.items.length} available)`;
  poolSelect.innerHTML = '<option value="">— select one —</option>' +
    data.items.map(p =>
      `<option value="${escapeHtml(p.id)}">${escapeHtml(p.id)} — ${escapeHtml(p.url || '(no url)')} ${p.expired ? '[expired]' : ''}</option>`
    ).join('');
  poolSelect.style.display = '';
  poolInput.style.display = 'none';
  poolInput.value = '';
}
function updateCrawlerProxyWarning() {
  const warn = document.getElementById('c-proxy-warning');
  if (!warn) return;
  warn.style.display = document.getElementById('c-proxy-type').value === 'none' ? '' : 'none';
}
document.getElementById('c-proxy-type').addEventListener('change', () => {
  refreshCrawlerProxyPool();
  updateCrawlerProxyWarning();
});
document.getElementById('c-proxy-pool-select').addEventListener('change', () => {
  document.getElementById('c-proxy-pool').value = document.getElementById('c-proxy-pool-select').value;
});
refreshCrawlerProxyPool();
updateCrawlerProxyWarning();

// ─── Crawl Proxy pool (independent block, cp-* prefix) ───────────────────────
async function refreshCrawlProxyPool() {
  const type = document.getElementById('cp-proxy-type').value;
  const geoFields = document.getElementById('cp-geo-fields');
  const poolField = document.getElementById('cp-pool-id-field');
  const poolSelect = document.getElementById('cp-proxy-pool-select');
  const poolInput = document.getElementById('cp-proxy-pool');
  const hint = document.getElementById('cp-pool-id-hint');
  const buyEl = ensureBuyButtonEl(poolField, 'cp-buy-proxy');

  if (type === 'none') {
    geoFields.style.display = 'none';
    poolField.style.display = 'none';
    buyEl.innerHTML = '';
    return;
  }
  poolField.style.display = '';
  geoFields.style.display = type === 'res_rotating' ? '' : 'none';

  hint.textContent = 'loading...';
  poolSelect.style.display = 'none';
  poolInput.style.display = '';
  poolSelect.innerHTML = '';
  buyEl.innerHTML = '';

  const { ok, data } = await apiCall(`/api/v1/proxies/available?proxy_type=${encodeURIComponent(type)}`);
  if (!ok || !data) { hint.textContent = '(failed to load)'; return; }
  if (!data.configured) { hint.textContent = '(CyberYozh API key not set)'; return; }
  if (!data.items?.length) {
    hint.textContent = `(no purchased ${type.replace(/_/g, ' ')} proxies)`;
    poolSelect.style.display = 'none';
    poolInput.style.display = 'none';
    buyEl.innerHTML = renderBuyButton(type);
    return;
  }
  hint.textContent = `(${data.items.length} available)`;
  poolSelect.innerHTML = '<option value="">— select one —</option>' +
    data.items.map(p =>
      `<option value="${escapeHtml(p.id)}">${escapeHtml(p.id)} — ${escapeHtml(p.url || '(no url)')} ${p.expired ? '[expired]' : ''}</option>`
    ).join('');
  poolSelect.style.display = '';
  poolInput.style.display = 'none';
  poolInput.value = '';
}
function updateCrawlProxyWarning() {
  const warn = document.getElementById('cp-proxy-warning');
  if (!warn) return;
  warn.style.display = document.getElementById('cp-proxy-type').value === 'none' ? '' : 'none';
}
document.getElementById('cp-proxy-type').addEventListener('change', () => {
  refreshCrawlProxyPool();
  updateCrawlProxyWarning();
});
document.getElementById('cp-proxy-pool-select').addEventListener('change', () => {
  document.getElementById('cp-proxy-pool').value = document.getElementById('cp-proxy-pool-select').value;
});
refreshCrawlProxyPool();
updateCrawlProxyWarning();

// ─── Enable-scraping toggle: show/hide the scraping section ─────────────────
function applyCrawlerScrapingVisibility() {
  const section = document.getElementById('c-scraping-section');
  if (!section) return;
  // Restore the intended display:flex explicitly — setting '' after 'none'
  // would not bring back the inline style originally set in HTML.
  section.style.display = document.getElementById('c-enable-scraping').checked ? 'flex' : 'none';
}
document.getElementById('c-enable-scraping').addEventListener('change', applyCrawlerScrapingVisibility);
applyCrawlerScrapingVisibility();

// ─── Build Crawl payload ─────────────────────────────────────────────────────
function buildCrawlPayload() {
  const seed = normalizeUrl(document.getElementById('c-seed-url').value);
  if (!seed) { alert('Seed URL is required'); return null; }

  const scope = {
    mode: document.getElementById('c-scope-mode').value,
    max_depth: Number(document.getElementById('c-max-depth').value) || 3,
    max_pages: Number(document.getElementById('c-max-pages').value) || 500,
    per_domain_rps: Number(document.getElementById('c-rps').value) || 1.0,
    per_domain_concurrency: Number(document.getElementById('c-concurrency').value) || 1,
    include_patterns: document.getElementById('c-include').value.split(/\r?\n/).map(s => s.trim()).filter(Boolean),
    exclude_patterns: document.getElementById('c-exclude').value.split(/\r?\n/).map(s => s.trim()).filter(Boolean),
  };

  const scrape = {
    render: document.getElementById('c-render').checked,
    wait_until: document.getElementById('c-wait-until').value,
    device: document.getElementById('c-device').value,
    timeout_ms: Number(document.getElementById('c-timeout').value) || 30000,
    block_assets: document.getElementById('c-block-assets').checked,
    screenshot: document.getElementById('c-screenshot').checked,
    stealth: document.getElementById('c-stealth').checked,
    proxy_type: document.getElementById('c-proxy-type').value,
  };
  const waitSelector = document.getElementById('c-wait-selector').value.trim();
  if (waitSelector) scrape.wait_for_selector = waitSelector;

  const poolId = document.getElementById('c-proxy-pool').value.trim();
  if (poolId) scrape.proxy_pool_id = poolId;
  if (scrape.proxy_type !== 'none' && !poolId) {
    alert(`Select a Pool ID for proxy type "${scrape.proxy_type}". If none available, use the "Buy" button on the Scrape Page tab.`);
    return null;
  }
  if (scrape.proxy_type === 'res_rotating') {
    const cc = document.getElementById('c-geo-country').value.trim();
    const region = document.getElementById('c-geo-region').value.trim();
    const city = document.getElementById('c-geo-city').value.trim();
    if (cc || region || city) {
      scrape.proxy_geo = {};
      if (cc) scrape.proxy_geo.country_code = cc.toUpperCase();
      if (region) scrape.proxy_geo.region = region;
      if (city) scrape.proxy_geo.city = city;
    }
  }

  const extractType = document.getElementById('c-extract-type').value;
  if (extractType) {
    const fieldRows = document.querySelectorAll('#c-extract-fields .dynamic-row');
    const fields = {};
    let totalRows = 0;
    fieldRows.forEach(row => {
      totalRows++;
      const inputs = row.querySelectorAll('input[type="text"]');
      const name = inputs[0].value.trim();
      const selector = inputs[1].value.trim();
      const attr = row.querySelector('select').value;
      const all = row.querySelector('input[type="checkbox"]').checked;
      if (name && selector) fields[name] = { selector, attr, all };
    });
    if (totalRows > 0 && Object.keys(fields).length === 0) {
      alert('Extraction is enabled but no fields have both a name and a selector set. Please fill them in or disable extraction.');
      return null;
    }
    if (Object.keys(fields).length) scrape.extract = { type: extractType, fields };
  }

  const headerRows = getRowValues('c-headers-list');
  if (headerRows.length) {
    scrape.headers = {};
    headerRows.forEach(([k, v]) => { if (k) scrape.headers[k] = v; });
  }

  const cookieRows = getRowValues('c-cookies-list');
  if (cookieRows.length) {
    scrape.cookies = cookieRows.filter(([n]) => n).map(([name, value, domain, path]) => ({
      name, value, domain: domain || undefined, path: path || undefined,
    }));
  }

  // Separate crawl proxy (used when enable_scraping=false)
  let crawl_proxy = null;
  const cpType = document.getElementById('cp-proxy-type').value;
  if (cpType !== 'none') {
    const cpPoolId = document.getElementById('cp-proxy-pool').value.trim();
    if (!cpPoolId) {
      alert(`Crawl proxy: select a Pool ID for "${cpType}" or switch the type back to "none".`);
      return null;
    }
    crawl_proxy = { proxy_type: cpType, proxy_pool_id: cpPoolId };
    if (cpType === 'res_rotating') {
      const cc = document.getElementById('cp-geo-country').value.trim();
      const region = document.getElementById('cp-geo-region').value.trim();
      const city = document.getElementById('cp-geo-city').value.trim();
      if (cc || region || city) {
        crawl_proxy.proxy_geo = {};
        if (cc) crawl_proxy.proxy_geo.country_code = cc.toUpperCase();
        if (region) crawl_proxy.proxy_geo.region = region;
        if (city) crawl_proxy.proxy_geo.city = city;
      }
    }
  }

  return {
    seed_url: seed,
    scope,
    scrape_options: scrape,
    crawl_proxy,
    enable_scraping: document.getElementById('c-enable-scraping').checked,
  };
}

// ─── Crawler state + SSE + rendering ─────────────────────────────────────────
let currentCrawlId = null;
let currentCrawlSource = null;
let crawlCancelState = 'ready';    // ready | soft-sent | hard-sent
const crawlPages = new Map();      // url → page record
const crawlPagesOrder = [];         // insertion order for rendering

function setCrawlCancelButton(state) {
  const btn = document.getElementById('btnCancelCrawl');
  crawlCancelState = state;
  btn.classList.remove('btn-primary');
  btn.classList.add('btn-secondary');
  if (state === 'ready' || state === 'done') {
    btn.disabled = true;
    btn.style.display = 'none';
    btn.textContent = 'Cancel';
  } else if (state === 'armed') {
    btn.disabled = false;
    btn.style.display = '';
    btn.textContent = 'Cancel';
  } else if (state === 'soft-sent') {
    btn.disabled = false;
    btn.style.display = '';
    btn.classList.remove('btn-secondary');
    btn.classList.add('btn-primary');
    btn.textContent = 'Force stop';
  } else if (state === 'hard-sent') {
    btn.disabled = true;
    btn.style.display = '';
    btn.textContent = 'Force-stopping…';
  }
}

function resetCrawlUI() {
  crawlPages.clear();
  crawlPagesOrder.length = 0;
  document.getElementById('crawl-result').innerHTML =
    '<span class="placeholder">Waiting for crawler...</span>';
}

function renderCrawlStats(stats, jobId, status) {
  const el = document.getElementById('crawl-status');
  el.className = `status-bar ${status === 'running' ? 'running' : status === 'done' ? 'done' : status === 'cancelled' ? 'failed' : 'queued'}`;
  const spinner = status === 'running' ? '<div class="spinner"></div>' : '';
  const cap = document.getElementById('c-max-pages').value || '?';
  el.innerHTML = `
    ${spinner}
    <div style="flex:1;display:flex;flex-direction:column;gap:4px">
      <div style="display:flex;gap:0.6rem;align-items:center;font-size:12px;flex-wrap:wrap">
        <span><b>${jobId || '—'}</b></span>
        <span>·</span>
        <span>status: <b>${status}</b></span>
        <span>·</span>
        <span>visited: <b>${stats.visited ?? 0}</b> / ${cap}</span>
        <span>·</span>
        <span>queued: <b>${stats.queued ?? 0}</b></span>
        <span>·</span>
        <span>failed: <b>${stats.failed ?? 0}</b></span>
        <span>·</span>
        <span>dedup: ${stats.dedup_skipped ?? 0}</span>
        <span>·</span>
        <span>out-of-scope: ${stats.out_of_scope ?? 0}</span>
        <span>·</span>
        <span>retries: ${stats.retries_total ?? 0}</span>
      </div>
    </div>`;
}

function buildSiteMapText() {
  // Group by parent_url, preserve insertion order under each parent.
  const childrenOf = new Map();
  const roots = [];
  for (const url of crawlPagesOrder) {
    const p = crawlPages.get(url);
    const parent = p.parent_url;
    if (!parent || !crawlPages.has(parent)) {
      roots.push(p);
    } else {
      if (!childrenOf.has(parent)) childrenOf.set(parent, []);
      childrenOf.get(parent).push(p);
    }
  }
  const lines = [];
  function render(p, prefix, branchPrefix) {
    const tag = p.error ? 'ERR' : (p.status_code ?? '—');
    lines.push(`${prefix}${p.url}  [d=${p.depth}, ${tag}, ${p.took_ms}ms]`);
    const kids = childrenOf.get(p.url) || [];
    kids.forEach((child, i) => {
      const last = i === kids.length - 1;
      render(child, branchPrefix + (last ? '└── ' : '├── '), branchPrefix + (last ? '    ' : '│   '));
    });
  }
  roots.forEach((root, i) => {
    const last = i === roots.length - 1;
    render(root, '', last ? '    ' : '│   ');
  });
  return lines.join('\n');
}

function renderSiteMapBlock(containerEl, openKeys) {
  const wrap = document.createElement('details');
  wrap.dataset.key = 'sitemap';
  // Default open on first render, remember user's toggle after
  if (openKeys.size === 0 || openKeys.has('sitemap')) wrap.setAttribute('open', '');
  wrap.style.cssText = 'margin-bottom:0.75rem';

  const summary = document.createElement('summary');
  summary.className = 'details-summary';
  summary.style.cssText = 'cursor:pointer;padding:0.5rem 0.75rem;background:var(--bg-secondary);border:1px solid var(--border);border-radius:var(--radius-sm);font-size:12px;color:var(--text-secondary);display:flex;align-items:center;gap:0.5rem;list-style:none';
  summary.innerHTML = `<span class="details-chevron" style="display:inline-block;transition:transform 0.15s;font-size:10px">▶</span><span style="flex:1"><b style="color:var(--text-primary)">Site Map</b> (${crawlPagesOrder.length} pages)</span>`;

  const btnCopy = document.createElement('button');
  btnCopy.textContent = 'Copy';
  btnCopy.className = 'btn-secondary btn-sm';
  btnCopy.addEventListener('click', async (e) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(buildSiteMapText());
      btnCopy.textContent = 'Copied!';
      setTimeout(() => { btnCopy.textContent = 'Copy'; }, 1200);
    } catch {
      btnCopy.textContent = 'Failed';
    }
  });
  summary.appendChild(btnCopy);
  wrap.appendChild(summary);

  const pre = document.createElement('pre');
  pre.id = 'sitemap-text';
  pre.style.cssText = 'max-height:320px;overflow:auto;background:var(--bg-primary);border:1px solid var(--border);border-top:none;padding:0.75rem;font-size:11px;line-height:1.5;border-radius:0 0 var(--radius-sm) var(--radius-sm);color:var(--text-primary);white-space:pre;margin:0';
  pre.textContent = buildSiteMapText();
  wrap.appendChild(pre);
  containerEl.appendChild(wrap);
}

function renderCrawlPages(enableScraping) {
  const el = document.getElementById('crawl-result');
  if (!crawlPagesOrder.length) {
    el.innerHTML = '<span class="placeholder">No pages yet...</span>';
    return;
  }

  const openKeys = new Set(
    Array.from(el.querySelectorAll('details[open][data-key]')).map(d => d.dataset.key)
  );
  const prevScroll = el.scrollTop;

  el.innerHTML = '';
  renderSiteMapBlock(el, openKeys);

  // Compact mode (enable_scraping=false): single table
  if (!enableScraping) {
    const rows = crawlPagesOrder.map(url => {
      const p = crawlPages.get(url);
      const statusColor = p.error ? 'var(--color-red)' : (p.status_code && p.status_code < 400 ? 'var(--color-green)' : 'var(--color-red)');
      return `
        <tr>
          <td style="font-family:var(--mono);font-size:11px;max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(p.url)}">${escapeHtml(p.url)}</td>
          <td style="font-family:var(--mono);font-size:11px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-secondary)" title="${escapeHtml(p.parent_url || '')}">${escapeHtml(p.parent_url || '—')}</td>
          <td style="text-align:center">${p.depth}</td>
          <td style="text-align:center;color:${statusColor}"><b>${p.error ? 'ERR' : (p.status_code ?? '—')}</b></td>
          <td style="text-align:right;color:var(--text-secondary)">${p.took_ms}ms</td>
        </tr>`;
    }).join('');
    const tableWrap = document.createElement('div');
    tableWrap.innerHTML = `
      <table style="width:100%;border-collapse:collapse;font-size:12px">
        <thead>
          <tr style="background:var(--bg-secondary);color:var(--text-secondary)">
            <th style="text-align:left;padding:0.4rem 0.5rem">URL</th>
            <th style="text-align:left;padding:0.4rem 0.5rem">Parent</th>
            <th style="padding:0.4rem 0.5rem">Depth</th>
            <th style="padding:0.4rem 0.5rem">Status</th>
            <th style="text-align:right;padding:0.4rem 0.5rem">Took</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
    el.appendChild(tableWrap);
    el.scrollTop = prevScroll;
    return;
  }

  // Rich mode (enable_scraping=true): reuse showResult-style per-page blocks
  crawlPagesOrder.forEach((url, i) => {
    const p = crawlPages.get(url);
    const sr = p.scrape_response || {};
    const meta = sr.meta || {};
    const badge = document.createElement('div');
    badge.style.cssText = 'margin:0.75rem 0 0.4rem;padding:0.5rem 0.75rem;background:var(--bg-secondary);border:1px solid var(--border);border-radius:var(--radius-sm);font-size:12px;color:var(--text-secondary)';
    const errBadge = p.error ? ` &nbsp;|&nbsp; <b style="color:var(--color-red)">ERROR</b>` : '';
    badge.innerHTML = `
      <div style="display:flex;gap:0.5rem;align-items:center;flex-wrap:wrap">
        <b style="color:var(--text-primary)">#${i+1}</b>
        <span style="font-family:var(--mono);font-size:11px;max-width:520px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(p.url)}">${escapeHtml(p.url)}</span>
        <span>depth: <b>${p.depth}</b></span>
        <span>status: <b style="color:var(--color-green)">${p.status_code ?? '—'}</b></span>
        <span>took: ${p.took_ms}ms</span>${errBadge}
      </div>
      <div style="margin-top:2px;font-size:11px">parent: <code>${escapeHtml(p.parent_url || '—')}</code></div>`;
    el.appendChild(badge);

    if (p.error) {
      const err = document.createElement('pre');
      err.style.cssText = 'color:var(--color-red);padding:0.5rem;background:var(--bg-primary);border:1px solid var(--border);border-radius:var(--radius-sm);font-size:12px;margin:0 0 0.5rem 0';
      err.textContent = p.error;
      el.appendChild(err);
      return;
    }

    // Extracted data
    if (sr.data && typeof sr.data === 'object' && Object.keys(sr.data).length > 0) {
      const wrap = document.createElement('details');
      wrap.dataset.key = `data-${p.url}`;
      if (openKeys.has(wrap.dataset.key) || openKeys.size === 0) wrap.setAttribute('open', '');
      wrap.style.cssText = 'margin-bottom:0.5rem';
      const fieldCount = Object.keys(sr.data).length;
      wrap.innerHTML = `<summary class="details-summary" style="cursor:pointer;padding:0.5rem 0.75rem;background:var(--bg-secondary);border:1px solid var(--border);border-radius:var(--radius-sm);font-size:12px;color:var(--text-secondary);display:flex;align-items:center;gap:0.5rem;list-style:none"><span class="details-chevron" style="display:inline-block;transition:transform 0.15s;font-size:10px">▶</span><span>Extracted Data (${fieldCount} field${fieldCount === 1 ? '' : 's'})</span></summary>`;
      const pre = document.createElement('pre');
      pre.style.cssText = 'max-height:300px;overflow:auto;background:var(--bg-primary);border:1px solid var(--border);border-top:none;padding:0.75rem;font-size:12px;border-radius:0 0 var(--radius-sm) var(--radius-sm);color:var(--text-primary);white-space:pre-wrap;word-break:break-word';
      pre.innerHTML = syntaxHighlight(sr.data);
      wrap.appendChild(pre);
      el.appendChild(wrap);
    }

    // Raw HTML (only if user requested it in c-raw-html)
    if (sr.raw_html && document.getElementById('c-raw-html').checked) {
      const wrap = document.createElement('details');
      wrap.dataset.key = `html-${p.url}`;
      if (openKeys.has(wrap.dataset.key)) wrap.setAttribute('open', '');
      wrap.style.cssText = 'margin-bottom:0.5rem';
      wrap.innerHTML = `<summary class="details-summary" style="cursor:pointer;padding:0.5rem 0.75rem;background:var(--bg-secondary);border:1px solid var(--border);border-radius:var(--radius-sm);font-size:12px;color:var(--text-secondary);display:flex;align-items:center;gap:0.5rem;list-style:none"><span class="details-chevron" style="display:inline-block;transition:transform 0.15s;font-size:10px">▶</span><span>Raw HTML (${sr.raw_html.length} chars)</span></summary>`;
      const code = document.createElement('pre');
      code.style.cssText = 'max-height:300px;overflow:auto;background:var(--bg-primary);border:1px solid var(--border);border-top:none;padding:0.75rem;font-size:11px;border-radius:0 0 var(--radius-sm) var(--radius-sm);color:var(--text-primary)';
      code.textContent = sr.raw_html;
      wrap.appendChild(code);
      el.appendChild(wrap);
    }

    // Screenshot
    if (sr.screenshot_base64) {
      const wrap = document.createElement('details');
      wrap.dataset.key = `shot-${p.url}`;
      if (openKeys.has(wrap.dataset.key)) wrap.setAttribute('open', '');
      wrap.style.cssText = 'margin-bottom:0.5rem';
      wrap.innerHTML = `<summary class="details-summary" style="cursor:pointer;padding:0.5rem 0.75rem;background:var(--bg-secondary);border:1px solid var(--border);border-radius:var(--radius-sm);font-size:12px;color:var(--text-secondary);display:flex;align-items:center;gap:0.5rem;list-style:none"><span class="details-chevron" style="display:inline-block;transition:transform 0.15s;font-size:10px">▶</span><span>Screenshot</span></summary>`;
      const img = document.createElement('img');
      img.src = `data:image/png;base64,${sr.screenshot_base64}`;
      img.style.cssText = 'width:100%;display:block;border:1px solid var(--border);border-top:none;border-radius:0 0 var(--radius-sm) var(--radius-sm)';
      wrap.appendChild(img);
      el.appendChild(wrap);
    }
  });
  el.scrollTop = prevScroll;
}

// Throttle re-renders so 500 pages don't tank the UI
let renderScheduled = false;
let renderEnableScraping = false;
function scheduleRender() {
  if (renderScheduled) return;
  renderScheduled = true;
  requestAnimationFrame(() => {
    renderScheduled = false;
    renderCrawlPages(renderEnableScraping);
  });
}

// ─── Submit crawl + consume SSE ──────────────────────────────────────────────
document.getElementById('btnCrawl').addEventListener('click', async () => {
  const payload = buildCrawlPayload();
  if (!payload) return;

  // Close previous stream if any
  if (currentCrawlSource) { try { currentCrawlSource.close(); } catch {} currentCrawlSource = null; }

  resetCrawlUI();
  renderEnableScraping = !!payload.enable_scraping;

  const el = document.getElementById('crawl-status');
  el.className = 'status-bar queued';
  el.innerHTML = '<div class="spinner"></div><span>Submitting crawl...</span>';

  const { ok, data, status } = await crawlerCall('/api/v1/crawl', {
    method: 'POST',
    body: JSON.stringify(payload),
  });

  if (!ok || !data?.job_id) {
    el.className = 'status-bar failed';
    el.innerHTML = `<span>Failed to submit: ${escapeHtml(JSON.stringify(data).slice(0, 300))}</span>`;
    return;
  }

  currentCrawlId = data.job_id;
  setCrawlCancelButton('armed');
  renderCrawlStats({}, currentCrawlId, 'queued');

  // Open SSE stream directly to the crawler (CORS-enabled, EventSource can't send headers)
  const source = new EventSource(`${crawlerUrl()}/api/v1/crawl/${currentCrawlId}/events`);
  currentCrawlSource = source;

  source.addEventListener('stats', (e) => {
    try {
      const ev = JSON.parse(e.data);
      renderCrawlStats(ev.stats, currentCrawlId, 'running');
    } catch {}
  });

  source.addEventListener('page', (e) => {
    try {
      const ev = JSON.parse(e.data);
      const p = ev.page;
      if (!crawlPages.has(p.url)) crawlPagesOrder.push(p.url);
      crawlPages.set(p.url, p);
      scheduleRender();
    } catch {}
  });

  source.addEventListener('page_error', (e) => {
    try {
      const ev = JSON.parse(e.data);
      console.warn('crawl page_error', ev);
    } catch {}
  });

  source.addEventListener('error', (e) => {
    // EventSource-level transport error (connection dropped, CORS, etc).
    // Browser auto-reconnects — we only surface if the stream looks dead.
    if (source.readyState === EventSource.CLOSED) {
      const el = document.getElementById('crawl-status');
      el.className = 'status-bar failed';
      el.innerHTML = '<span>Stream closed (transport error)</span>';
    }
  });

  source.addEventListener('done', (e) => {
    try {
      const ev = JSON.parse(e.data);
      renderCrawlStats(ev.stats, currentCrawlId, ev.status || 'done');
    } catch {}
    source.close();
    currentCrawlSource = null;
    setCrawlCancelButton('done');
    scheduleRender();
  });

  source.addEventListener('cancelled', (e) => {
    try {
      const ev = JSON.parse(e.data);
      renderCrawlStats(ev.stats, currentCrawlId, 'cancelled');
    } catch {}
    source.close();
    currentCrawlSource = null;
    setCrawlCancelButton('done');
    scheduleRender();
  });
});

document.getElementById('btnCancelCrawl').addEventListener('click', async () => {
  if (!currentCrawlId) return;

  if (crawlCancelState === 'armed') {
    // First click — soft cancel. Workers stop dispatching new URLs, in-flight
    // requests drain. Button becomes "Force stop" for a second click.
    setCrawlCancelButton('soft-sent');
    await crawlerCall(`/api/v1/crawl/${currentCrawlId}?hard=false`, { method: 'DELETE' });
  } else if (crawlCancelState === 'soft-sent') {
    // Second click — hard cancel. Task.cancel() propagates through httpx and
    // drops the in-flight fetch. The scraper-side job is orphaned (no cancel
    // API upstream yet) but the crawler unblocks immediately.
    setCrawlCancelButton('hard-sent');
    await crawlerCall(`/api/v1/crawl/${currentCrawlId}?hard=true`, { method: 'DELETE' });
  }
});

// ═══════════════════════════════════════════════════════════════════════════
// CUSTOM SELECT — replaces the native dropdown popup with a styled panel.
// Keeps the underlying <select> intact so buildPayload() and value reads work.
// ═══════════════════════════════════════════════════════════════════════════
function enhanceSelect(select) {
  if (select.dataset.enhanced) return;
  // Skip selects we don't want to enhance (opt-out via data-no-enhance)
  if (select.hasAttribute('data-no-enhance')) return;
  select.dataset.enhanced = '1';

  const wrap = document.createElement('div');
  wrap.className = 'custom-select';
  select.parentNode.insertBefore(wrap, select);
  wrap.appendChild(select);

  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'custom-select-trigger';
  trigger.innerHTML = `
    <span class="custom-select-label"></span>
    <svg class="custom-select-chevron" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M5 7.5L10 12.5L15 7.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
  wrap.appendChild(trigger);

  const dropdown = document.createElement('ul');
  dropdown.className = 'custom-select-dropdown';
  dropdown.style.display = 'none';
  dropdown.setAttribute('role', 'listbox');
  wrap.appendChild(dropdown);

  const labelEl = trigger.querySelector('.custom-select-label');

  const esc = (s) => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

  function setLabel() {
    const opt = select.options[select.selectedIndex];
    const text = opt ? opt.textContent : '';
    const empty = !opt || opt.value === '' || text.startsWith('—');
    labelEl.textContent = text || '—';
    labelEl.classList.toggle('placeholder', empty);
  }

  function rebuildOptions() {
    dropdown.innerHTML = '';
    const groupName = 'cs-' + (select.id || select.name || Math.random().toString(36).slice(2, 8));
    Array.from(select.options).forEach((opt, i) => {
      const li = document.createElement('li');
      li.className = 'custom-select-option';
      li.setAttribute('role', 'option');
      li.dataset.value = opt.value;
      const selected = opt.value === select.value;
      if (selected) li.setAttribute('aria-selected', 'true');
      const optId = `${groupName}-${i}`;
      const input = document.createElement('input');
      input.type = 'radio';
      input.name = groupName;
      input.id = optId;
      input.value = opt.value;
      if (selected) input.checked = true;
      const label = document.createElement('label');
      label.setAttribute('for', optId);
      label.textContent = opt.textContent;
      li.appendChild(input);
      li.appendChild(label);
      li.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (select.value !== opt.value) {
          select.value = opt.value;
          select.dispatchEvent(new Event('change', { bubbles: true }));
        }
        setLabel();
        closeDropdown();
      });
      dropdown.appendChild(li);
    });
  }

  function openDropdown() {
    rebuildOptions();
    if (!dropdown.children.length) return;
    wrap.classList.add('open');
    dropdown.style.display = '';
    // Close other open custom selects
    document.querySelectorAll('.custom-select.open').forEach(el => {
      if (el !== wrap) el.dispatchEvent(new CustomEvent('cs:close'));
    });
  }
  function closeDropdown() {
    wrap.classList.remove('open');
    dropdown.style.display = 'none';
  }
  wrap.addEventListener('cs:close', closeDropdown);

  trigger.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (select.disabled) return;
    wrap.classList.contains('open') ? closeDropdown() : openDropdown();
  });
  trigger.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); trigger.click(); }
    if (e.key === 'Escape') closeDropdown();
  });

  // Click outside — close
  document.addEventListener('click', (e) => {
    if (!wrap.contains(e.target)) closeDropdown();
  });

  // Mirror disabled state to trigger
  const syncDisabled = () => { trigger.disabled = select.disabled; };

  // Mirror select's own display (hide wrapper if existing code hides the select)
  const syncVisibility = () => {
    const d = select.style.display;
    wrap.style.display = (d === 'none') ? 'none' : '';
  };

  // External value changes (select.value = X programmatically) — refresh label
  select.addEventListener('change', () => { setLabel(); });

  // Observe options, style (display), disabled attr
  new MutationObserver(() => {
    syncDisabled();
    syncVisibility();
    setLabel();
  }).observe(select, {
    attributes: true,
    attributeFilter: ['style', 'disabled'],
    childList: true,
    subtree: true,
  });

  syncDisabled();
  syncVisibility();
  setLabel();
}

function enhanceAllSelects(root) {
  (root || document).querySelectorAll('select').forEach(enhanceSelect);
}

enhanceAllSelects();

// Catch dynamically inserted selects (+Add Field rows, etc.)
new MutationObserver((muts) => {
  muts.forEach(m => m.addedNodes.forEach(n => {
    if (n.nodeType !== 1) return;
    if (n.tagName === 'SELECT') enhanceSelect(n);
    if (n.querySelectorAll) n.querySelectorAll('select').forEach(enhanceSelect);
  }));
}).observe(document.body, { childList: true, subtree: true });

// ═══════════════════════════════════════════════════════════════════════════
// Persist tab + form values across F5 — localStorage-backed.
// Dev convenience only; cookies/headers persisted here are in clear text.
// ═══════════════════════════════════════════════════════════════════════════
const STATE_KEY = 'scraper-tester:state:v1';
const DYNAMIC_CONTAINERS = {
  'headers-list':     'kv',
  'cookies-list':     'cookie',
  'extract-fields':   'extract',
  'b-headers-list':   'kv',
  'b-cookies-list':   'cookie',
  'b-extract-fields': 'extract',
  'c-headers-list':   'kv',
  'c-cookies-list':   'cookie',
  'c-extract-fields': 'extract',
};
const _KV_PH = ['Header name', 'Value'];
const _COOKIE_PH = ['name', 'value', 'domain', 'path'];
let _stateSaveTimer = null;

function captureState() {
  const state = {
    activeTab: document.querySelector('.tab-btn.active')?.dataset.tab || null,
    inputs: {},
    dynamicRows: {},
  };
  document.querySelectorAll('input[id], select[id], textarea[id]').forEach(el => {
    if (!el.id) return;
    // Skip selects populated dynamically from APIs — restoring stale values
    // would conflict with the refresh fetches on page load.
    if (el.id.endsWith('-proxy-pool-select') || el.id.endsWith('-geo-country')) return;
    // Skip ephemeral inputs
    if (el.id === 'mcp-tool-schema' || el.id === 'mcp-tool-name' || el.id === 'mcp-tool-args') return;
    if (el.id === 'j-job-id') return;
    // Skip inputs nested inside .dynamic-row — those get captured separately.
    if (el.closest('.dynamic-row')) return;
    if (el.type === 'checkbox') state.inputs[el.id] = el.checked;
    else state.inputs[el.id] = el.value;
  });
  Object.keys(DYNAMIC_CONTAINERS).forEach(cid => {
    const container = document.getElementById(cid);
    if (!container) return;
    const rows = Array.from(container.querySelectorAll('.dynamic-row')).map(row => ({
      texts: Array.from(row.querySelectorAll('input[type="text"]')).map(i => i.value),
      select: row.querySelector('select')?.value,
      checkbox: row.querySelector('input[type="checkbox"]')?.checked,
    }));
    if (rows.length) state.dynamicRows[cid] = rows;
  });
  return state;
}

let _stateSaveWarned = false;
function persistState() {
  try {
    localStorage.setItem(STATE_KEY, JSON.stringify(captureState()));
  } catch (e) {
    // QuotaExceededError is the common case — too much state (big HTML in
    // extract-field placeholders, thousands of dynamic rows). Warn ONCE so
    // we don't spam the console every keystroke.
    if (!_stateSaveWarned) {
      _stateSaveWarned = true;
      console.warn('scraper-tester: cannot persist UI state to localStorage:', e?.message || e);
    }
  }
}
function schedulePersist() {
  clearTimeout(_stateSaveTimer);
  _stateSaveTimer = setTimeout(persistState, 300);
}

function restoreState() {
  let state;
  try { state = JSON.parse(localStorage.getItem(STATE_KEY) || 'null'); } catch { return; }
  if (!state) return;

  Object.entries(state.inputs || {}).forEach(([id, value]) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (el.type === 'checkbox') el.checked = !!value;
    else el.value = value;
  });

  Object.entries(state.dynamicRows || {}).forEach(([cid, rows]) => {
    const type = DYNAMIC_CONTAINERS[cid];
    const container = document.getElementById(cid);
    if (!type || !container) return;
    container.innerHTML = '';
    rows.forEach(row => {
      if (type === 'extract') {
        addExtractField(cid);
      } else {
        addDynamicRow(cid, type === 'kv' ? _KV_PH : _COOKIE_PH);
      }
      const last = container.querySelector('.dynamic-row:last-child');
      if (!last) return;
      const texts = last.querySelectorAll('input[type="text"]');
      (row.texts || []).forEach((v, i) => { if (texts[i]) texts[i].value = v; });
      const sel = last.querySelector('select');
      if (sel && row.select !== undefined) sel.value = row.select;
      const cb = last.querySelector('input[type="checkbox"]');
      if (cb && row.checkbox !== undefined) cb.checked = !!row.checkbox;
    });
  });

  // Fire change on selects / toggles so dependent refreshers re-run
  // (proxy pool visibility, Enable-scraping section, custom-select labels).
  ['s-proxy-type','b-proxy-type','c-proxy-type','cp-proxy-type',
   's-extract-type','b-extract-type','c-extract-type',
   'c-enable-scraping','mcp-target']
    .forEach(id => {
      const el = document.getElementById(id);
      if (el) el.dispatchEvent(new Event('change', { bubbles: true }));
    });
  // Dispatch change on all enhanced selects so their custom-select label syncs.
  document.querySelectorAll('select[id]').forEach(el => {
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });

  if (state.activeTab) {
    const btn = document.querySelector(`.tab-btn[data-tab="${state.activeTab}"]`);
    if (btn) btn.click();
  }
}

document.addEventListener('input', schedulePersist, true);
document.addEventListener('change', schedulePersist, true);
document.addEventListener('click', (e) => {
  if (e.target.closest('.tab-btn, .btn-remove, button[id^="btnAdd"], button[id^="btnClearHeaders"], button[id^="btnApplyPreset"]')) {
    schedulePersist();
  }
}, true);
window.addEventListener('beforeunload', persistState);

restoreState();
