FROM mcr.microsoft.com/playwright/python:v1.57.0-jammy

ENV PATH=/app/.venv/bin:$PATH \
    \
    # Python
    PYTHONPATH=/app:$PYTHONPATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    \
    # Pip
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    \
    # Ruff
    RUFF_CACHE_DIR=/tmp

WORKDIR /app

COPY pyproject.toml requirements.txt requirements-dev.txt ./

RUN pip install -r requirements.txt

# Fetch the Camoufox browser binary + GeoIP data into the image at build time.
# Must run after camoufox is installed (above) and before COPY src so that a
# source-only change does not invalidate this ~1.3 GB download layer.
RUN python -m camoufox fetch

# Install a real Google Chrome (stable) alongside the bundled Chromium so the
# scraper can drive it via channel="chrome" (CHROME_CHANNEL=chrome) for better
# anti-bot fidelity — real branding/codecs, a populated navigator.plugins, and a
# newer engine than the pinned Chromium. No-op at runtime unless CHROME_CHANNEL
# is set. Kept before COPY src so a source change doesn't re-pull it.
RUN playwright install chrome

# Xvfb (virtual X display) so the browser can run *headful* on a headless
# server. Launch mode is per-request, so the entrypoint starts Xvfb
# unconditionally — one idle process per container, independent of HEADLESS.
RUN apt-get update && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/*

# Test/dev deps (pytest, fakeredis, ...). The production CMD never imports them;
# they live in the image so `docker compose run --rm web-scraper pytest` works on
# hosts without a local Python 3.12 toolchain (the K12 dev convention). Drop this
# layer behind a build target if a slim production image is ever needed.
RUN pip install -r requirements-dev.txt

COPY src /app/src
COPY scripts /app/scripts
RUN chmod +x /app/scripts/docker-entrypoint.sh

ENV HOST=0.0.0.0
ENV PORT=8000

# Entrypoint starts Xvfb (headful support is per-request) then execs the CMD / compose command.
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
