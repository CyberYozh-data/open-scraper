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

# Test/dev deps (pytest, fakeredis, ...). The production CMD never imports them;
# they live in the image so `docker compose run --rm web-scraper pytest` works on
# hosts without a local Python 3.12 toolchain (the K12 dev convention). Drop this
# layer behind a build target if a slim production image is ever needed.
RUN pip install -r requirements-dev.txt

COPY src /app/src
COPY scripts /app/scripts

ENV HOST=0.0.0.0
ENV PORT=8000

CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
