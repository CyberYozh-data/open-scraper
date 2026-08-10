# Per-request launch mode (headless / headful) — design

- **Date:** 2026-07-13
- **Status:** Approved (design), pending implementation plan
- **Repo:** open-scraper-clone
- **Reworks:** PR #56 (`feat/headful-xvfb`) — keeps its Xvfb/Dockerfile/entrypoint infra, changes the default + adds per-request control
- **Out of scope:** fingerprint leak #1 (canvas/fonts OS-mismatch, honest-Linux vs Camoufox) — separate spec

## Problem

PR #56 made headful a **global** mode via `HEADLESS=false` + Xvfb, deployed on K12. Headful's measured value is marginal (~+9% on some targets; fixes the `getBattery` headless tell; did **not** fix Google — that was proxy geo, fixed in #59) and it costs Xvfb + resources on every scrape. We want:

1. **Flexibility:** headless/headful selectable **per request** (API) and in the tester UI.
2. **Lean default:** headless by default; headful is opt-in.
3. **No resource regression:** must not multiply warm-browser memory (K12 has OOM history, 2026-06-13); must preserve the existing "warm while scraping, shut down when idle" behavior.

## Decisions (from brainstorm)

- Primary goal = **API+UI flexibility** (per-request), not target-specific block reduction.
- Default launch mode = **headless**; headful is **opt-in** per request. K12 flips from headful-global to headless-default.
- "Warm browser" (warmup/session) and "launch mode" (headless/headful) are **orthogonal** — keep them as **separate** controls in the UI, do not conflate.
- Pooling = **Variant A**: pool only the default mode; run the non-default mode **ephemerally** (mirror the existing camoufox short-circuit). Zero warm-memory multiplication.

## Design

### 1. API — `src/schemas.py`

Add to `ScrapeRequest`:

```python
headless: bool | None = Field(
    default=None,
    description=(
        "Launch mode override. None (default) uses the server default "
        "(settings.headless). true = headless; false = headful (needs an X "
        "display; the container provides Xvfb). Orthogonal to `warmup`."
    ),
)
```

Tri-state, mirroring the existing `block_assets: bool | None` pattern. Additive — existing callers are unaffected. Precedence: **request `headless` (if not None) > `settings.headless`**.

### 2. Default + config

- `src/settings.py`: `headless` field default `True` (headless).
- K12 `.env`: set `HEADLESS=true` (remove the current `HEADLESS=false`). Default becomes headless; headful is opt-in.
- `.env.example`: document that `HEADLESS` is now only the **default** launch mode (per-request `headless` overrides), and that Xvfb runs regardless so per-request headful works.

### 3. Runner selection — Variant A (`src/queue/tasks.py`, `worker.py`)

The per-worker registry `state.runners` currently holds **one long-lived runner per engine**; `runner.start()` is idempotent (launches the Browser once, reuses it; each `fetch()` builds/tears down only context+page). camoufox already **bypasses** the registry — `_get_runner` returns a fresh per-request `CamoufoxRunner` (launch-in-fetch / close-after, `is_started() == False`, zero idle RAM).

Extend `_get_runner(context, engine, headless)`, where `headless` is the **resolved effective mode** (request value if not None, else `settings.headless`):

1. **camoufox** (or any non-pooled engine) → ephemeral, as today.
2. **default mode** (`headless == settings.headless`) → the existing **engine-keyed warm runner** from `state.runners` (unchanged warm/idle path).
3. **non-default mode** (`headless != settings.headless`, e.g. chromium headful when default is headless) → a fresh **ephemeral `PlaywrightRunner(headless=headless, ...)`** with the same contract as `CamoufoxRunner` (`is_started() == False`, no-op start/stop, launch-in-fetch, close-after). **Never stored in `state.runners`.**

Consequences:

- Registry key stays **engine only** — no `(engine, headless)` tuple, so no ripple into `last_activity` / `pages_since_launch` / the idle-window predicate (`_lifecycle_tick` keeps its `engine == 'chromium'` check working).
- Thread per-request `headless` into the resolution point; `_new_runner` keeps using `settings.headless` for the pooled default runner.
- Warm-memory multiplication = **0**. Non-default mode pays ~1–2 s cold start per request (acceptable: headful is the opt-in minority; `--max-async-tasks 1` means two modes are never warm-needed at once).

### 4. Resource model (constraint, not just consequence)

Facts (measured):

| Knob | Base | K12 |
|---|---|---|
| worker `mem_limit` | 4g | 6g |
| `shm_size` (fixed, does not scale with WORKERS) | 1g | 1g |
| `WORKERS` (procs/container) | 2 | 4 |
| per-worker budget `(mem − 1g) / WORKERS` | ~1.5 GB | **~1.25 GB** |
| global concurrency (`--max-async-tasks 1` × WORKERS × replicas) | 2 | 4 |
| warm chromium/real-Chrome RSS (permanent) | ~400–500 MB | same (+10–20%) |
| idle window: chromium / secondary | 600s / 120s | 600s / 120s |
| `BROWSER_MAX_PAGES` / page timeout | 100 / 120s | 100 / 120s |

- **Memory is the only bound** (no CPU/pids/ulimit caps). The warm-browser count is the lever.
- A naive `(engine, headless)` re-key would keep **two** warm chromium browsers (~0.9–1.0 GB) + a transient camoufox (~0.8 GB) ≈ 1.7–1.8 GB > the 1.25 GB K12 per-worker budget → **cgroup OOM-kill**. Variant A avoids this by keeping **at most one warm browser per engine**.
- Idle-shutdown is **preserved unchanged** for the pooled default runner (600s chromium). Ephemeral non-default runners carry no idle timer — closed at the end of each `fetch()`.

### 5. Xvfb — `scripts/docker-entrypoint.sh`, `Dockerfile` (mandatory)

Today the entrypoint starts `Xvfb :99` **only when `HEADLESS=false`**. With a headless default, a per-request headful scrape has no `DISPLAY` and the launch fails. Fix: **start Xvfb unconditionally** (+ `export DISPLAY=:99`), decoupled from `HEADLESS`. Cost: ~40 MB flat, **one per container**, does not multiply with browser count. Headless Chromium ignores `DISPLAY`, so always-on Xvfb is safe for the default path.

### 6. Tester UI — `scraper-tester/public/{index.html,app.js}`

- Add a **"Launch mode"** control to the Scrape Page form: `Auto (server default) / Headless / Headful`. `Auto` → omit the field; `Headless` → `headless: true`; `Headful` → `headless: false`.
- Place it as its **own** control, visually separate from the existing **warmup** control (orthogonality constraint) — e.g. a "Browser" group distinct from the session/warmup group.
- Persist the selection with the tester's existing form-state persistence.

### 7. Error handling

If headful is requested but no X display is available (Xvfb not running / `DISPLAY` unset — e.g. a minimal deployment), Playwright's launch throws. Catch it and surface a clear error ("headful requested but no display available — enable Xvfb / set DISPLAY") instead of an opaque crash. On K12 (Xvfb always on) this never fires.

## Testing

- **Unit:** `ScrapeRequest` accepts `headless` tri-state; effective-headless precedence (request > env); `_get_runner` returns the warm engine-keyed runner for the default mode and a fresh non-stored ephemeral runner for the non-default mode; camoufox stays ephemeral.
- **Lifecycle:** pooled default runner still idle-shuts at 600s; ephemeral runner is closed after its fetch and never lands in `state.runners`.
- **Entrypoint:** Xvfb starts and `DISPLAY` is set even with `HEADLESS=true` (smoke).
- **Tester (manual):** the Launch mode control sends the field, `Auto` omits it, and it is visually/semantically distinct from warmup.

## Files touched

- `src/schemas.py` — `headless` field
- `src/settings.py` — default `True`
- `src/queue/tasks.py` — `_get_runner` ephemeral non-default branch; thread `headless`
- `src/queue/worker.py` — `_new_runner` unchanged default; resolution wiring
- `scripts/docker-entrypoint.sh`, `Dockerfile` — unconditional Xvfb
- `.env.example` (+ K12 `.env`, untracked) — `HEADLESS` semantics/default
- `scraper-tester/public/index.html`, `app.js` — Launch mode control
- tests under `tests/`

## Rollout / deploy notes

- Reworks branch `feat/headful-xvfb` (PR #56): keep Xvfb infra, flip default, add per-request + UI.
- K12: set `.env` `HEADLESS=true`; **rebuild the scraper-worker image** (separate image; worker actually launches browsers) and restart.
- Xvfb now always-on — verify one idle `Xvfb :99` per container.

## Open questions

- None blocking. (Variant B — pool both modes with an LRU cap of 1 per engine — is documented as a fallback only if headless/headful traffic turns out ~50/50; not chosen.)
